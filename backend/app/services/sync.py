from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clients.kpl_api import KplApiClient
from app.config import Settings, get_settings
from app.models import (
    Battle,
    BattleBp,
    BattlePlayer,
    Hero,
    League,
    Match,
    Player,
    Team,
)
from app.services.bp_stats import recompute_hero_bp_stats
from app.services.camp_mapping import (
    compute_camp_flip,
    player_display_name,
    to_match_camp,
)

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
        incremental: bool = True,
    ) -> dict[str, Any]:
        """Sync one league.

        Incremental syncs always refresh the lightweight match catalog, but
        only download battle and BP details for a finished match that has no
        complete local battle data yet. This makes the normal scheduled path scale
        with new matches rather than with the whole season.  Passing
        ``incremental=False`` keeps the original full repair/backfill path.
        """
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

        if incremental:
            finished_to_sync = [
                match
                for match in finished
                if not self._match_has_complete_battle_data(match.match_id)
            ]
        else:
            finished_to_sync = finished

        battles_synced = 0
        bp_rows = 0
        battle_player_rows = 0
        team_ids: set[str] = set()
        player_keys: set[tuple[str, str]] = set()
        detail_errors = 0
        for match in finished_to_sync:
            result = self._sync_match_battles_and_bp(match)
            battles_synced += result["battles"]
            bp_rows += result["bp_rows"]
            battle_player_rows += result["battle_player_rows"]
            team_ids.update(result["team_ids"])
            player_keys.update(result["player_keys"])
            detail_errors += result["detail_errors"]
            self._sleep()

        heroes_upserted = self._refresh_heroes_for_league(lid)
        stats = None
        if recompute_stats and finished_to_sync:
            stats = recompute_hero_bp_stats(self.db, lid)

        return {
            "league_id": lid,
            "incremental": incremental,
            "matches_upserted": match_count,
            "finished_matches_found": len(finished),
            "finished_matches_processed": len(finished_to_sync),
            "finished_matches_skipped": len(finished) - len(finished_to_sync),
            "data_changed": bool(finished_to_sync),
            "battles_upserted": battles_synced,
            "bp_rows_written": bp_rows,
            "battle_player_rows_written": battle_player_rows,
            "teams_seen": len(team_ids),
            "players_seen": len(player_keys),
            "heroes_upserted": heroes_upserted,
            "battle_detail_errors": detail_errors,
            "hero_stats": stats,
        }

    def _match_has_complete_battle_data(self, match_id: str) -> bool:
        """Whether every locally known game has persisted BP detail.

        A failed battle-detail request creates a battle row before the detail
        is available. Treat that as incomplete so the *same new match* is
        retried on the next run instead of silently publishing partial data.
        """
        battle_ids = list(
            self.db.scalars(select(Battle.battle_id).where(Battle.match_id == match_id))
        )
        if not battle_ids:
            return False
        for battle_id in battle_ids:
            if self.db.scalar(
                select(BattleBp.id).where(BattleBp.battle_id == battle_id).limit(1)
            ) is None:
                return False
        return True

    def _sync_matches(self, league_id: str) -> int:
        payload = self.api.get_matches(league_id)
        if not payload or payload.get("code") != 200:
            raise RuntimeError(f"Failed to fetch matches for league {league_id}")

        count = 0
        teams: dict[str, tuple[str, str]] = {}
        for node in payload.get("results") or []:
            match_id = str(node.get("match_id", ""))
            if not match_id:
                continue
            camp1 = node.get("camp1") or {}
            camp2 = node.get("camp2") or {}
            for camp in (camp1, camp2):
                team_id = str(camp.get("team_id") or "")
                if team_id:
                    teams[team_id] = (
                        camp.get("team_name") or "",
                        camp.get("team_icon") or "",
                    )
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

        for team_id, (team_name, team_icon) in teams.items():
            self._upsert_team(team_id, team_name, team_icon)
        self.db.commit()
        return count

    def _sync_match_battles_and_bp(self, match: Match) -> dict[str, Any]:
        payload = self.api.get_match_battles(match.match_id)
        if not payload or payload.get("code") != 200:
            logger.warning("No battles for match %s", match.match_id)
            return {
                "battles": 0,
                "bp_rows": 0,
                "battle_player_rows": 0,
                "team_ids": set(),
                "player_keys": set(),
                "detail_errors": 1,
            }

        results = payload.get("results") or payload.get("data") or []
        if isinstance(results, dict):
            results = results.get("battle_list") or results.get("results") or []

        battle_count = 0
        bp_total = 0
        battle_player_total = 0
        team_ids: set[str] = set()
        player_keys: set[tuple[str, str]] = set()
        detail_errors = 0
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
            payload = self.api.get_battle_detail(battle_id)
            if not payload or payload.get("code") != 200:
                logger.warning("Battle detail missing for %s", battle_id)
                detail_errors += 1
                continue
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                logger.warning("Unexpected battle detail shape for %s", battle_id)
                detail_errors += 1
                continue

            result = self._persist_battle_detail(
                battle=existing or self.db.scalar(
                    select(Battle).where(Battle.battle_id == battle_id)
                ),
                match=match,
                data=data,
            )
            bp_total += result["bp_rows"]
            battle_player_total += result["battle_player_rows"]
            team_ids.update(result["team_ids"])
            player_keys.update(result["player_keys"])
            self.db.flush()

        self.db.commit()
        return {
            "battles": battle_count,
            "bp_rows": bp_total,
            "battle_player_rows": battle_player_total,
            "team_ids": team_ids,
            "player_keys": player_keys,
            "detail_errors": detail_errors,
        }

    def _persist_battle_detail(
        self,
        *,
        battle: Battle | None,
        match: Match,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        if battle is None:
            raise RuntimeError("Battle row missing before detail persistence")

        battle_id = battle.battle_id
        battle.win_camp = int(data.get("win_camp") or battle.win_camp or 0)
        battle.battle_seq = int(
            data.get("battle_seq") or battle.battle_seq or 0
        )
        battle.game_duration = int(
            data.get("game_duration") or battle.game_duration or 0
        )
        battle.status = int(data.get("status") or battle.status or 0)

        hero_data: dict[int, tuple[str, str]] = {}
        bp_list = data.get("bp_list") or []
        self.db.execute(delete(BattleBp).where(BattleBp.battle_id == battle_id))
        bp_rows = 0
        for index, node in enumerate(bp_list):
            if not isinstance(node, dict):
                continue
            action_type = int(node.get("is_ban_or_pick", -1))
            if action_type not in (0, 1):
                continue
            hero_id = int(node.get("hero_id") or 0)
            hero_name = node.get("hero_name") or ""
            hero_icon = node.get("hero_icon") or ""
            self.db.add(
                BattleBp(
                    battle_id=battle_id,
                    league_id=match.league_id,
                    camp=int(node.get("camp") or 0),
                    action_type=action_type,
                    hero_id=hero_id,
                    hero_name=hero_name,
                    hero_icon=hero_icon,
                    position=int(node.get("position") or 0),
                    bp_order=index + 1,
                )
            )
            if hero_id > 0:
                hero_data[hero_id] = (hero_name, hero_icon)
            bp_rows += 1

        player_nodes = data.get("battle_player_list") or []
        team_data: dict[str, tuple[str, str]] = {}
        for camp_key in ("camp1", "camp2"):
            node = data.get(camp_key) or {}
            if not isinstance(node, dict):
                continue
            team_id = str(node.get("team_id") or "")
            if team_id:
                team_data[team_id] = (
                    node.get("team_name") or "",
                    node.get("team_icon") or "",
                )

        battle_player_rows = 0
        player_keys: set[tuple[str, str]] = set()
        pending_players: list[dict[str, Any]] = []
        seen_rows: set[tuple[str, int, int]] = set()
        camp_flip = compute_camp_flip(data, match.camp1_team_id)
        if isinstance(player_nodes, list) and player_nodes:
            self.db.execute(
                delete(BattlePlayer).where(
                    BattlePlayer.battle_id == battle_id
                )
            )
            for node in player_nodes:
                if not isinstance(node, dict):
                    continue
                team_id = str(node.get("team_id") or "")
                team_name = node.get("team_name") or ""
                team_icon = node.get("team_icon") or ""
                player_name = player_display_name(node)
                hero_id = int(node.get("hero_id") or 0)
                api_camp = int(node.get("camp") or 0)
                row_key = (player_name, hero_id, api_camp)
                if player_name and row_key in seen_rows:
                    continue
                if player_name:
                    seen_rows.add(row_key)
                    player_keys.add((player_name, team_id))
                if team_id:
                    existing_team = team_data.get(team_id, ("", ""))
                    team_data[team_id] = (
                        team_name or existing_team[0],
                        team_icon or existing_team[1],
                    )
                hero_name = node.get("hero_name") or ""
                if hero_id > 0:
                    previous = hero_data.get(hero_id, ("", ""))
                    hero_data[hero_id] = (
                        hero_name or previous[0],
                        previous[1],
                    )
                pending_players.append(
                    {
                        "team_id": team_id,
                        "team_name": team_name,
                        "player_name": player_name,
                        "player_icon": node.get("player_icon") or "",
                        "hero_id": hero_id,
                        "hero_name": hero_name,
                        "camp": api_camp,
                        "match_camp": to_match_camp(api_camp, camp_flip),
                        "position": int(node.get("position") or 0),
                        "position_desc": node.get("position_desc") or "",
                    }
                )

        for team_id, (team_name, team_icon) in team_data.items():
            self._upsert_team(team_id, team_name, team_icon)
        for row in pending_players:
            self._upsert_player(
                row["player_name"],
                row["team_id"],
                row["team_name"],
                row["player_icon"],
            )
            self.db.add(
                BattlePlayer(
                    battle_id=battle_id,
                    match_id=match.match_id,
                    league_id=match.league_id,
                    **row,
                )
            )
            battle_player_rows += 1
        for hero_id, (hero_name, hero_icon) in hero_data.items():
            self._upsert_hero(hero_id, hero_name, hero_icon)

        return {
            "bp_rows": bp_rows,
            "battle_player_rows": battle_player_rows,
            "team_ids": set(team_data),
            "player_keys": player_keys,
        }

    def _upsert_team(
        self,
        team_id: str,
        team_name: str = "",
        team_icon: str = "",
    ) -> None:
        if not team_id:
            return
        team = self.db.get(Team, team_id)
        if team is None:
            self.db.add(
                Team(
                    team_id=team_id,
                    team_name=team_name or "",
                    team_icon=team_icon or "",
                )
            )
            return
        if team_name:
            team.team_name = team_name
        if team_icon:
            team.team_icon = team_icon

    def _upsert_player(
        self,
        player_name: str,
        team_id: str,
        team_name: str = "",
        player_icon: str = "",
    ) -> None:
        if not player_name:
            return
        player = self.db.scalar(
            select(Player).where(
                Player.player_name == player_name,
                Player.team_id == team_id,
            )
        )
        if player is None:
            self.db.add(
                Player(
                    player_name=player_name,
                    team_id=team_id,
                    team_name=team_name or "",
                    player_icon=player_icon or "",
                )
            )
            return
        if team_name:
            player.team_name = team_name
        if player_icon:
            player.player_icon = player_icon

    def _upsert_hero(
        self,
        hero_id: int,
        hero_name: str = "",
        hero_icon: str = "",
    ) -> None:
        if hero_id <= 0:
            return
        hero = self.db.get(Hero, hero_id)
        if hero is None:
            self.db.add(
                Hero(
                    hero_id=hero_id,
                    hero_name=hero_name or "",
                    hero_icon=hero_icon or "",
                )
            )
            return
        if hero_name:
            hero.hero_name = hero_name
        if hero_icon:
            hero.hero_icon = hero_icon

    def _refresh_heroes_for_league(self, league_id: str) -> int:
        heroes: dict[int, tuple[str, str]] = {}
        for hero_id, hero_name, hero_icon in self.db.execute(
            select(
                BattleBp.hero_id,
                BattleBp.hero_name,
                BattleBp.hero_icon,
            ).where(
                BattleBp.league_id == league_id,
                BattleBp.hero_id > 0,
            )
        ):
            previous = heroes.get(hero_id, ("", ""))
            heroes[hero_id] = (
                hero_name or previous[0],
                hero_icon or previous[1],
            )
        for hero_id, hero_name in self.db.execute(
            select(
                BattlePlayer.hero_id,
                BattlePlayer.hero_name,
            ).where(
                BattlePlayer.league_id == league_id,
                BattlePlayer.hero_id > 0,
            )
        ):
            previous = heroes.get(hero_id, ("", ""))
            heroes[hero_id] = (hero_name or previous[0], previous[1])
        for hero_id, (hero_name, hero_icon) in heroes.items():
            self._upsert_hero(hero_id, hero_name, hero_icon)
        self.db.commit()
        return len(heroes)

    def _sleep(self) -> None:
        delay = self.settings.sync_request_delay
        if delay > 0:
            time.sleep(delay)
