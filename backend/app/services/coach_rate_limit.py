"""Process-local abuse controls and privacy-safe metrics for Draft Coach."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    code: str | None = None
    retry_after_seconds: int = 0


class CoachRateLimiter:
    """Sliding-window limiter for a single FastAPI process.

    The limiter deliberately retains only request timestamps in memory. It does
    not persist questions or IP addresses, and its counters reset on restart.
    """

    def __init__(
        self,
        *,
        per_ip_per_minute: int,
        per_ip_per_day: int,
        server_per_minute: int,
        server_per_day: int,
        max_active_per_ip: int,
        max_active_server: int,
    ) -> None:
        self.per_ip_per_minute = per_ip_per_minute
        self.per_ip_per_day = per_ip_per_day
        self.server_per_minute = server_per_minute
        self.server_per_day = server_per_day
        self.max_active_per_ip = max_active_per_ip
        self.max_active_server = max_active_server
        self._ip_requests: dict[str, deque[float]] = defaultdict(deque)
        self._server_requests: deque[float] = deque()
        self._active_by_ip: Counter[str] = Counter()
        self._active_server = 0
        self._accepted = 0
        self._blocked = Counter()
        self._lock = Lock()

    @staticmethod
    def _trim(values: deque[float], now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while values and values[0] <= cutoff:
            values.popleft()

    @staticmethod
    def _retry_after(values: deque[float], now: float, window_seconds: int) -> int:
        if not values:
            return 1
        return max(1, int(values[0] + window_seconds - now) + 1)

    def acquire(self, client_key: str) -> LimitDecision:
        now = monotonic()
        with self._lock:
            self._trim(self._server_requests, now, 86_400)
            ip_requests = self._ip_requests[client_key]
            self._trim(ip_requests, now, 86_400)

            if self._active_server >= self.max_active_server:
                return self._block("server_concurrency", 1)
            if self._active_by_ip[client_key] >= self.max_active_per_ip:
                return self._block("ip_concurrency", 1)
            if len(self._server_requests) >= self.server_per_day:
                return self._block(
                    "server_daily", self._retry_after(self._server_requests, now, 86_400)
                )
            server_minute = sum(timestamp > now - 60 for timestamp in self._server_requests)
            if server_minute >= self.server_per_minute:
                minute_values = deque(timestamp for timestamp in self._server_requests if timestamp > now - 60)
                return self._block(
                    "server_minute", self._retry_after(minute_values, now, 60)
                )
            if len(ip_requests) >= self.per_ip_per_day:
                return self._block("ip_daily", self._retry_after(ip_requests, now, 86_400))
            ip_minute_values = deque(timestamp for timestamp in ip_requests if timestamp > now - 60)
            if len(ip_minute_values) >= self.per_ip_per_minute:
                return self._block(
                    "ip_minute", self._retry_after(ip_minute_values, now, 60)
                )

            self._server_requests.append(now)
            ip_requests.append(now)
            self._active_server += 1
            self._active_by_ip[client_key] += 1
            self._accepted += 1
            return LimitDecision(allowed=True)

    def _block(self, code: str, retry_after_seconds: int) -> LimitDecision:
        self._blocked[code] += 1
        return LimitDecision(False, code=code, retry_after_seconds=retry_after_seconds)

    def release(self, client_key: str) -> None:
        with self._lock:
            if self._active_by_ip[client_key] > 0:
                self._active_by_ip[client_key] -= 1
                if self._active_by_ip[client_key] == 0:
                    del self._active_by_ip[client_key]
            self._active_server = max(0, self._active_server - 1)

    def update_limits(
        self,
        *,
        per_ip_per_minute: int,
        per_ip_per_day: int,
        server_per_minute: int,
        server_per_day: int,
        max_active_per_ip: int,
        max_active_server: int,
    ) -> None:
        """Apply new limits without retaining any additional visitor data."""
        with self._lock:
            self.per_ip_per_minute = per_ip_per_minute
            self.per_ip_per_day = per_ip_per_day
            self.server_per_minute = server_per_minute
            self.server_per_day = server_per_day
            self.max_active_per_ip = max_active_per_ip
            self.max_active_server = max_active_server

    def usage(self) -> dict[str, object]:
        now = monotonic()
        with self._lock:
            self._trim(self._server_requests, now, 86_400)
            minute_count = sum(timestamp > now - 60 for timestamp in self._server_requests)
            return {
                "active_requests": self._active_server,
                "accepted_since_start": self._accepted,
                "blocked_since_start": sum(self._blocked.values()),
                "blocked_by_rule": dict(self._blocked),
                "server": {
                    "last_minute": minute_count,
                    "per_minute_limit": self.server_per_minute,
                    "last_24_hours": len(self._server_requests),
                    "per_24_hours_limit": self.server_per_day,
                    "max_active_requests": self.max_active_server,
                },
                "per_ip": {
                    "per_minute_limit": self.per_ip_per_minute,
                    "per_24_hours_limit": self.per_ip_per_day,
                    "max_active_requests": self.max_active_per_ip,
                },
                "storage": "process_memory",
            }
