from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clients.kpl_api import KplApiClient
from app.config import Settings, get_settings
from app.models import Battle, BattleBp, League, Match
from app.services.bp_stats import recompute_hero_bp_stats

logger = logging.getLogger(__name__)

# Match status used by official API for finished series (same convention as kpl-agent).
FINISHED_MATCH_STATUS = 2


class SyncService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.api = KplApiClient(self.settings)

    def close(self) -> None:
        self.api.close()

    def sync_leagues(self) -> dict[str, Any]:
        payload = self.api.get_leagues()
        if not payload or payload.get("code") != 200:
            raise RuntimeError("Failed to fetch leagues from KPL API")

        inserted = 0
        updated = 0
        for node in payload.get("results") or []:
            league_id = str(node.get("league_id", ""))
            if not league_id:
                continue
            existing = self.db.scalar(select(League).where(League.league_id == league_id))
            fields = {
                "league_name": node.get("league_name") or "",
                "league_type": node.get("league_type_name") or "",
                "year": node.get("year"),
                "season": node.get("season"),
                "status": int(node.get("status") or 0),
                "start_time": node.get("start_time"),
                "end_time": node.get("end_time"),
            }
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                self.db.add(League(league_id=league_id, **fields))
                inserted += 1

        self.db.commit()
        return {"inserted": inserted, "updated": updated}

    def resolve_league_id(self, league_id: str | None) -> str:
        if league_id:
            return league_id

        self.sync_leagues()
        latest = self.db.scalar(
            select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
        )
        if not latest:
            raise RuntimeError("No leagues available after sync")
        return latest.league_id

    def sync_league_bp(
        self,
        league_id: str | None = None,
        match_limit: int | None = None,
        recompute_stats: bool = True,
    ) -> dict[str, Any]:
        """Sync matches → battles → bp_list for a league (BP-focused deep sync)."""
        lid = self.resolve_league_id(league_id)
        match_count = self._sync_matches(lid)
        self._sleep()

        matches = list(
            self.db.scalars(
                select(Match)
                .where(Match.league_id == lid)
                .order_by(Match.start_time.desc())
            )
        )
        finished = [m for m in matches if m.status == FINISHED_MATCH_STATUS]
        if match_limit is not None:
            finished = finished[: max(0, match_limit)]

        battles_synced = 0
        bp_rows = 0
        for match in finished:
            b_count, bp_count = self._sync_match_battles_and_bp(match)
            battles_synced += b_count
            bp_rows += bp_count
            self._sleep()

        stats = None
        if recompute_stats:
            stats = recompute_hero_bp_stats(self.db, lid)

        return {
            "league_id": lid,
            "matches_upserted": match_count,
            "finished_matches_processed": len(finished),
            "battles_upserted": battles_synced,
            "bp_rows_written": bp_rows,
            "hero_stats": stats,
        }

    def _sync_matches(self, league_id: str) -> int:
        payload = self.api.get_matches(league_id)
        if not payload or payload.get("code") != 200:
            raise RuntimeError(f"Failed to fetch matches for league {league_id}")

        count = 0
        for node in payload.get("results") or []:
            match_id = str(node.get("match_id", ""))
            if not match_id:
                continue
            camp1 = node.get("camp1") or {}
            camp2 = node.get("camp2") or {}
            existing = self.db.scalar(select(Match).where(Match.match_id == match_id))
            fields = {
                "league_id": league_id,
                "camp1_team_id": str(camp1.get("team_id") or ""),
                "camp1_team_name": camp1.get("team_name") or "",
                "camp1_score": int(camp1.get("score") or 0),
                "camp2_team_id": str(camp2.get("team_id") or ""),
                "camp2_team_name": camp2.get("team_name") or "",
                "camp2_score": int(camp2.get("score") or 0),
                "bo": int(node.get("bo") or 0),
                "win_camp": int(node.get("win_camp") or 0),
                "status": int(node.get("status") or 0),
                "match_stage": node.get("match_stage_name") or "",
                "start_time": node.get("start_time"),
            }
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
            else:
                self.db.add(Match(match_id=match_id, **fields))
            count += 1

        self.db.commit()
        return count

    def _sync_match_battles_and_bp(self, match: Match) -> tuple[int, int]:
        payload = self.api.get_match_battles(match.match_id)
        if not payload or payload.get("code") != 200:
            logger.warning("No battles for match %s", match.match_id)
            return 0, 0

        results = payload.get("results") or payload.get("data") or []
        if isinstance(results, dict):
            results = results.get("battle_list") or results.get("results") or []

        battle_count = 0
        bp_total = 0
        for node in results:
            battle_id = str(node.get("battle_id") or "")
            if not battle_id:
                continue
            existing = self.db.scalar(select(Battle).where(Battle.battle_id == battle_id))
            fields = {
                "match_id": match.match_id,
                "league_id": match.league_id,
                "battle_seq": int(node.get("battle_seq") or 0),
                "win_camp": int(node.get("win_camp") or 0),
                "game_duration": int(node.get("game_duration") or 0),
                "status": int(node.get("status") or 0),
            }
            if existing:
                for key, value in fields.items():
                    setattr(existing, key, value)
            else:
                self.db.add(Battle(battle_id=battle_id, **fields))
            battle_count += 1
            self.db.flush()

            self._sleep()
            bp_total += self._sync_battle_bp(battle_id, match.league_id)

        self.db.commit()
        return battle_count, bp_total

    def _sync_battle_bp(self, battle_id: str, league_id: str) -> int:
        payload = self.api.get_battle_detail(battle_id)
        if not payload or payload.get("code") != 200:
            logger.warning("Battle detail missing for %s", battle_id)
            return 0

        data = payload.get("data") or {}
        if isinstance(data, list):
            # Unexpected shape; skip
            return 0

        win_camp = int(data.get("win_camp") or 0)
        if win_camp:
            battle = self.db.scalar(select(Battle).where(Battle.battle_id == battle_id))
            if battle and (battle.win_camp or 0) == 0:
                battle.win_camp = win_camp

        bp_list = data.get("bp_list") or []
        self.db.execute(delete(BattleBp).where(BattleBp.battle_id == battle_id))

        written = 0
        for index, node in enumerate(bp_list):
            action_type = int(node.get("is_ban_or_pick", -1))
            if action_type not in (0, 1):
                continue
            self.db.add(
                BattleBp(
                    battle_id=battle_id,
                    league_id=league_id,
                    camp=int(node.get("camp") or 0),
                    action_type=action_type,
                    hero_id=int(node.get("hero_id") or 0),
                    hero_name=node.get("hero_name") or "",
                    hero_icon=node.get("hero_icon") or "",
                    position=int(node.get("position") or 0),
                    bp_order=index + 1,
                )
            )
            written += 1
        return written

    def _sleep(self) -> None:
        delay = self.settings.sync_request_delay
        if delay > 0:
            time.sleep(delay)
