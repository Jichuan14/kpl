from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class KplApiClient:
    """Thin client for official KPL open APIs (same endpoints as kpl-agent)."""

    def __init__(self, settings: Settings):
        self.comp_base_url = settings.comp_base_url.rstrip("/")
        self.tga_base_url = settings.tga_base_url.rstrip("/")
        self._client = httpx.Client(timeout=20.0)

    def close(self) -> None:
        self._client.close()

    def get_leagues(self) -> dict[str, Any] | None:
        return self._get(f"{self.comp_base_url}/leaguesite/leagues/open")

    def get_matches(self, league_id: str) -> dict[str, Any] | None:
        return self._get(
            f"{self.comp_base_url}/leaguesite/matches/open",
            params={"league_id": league_id},
        )

    def get_match_battles(self, match_id: str) -> dict[str, Any] | None:
        return self._get(
            f"{self.comp_base_url}/leaguesite/match/battles/open",
            params={"match_id": match_id},
        )

    def get_battle_detail(self, battle_id: str) -> dict[str, Any] | None:
        return self._get(
            f"{self.comp_base_url}/leaguesite/battle/open",
            params={"battle_id": battle_id},
        )

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            logger.debug("KPL GET %s params=%s", url, params)
            response = self._client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.exception("KPL API request failed: %s", url)
            return None
