"""Shared, version-aware cache for deterministic scout-report requests."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import Event, RLock
from typing import Any, Callable

from app.agent.artifact_cache import ArtifactVersion
from app.config import get_settings
from app.services.analysis_pipeline import OUTPUT_ROOT


SCOUT_ARTIFACTS = (
    "team_action_tendencies.jsonl",
    "team_opening_sequences.jsonl",
    "team_combo_performance.jsonl",
    "player_hero_pools.jsonl",
    "team_recent_trends.jsonl",
    "pick_synergy_stats.jsonl",
    "counter_pick_stats.jsonl",
)


def _path_version(path: Path) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    return ArtifactVersion(
        modified_ns=stat.st_mtime_ns,
        size=stat.st_size,
        inode=stat.st_ino,
    ).token


def scout_report_cache_key(
    *,
    league_id: str,
    blue_team_id: str,
    red_team_id: str,
    language: str,
) -> tuple[str, ...]:
    """Key reports by ordered matchup and every local evidence source version."""
    settings = get_settings()
    database_url = settings.database_url
    database_path = (
        Path(database_url.removeprefix("sqlite:///"))
        if database_url.startswith("sqlite:///")
        else Path("")
    )
    versions = [
        f"database:{_path_version(database_path)}",
        *(
            f"{filename}:{_path_version(OUTPUT_ROOT / league_id / filename)}"
            for filename in SCOUT_ARTIFACTS
        ),
    ]
    return (league_id, blue_team_id, red_team_id, language, *versions)


class ScoutReportCache:
    """Bounded process cache that also collapses simultaneous report requests."""

    def __init__(self, *, max_entries: int = 32) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
        self._in_flight: dict[tuple[str, ...], Event] = {}
        self._lock = RLock()

    def get_or_generate(
        self,
        key: tuple[str, ...],
        generate: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        while True:
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None:
                    self._entries.move_to_end(key)
                    return deepcopy(cached)
                pending = self._in_flight.get(key)
                if pending is None:
                    pending = Event()
                    self._in_flight[key] = pending
                    is_generator = True
                else:
                    is_generator = False
            if is_generator:
                break
            pending.wait()

        try:
            generated = generate()
        except Exception:
            with self._lock:
                self._in_flight.pop(key, None)
                pending.set()
            raise

        with self._lock:
            self._entries[key] = deepcopy(generated)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            self._in_flight.pop(key, None)
            pending.set()
        return deepcopy(generated)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


scout_report_cache = ScoutReportCache()
