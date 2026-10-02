"""Catalog and observation availability for factual views, independent of models."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Battle, League, Match
from app.services.sync import FINISHED_MATCH_STATUS
from app.services.file_summary_cache import file_summary

RANKING_SCOPE = "season_only"
RANKING_SCHEMA_VERSION = 3


def season_rankings_ready(path: Path, league_id: str) -> bool:
    if not path.is_file():
        return False
    try:
        def validate(source):
            value = json.loads(source.read_text(encoding="utf-8"))
            return (
                value.get("schema_version") == RANKING_SCHEMA_VERSION
                and value.get("evidence_scope") == RANKING_SCOPE
                and value.get("league", {}).get("league_id") == league_id
                and value.get("history_league_ids") == [league_id]
            )
        return file_summary(path, validate, variant=("rankings", league_id))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def factual_seasons(db: Session, data_root: Path) -> list[dict]:
    leagues = db.scalars(select(League).order_by(
        League.year.desc(), League.season.desc(), League.start_time.desc(), League.id.desc()
    )).all()
    match_counts = {
        row.league_id: (row.match_count, row.completed_count)
        for row in db.execute(select(
            Match.league_id, func.count().label("match_count"),
            func.sum(case((or_(Match.status == FINISHED_MATCH_STATUS, Match.win_camp.in_([1, 2])), 1), else_=0)).label("completed_count"),
        ).group_by(Match.league_id))
    }
    teams_by_league = {}
    # Retain latest fixture name per team, in the same insertion order as the
    # former full Match scan, without materializing unused ORM match fields.
    for match in db.execute(select(Match.league_id, Match.camp1_team_id, Match.camp1_team_name,
                                   Match.camp2_team_id, Match.camp2_team_name).order_by(Match.id)):
        teams = teams_by_league.setdefault(match.league_id, {})
        for team_id, team_name in ((match.camp1_team_id, match.camp1_team_name), (match.camp2_team_id, match.camp2_team_name)):
            if team_id and team_id != "0":
                teams[team_id] = {"team_id": team_id, "team_name": team_name or team_id}
    battles_by_league = dict(db.execute(select(Battle.league_id, func.count())
        .where(Battle.win_camp.in_([1, 2])).group_by(Battle.league_id)).all())
    rows = []
    for league in leagues:
        teams = teams_by_league.get(league.league_id, {})
        match_count, completed_matches = match_counts.get(league.league_id, (0, 0))
        directory = data_root / league.league_id
        rankings_ready = season_rankings_ready(directory / "rankings.json", league.league_id)
        rows.append({
            "league_id": league.league_id, "league_name": league.league_name,
            "year": league.year, "season": league.season, "status": league.status,
            "start_time": league.start_time, "match_count": match_count,
            "completed_match_count": completed_matches,
            "completed_battle_count": battles_by_league.get(league.league_id, 0),
            "fixture_teams": sorted(teams.values(), key=lambda team: team["team_name"]),
            "statistics_ready": (directory / "overview.json").is_file(),
            "team_synergy_ready": (directory / "team-synergies.json").is_file(),
            "rankings_ready": rankings_ready,
            "ranking_status": "season_only" if rankings_ready else "unavailable",
        })
    return rows
