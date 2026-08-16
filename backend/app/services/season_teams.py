"""Season-scoped team lookup and validation shared by APIs."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models import BattlePlayer, Match, Team


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


def next_scheduled_match(
    db: Session,
    league_id: str,
    *,
    selectable_team_ids: set[str] | None = None,
    as_of_china: datetime | None = None,
) -> dict[str, object] | None:
    """Return the next selectable fixture using China-local catalogue time."""
    now = as_of_china or datetime.now(ZoneInfo("Asia/Shanghai"))
    cutoff = now.strftime("%Y-%m-%d %H:%M:%S")
    rows = db.scalars(
        select(Match)
        .where(
            Match.league_id == league_id,
            Match.start_time.is_not(None),
            Match.start_time >= cutoff,
        )
        .order_by(Match.start_time.asc(), Match.match_id.asc())
    ).all()
    for match in rows:
        team_ids = {str(match.camp1_team_id), str(match.camp2_team_id)}
        if selectable_team_ids is not None and not team_ids.issubset(selectable_team_ids):
            continue
        return {
            "match_id": match.match_id,
            "start_time": match.start_time,
            "timezone": "Asia/Shanghai",
            "bo": int(match.bo or 0),
            "teams": [
                {"team_id": match.camp1_team_id, "team_name": match.camp1_team_name},
                {"team_id": match.camp2_team_id, "team_name": match.camp2_team_name},
            ],
        }
    return None


def current_or_next_scheduled_match(
    db: Session,
    league_id: str,
    *,
    selectable_team_ids: set[str] | None = None,
    as_of_china: datetime | None = None,
) -> dict[str, object] | None:
    """Return a recently scheduled fixture, otherwise the next one.

    The catalogue is local data.  Keeping a recent fixture lets the browser
    defer its first official live-status check until five minutes after the
    scheduled China-time start, even when a visitor opens the page mid-match.
    """
    now = as_of_china or datetime.now(ZoneInfo("Asia/Shanghai"))
    cutoff = now.strftime("%Y-%m-%d %H:%M:%S")
    recent_cutoff = now.replace(tzinfo=None) - timedelta(hours=6)

    def as_fixture(match: Match) -> dict[str, object]:
        return {
            "match_id": match.match_id,
            "start_time": match.start_time,
            "timezone": "Asia/Shanghai",
            "bo": int(match.bo or 0),
            "teams": [
                {"team_id": match.camp1_team_id, "team_name": match.camp1_team_name},
                {"team_id": match.camp2_team_id, "team_name": match.camp2_team_name},
            ],
        }

    recent_rows = db.scalars(
        select(Match)
        .where(
            Match.league_id == league_id,
            Match.start_time.is_not(None),
            Match.start_time <= cutoff,
        )
        .order_by(Match.start_time.desc(), Match.match_id.desc())
    ).all()
    for match in recent_rows:
        try:
            scheduled_at = datetime.strptime(match.start_time or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if scheduled_at < recent_cutoff:
            break
        team_ids = {str(match.camp1_team_id), str(match.camp2_team_id)}
        if selectable_team_ids is not None and not team_ids.issubset(selectable_team_ids):
            continue
        return as_fixture(match)

    return next_scheduled_match(
        db,
        league_id,
        selectable_team_ids=selectable_team_ids,
        as_of_china=now,
    )


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
