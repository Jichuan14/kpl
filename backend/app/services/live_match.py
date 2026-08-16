"""Read-only, short-lived official KPL match context for the simulator.

This service deliberately never receives a database session and never calls the
normal sync pipeline.  It is safe to use while a match is live because all
state lives only in this process's expiring memory cache.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from math import ceil
from threading import Lock
from time import monotonic
from typing import Any

from app.clients.kpl_api import KplApiClient
from app.config import Settings, get_settings


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _results(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("results") or payload.get("data") or []
    if isinstance(rows, Mapping):
        rows = rows.get("battle_list") or rows.get("results") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


class LiveMatchService:
    """Fetch and derive live Global BP context without persisting anything."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: KplApiClient | Any | None = None,
        cache_seconds: int | None = None,
        manual_refresh_seconds: int | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.client = client or KplApiClient(settings)
        self.cache_seconds = cache_seconds or settings.live_match_cache_seconds
        self.manual_refresh_seconds = (
            manual_refresh_seconds or settings.live_match_manual_refresh_seconds
        )
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._fixture_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._lock = Lock()

    def get_current_fixture(
        self, league_id: str, *, selectable_team_ids: set[str]
    ) -> dict[str, Any] | None:
        """Return the current official fixture, without touching the database."""
        now = monotonic()
        with self._lock:
            cached = self._fixture_cache.get(str(league_id))
            if cached and now - cached[0] < self.cache_seconds:
                return cached[1]

            rows = _results(self.client.get_matches(str(league_id)))
            live = sorted(
                (
                    row
                    for row in rows
                    if _as_int(row.get("status")) == 1
                    and {
                        str((row.get("camp1") or {}).get("team_id") or ""),
                        str((row.get("camp2") or {}).get("team_id") or ""),
                    }.issubset(selectable_team_ids)
                ),
                key=lambda row: str(row.get("start_time") or ""),
            )
            fixture = None
            if live:
                match = live[0]
                camps = [match.get("camp1") or {}, match.get("camp2") or {}]
                fixture = {
                    "match_id": str(match.get("match_id") or ""),
                    "start_time": match.get("start_time"),
                    "timezone": "Asia/Shanghai",
                    "fixture_status": "live",
                    "is_live": True,
                    "teams": [
                        {
                            "team_id": str(camp.get("team_id") or ""),
                            "team_name": camp.get("team_name") or "",
                        }
                        for camp in camps
                    ],
                }
            self._fixture_cache[str(league_id)] = (now, fixture)
            return fixture

    def get_match_state(
        self, league_id: str, team_a_id: str, team_b_id: str, match_id: str
    ) -> dict[str, Any]:
        return self._get_match_state(
            league_id,
            team_a_id,
            team_b_id,
            match_id,
            refresh_after_seconds=self.cache_seconds,
        )

    def refresh_match_state(
        self, league_id: str, team_a_id: str, team_b_id: str, match_id: str
    ) -> dict[str, Any]:
        """Refresh on request, but never more than once a minute per matchup."""
        return self._get_match_state(
            league_id,
            team_a_id,
            team_b_id,
            match_id,
            refresh_after_seconds=self.manual_refresh_seconds,
        )

    def _get_match_state(
        self,
        league_id: str,
        team_a_id: str,
        team_b_id: str,
        match_id: str,
        *,
        refresh_after_seconds: int,
    ) -> dict[str, Any]:
        team_ids = {str(team_a_id), str(team_b_id)}
        if len(team_ids) != 2:
            raise ValueError("Choose two different teams to follow a live match")
        if not str(match_id):
            raise ValueError("A scheduled match is required for live follow")
        key = (str(league_id), str(match_id))
        now = monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < refresh_after_seconds:
                return self._response(cached[1], cache_age=now - cached[0], refreshed=False)

            # Holding this small lock prevents many browser tabs from making the
            # same upstream KPL request at once. No data is written to SQLite.
            state = self._fetch_state(str(league_id), team_ids, str(match_id))
            self._cache[key] = (now, state)
            return self._response(state, cache_age=0, refreshed=True)

    def _response(
        self, state: dict[str, Any], *, cache_age: float, refreshed: bool
    ) -> dict[str, Any]:
        response = dict(state)
        response["official_refresh"] = {
            "performed": refreshed,
            "cache_age_seconds": int(cache_age),
            "manual_refresh_available_in_seconds": max(
                0, self.manual_refresh_seconds - ceil(cache_age)
            ),
        }
        return response

    def _fetch_state(
        self, league_id: str, team_ids: set[str], match_id: str
    ) -> dict[str, Any]:
        matches_payload = self.client.get_matches(league_id)
        candidates = [
            row
            for row in _results(matches_payload)
            if str(row.get("match_id") or "") == match_id
            and {
                str((row.get("camp1") or {}).get("team_id") or ""),
                str((row.get("camp2") or {}).get("team_id") or ""),
            }
            == team_ids
        ]
        live = next((row for row in candidates if _as_int(row.get("status")) == 1), None)
        finished = sorted(
            (row for row in candidates if _as_int(row.get("status")) == 2),
            key=lambda row: str(row.get("start_time") or ""),
            reverse=True,
        )
        match = live or (finished[0] if finished else None)
        if match is None:
            return {
                "league_id": league_id,
                "is_live": False,
                "is_finished": False,
                "match": None,
                "completed_games": [],
                "used_hero_ids_by_team": {team_id: [] for team_id in sorted(team_ids)},
                "current_game": None,
                "current_game_status": "unavailable",
                "hero_selection_locked": False,
                "refreshed_at": datetime.now(UTC).isoformat(),
                "cache_seconds": self.cache_seconds,
            }

        status = _as_int(match.get("status"))
        camp1 = match.get("camp1") or {}
        camp2 = match.get("camp2") or {}
        match_id = str(match.get("match_id") or "")
        battle_rows = _results(self.client.get_match_battles(match_id)) if match_id else []
        completed = sorted(
            (
                row
                for row in battle_rows
                if _as_int(row.get("status")) == 2 or _as_int(row.get("win_camp")) > 0
            ),
            key=lambda row: _as_int(row.get("battle_seq")),
        )
        in_progress = sorted(
            (row for row in battle_rows if _as_int(row.get("status")) == 1),
            key=lambda row: _as_int(row.get("battle_seq")),
        )
        used_by_team = {team_id: [] for team_id in sorted(team_ids)}
        completed_games = []
        fallback_camps = {
            1: str(camp1.get("team_id") or ""),
            2: str(camp2.get("team_id") or ""),
        }
        for battle in completed:
            battle_id = str(battle.get("battle_id") or "")
            detail_payload = self.client.get_battle_detail(battle_id) if battle_id else None
            detail = detail_payload.get("data") if isinstance(detail_payload, Mapping) else None
            detail = detail if isinstance(detail, Mapping) else {}
            camps = {
                1: str((detail.get("camp1") or {}).get("team_id") or fallback_camps[1]),
                2: str((detail.get("camp2") or {}).get("team_id") or fallback_camps[2]),
            }
            picks_by_team = {team_id: [] for team_id in sorted(team_ids)}
            for action in detail.get("bp_list") or []:
                if not isinstance(action, Mapping) or _as_int(action.get("is_ban_or_pick")) != 1:
                    continue
                team_id = camps.get(_as_int(action.get("camp")), "")
                hero_id = _as_int(action.get("hero_id"))
                if team_id in picks_by_team and hero_id > 0:
                    picks_by_team[team_id].append(hero_id)
                    if hero_id not in used_by_team[team_id]:
                        used_by_team[team_id].append(hero_id)
            completed_games.append(
                {
                    "battle_id": battle_id,
                    "game": _as_int(battle.get("battle_seq")),
                    "winning_camp": _as_int(battle.get("win_camp")),
                    "used_hero_ids_by_team": picks_by_team,
                }
            )

        active_battle = in_progress[-1] if in_progress else None
        latest_finished_game = max((_as_int(row.get("battle_seq")) for row in completed), default=0)
        current_game = (
            _as_int(active_battle.get("battle_seq"))
            if active_battle
            else latest_finished_game + 1
        )
        return {
            "league_id": league_id,
            "is_live": status == 1,
            "is_finished": status == 2,
            "match": {
                "match_id": match_id,
                "bo": _as_int(match.get("bo")),
                "status": status,
                "teams": [
                    {
                        "team_id": fallback_camps[1],
                        "team_name": camp1.get("team_name") or "",
                        "score": _as_int(camp1.get("score")),
                    },
                    {
                        "team_id": fallback_camps[2],
                        "team_name": camp2.get("team_name") or "",
                        "score": _as_int(camp2.get("score")),
                    },
                ],
            },
            "completed_games": completed_games,
            "used_hero_ids_by_team": used_by_team,
            "current_game": current_game,
            "current_game_status": "in_progress" if active_battle else "awaiting_start",
            # An active official battle can include its BP phase. The simulator
            # keeps its *local* draft usable during that time; only a finished
            # series locks selection because there is no next game to draft.
            "hero_selection_locked": status == 2,
            "refreshed_at": datetime.now(UTC).isoformat(),
            "cache_seconds": self.cache_seconds,
        }
