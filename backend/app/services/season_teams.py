"""Season-scoped team lookup and validation shared by APIs."""

from __future__ import annotations

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models import BattlePlayer, Team


def list_season_teams(db: Session, league_id: str) -> list[dict[str, object]]:
    """Return only teams with recorded players in the requested season."""
    rows = db.execute(
        select(
            BattlePlayer.team_id,
            func.max(BattlePlayer.team_name).label("team_name"),
            func.max(Team.team_icon).label("team_icon"),
            func.count(distinct(BattlePlayer.match_id)).label("match_count"),
            func.count(distinct(BattlePlayer.battle_id)).label("battle_count"),
        )
        .outerjoin(Team, Team.team_id == BattlePlayer.team_id)
        .where(
            BattlePlayer.league_id == league_id,
            BattlePlayer.team_id != "",
        )
        .group_by(BattlePlayer.team_id)
        .order_by(func.max(BattlePlayer.team_name), BattlePlayer.team_id)
    ).all()
    return [
        {
            "team_id": str(row.team_id),
            "team_name": str(row.team_name or row.team_id),
            "team_icon": str(row.team_icon or ""),
            "match_count": int(row.match_count or 0),
            "battle_count": int(row.battle_count or 0),
        }
        for row in rows
    ]


def validate_season_team_pair(
    db: Session,
    league_id: str,
    blue_team_id: str | None,
    red_team_id: str | None,
) -> dict[str, dict[str, object]]:
    """Resolve a distinct Blue/Red pair against the season roster."""
    if not blue_team_id or not red_team_id:
        raise ValueError("Select both a Blue team and a Red team.")
    if blue_team_id == red_team_id:
        raise ValueError("Blue and Red must be different teams.")
    teams = {str(row["team_id"]): row for row in list_season_teams(db, league_id)}
    missing = [team_id for team_id in (blue_team_id, red_team_id) if team_id not in teams]
    if missing:
        raise ValueError(
            "The selected team is not part of this season: " + ", ".join(missing)
        )
    return {"blue": teams[blue_team_id], "red": teams[red_team_id]}
