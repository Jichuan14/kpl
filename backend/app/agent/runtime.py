"""Request-wide budgets, clocks, and a single retry owner for the coach graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic, sleep
from typing import Any, Callable

from app.agent.errors import CoachBudgetError

DEFAULT_DEADLINE_SECONDS = 90.0
DEFAULT_FINALIZE_RESERVE_SECONDS = 10.0
MAX_PROVIDER_RETRIES = 1


class Clock:
    """Process-local clock. Tests inject a fake clock; never persist this."""

    def monotonic(self) -> float:
        return monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            sleep(seconds)


class FakeClock(Clock):
    """Deterministic clock that never actually sleeps."""

    def __init__(self, start: float = 0.0):
        self._now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._now += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self._now += seconds


@dataclass
class RequestBudget:
    """One request-wide deadline and attempt counters."""

    request_id: str
    deadline: float
    reserve_seconds: float
    clock: Clock
    cancel_event: Event = field(default_factory=Event)
    provider_calls: int = 0
    provider_retries: int = 0
    tool_calls: int = 0
    tool_rounds: int = 0
    repairs: int = 0
    max_provider_retries: int = MAX_PROVIDER_RETRIES
    max_repairs: int = 1
    _lock: Lock = field(default_factory=Lock)

    def remaining(self) -> float:
        return self.deadline - self.clock.monotonic()

    def remaining_for_work(self) -> float:
        return self.remaining() - self.reserve_seconds

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()

    def allow_provider_call(self, *, reserve: bool = False) -> bool:
        if self.cancelled():
            return False
        remaining = self.remaining() if reserve else self.remaining_for_work()
        return remaining > 0.25

    def provider_timeout(self, *, reserve: bool = False) -> float:
        remaining = self.remaining() if reserve else self.remaining_for_work()
        return max(0.5, remaining)

    def record_provider_call(self) -> None:
        with self._lock:
            self.provider_calls += 1

    def record_provider_retry(self) -> None:
        with self._lock:
            self.provider_retries += 1

    def can_retry_provider(self) -> bool:
        if self.cancelled() or self.remaining_for_work() <= 1:
            return False
        return self.provider_retries < self.max_provider_retries

    def record_tool_call(self) -> None:
        with self._lock:
            self.tool_calls += 1

    def record_tool_round(self) -> None:
        with self._lock:
            self.tool_rounds += 1

    def can_repair(self) -> bool:
        return self.repairs < self.max_repairs and self.allow_provider_call()

    def record_repair(self) -> None:
        with self._lock:
            self.repairs += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider_calls": self.provider_calls,
            "provider_retries": self.provider_retries,
            "tool_calls": self.tool_calls,
            "tool_rounds": self.tool_rounds,
            "repairs": self.repairs,
            "remaining_seconds": round(self.remaining(), 3),
        }


def create_budget(
    request_id: str,
    *,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    reserve_seconds: float = DEFAULT_FINALIZE_RESERVE_SECONDS,
    clock: Clock | None = None,
    cancel_event: Event | None = None,
    now: float | None = None,
) -> RequestBudget:
    clock = clock or Clock()
    started = now if now is not None else clock.monotonic()
    return RequestBudget(
        request_id=request_id,
        deadline=started + deadline_seconds,
        reserve_seconds=reserve_seconds,
        clock=clock,
        cancel_event=cancel_event or Event(),
    )


def raise_if_exhausted(budget: RequestBudget | None, *, action: str = "continue") -> None:
    if budget is None:
        return
    if budget.cancelled():
        raise CoachBudgetError("The Draft Coach request was cancelled.")
    if not budget.allow_provider_call():
        raise CoachBudgetError(f"The Draft Coach ran out of time before it could {action}.")


def retry_wait_seconds(
    requested: float,
    budget: RequestBudget | None,
    *,
    minimum: float = 0.0,
) -> float | None:
    """Return a wait that still leaves finalize reserve, or None to stop retrying."""
    wait = max(requested, minimum)
    if budget is None:
        return wait
    if not budget.can_retry_provider():
        return None
    if wait >= budget.remaining_for_work():
        return None
    return wait


def run_with_optional_clock_sleep(
    seconds: float,
    budget: RequestBudget | None,
    sleeper: Callable[[float], None] | None = None,
) -> None:
    if seconds <= 0:
        return
    if budget is not None:
        budget.clock.sleep(seconds)
        return
    (sleeper or sleep)(seconds)
