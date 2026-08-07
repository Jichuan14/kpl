"""Read-only season roster retrieval from recorded battle participants."""

from typing import Any

from sqlalchemy import select

from app.agent.tools.common import normalize_name
from app.agent.tools.team_profiles import TeamReferenceArguments
from app.database import SessionLocal
from app.models import BattlePlayer


class GetTeamRosterArguments(TeamReferenceArguments):
    """Return players recorded for one team in the selected season."""


def get_team_roster(arguments: GetTeamRosterArguments) -> dict[str, Any]:
    """List distinct players seen for a team in the season's collected battles."""
    with SessionLocal() as db:
        records = db.scalars(
            select(BattlePlayer).where(BattlePlayer.league_id == arguments.league_id)
        ).all()

    rows = [
        row
        for row in records
        if (
            arguments.team_id is not None and row.team_id == arguments.team_id
        )
        or (
            arguments.team_name
            and normalize_name(row.team_name) == normalize_name(arguments.team_name)
        )
    ]
    if not rows:
        raise LookupError(
            f"No recorded players match team: {arguments.team_id or arguments.team_name}"
        )

    players: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.player_name or "").strip()
        if not name:
            continue
        key = normalize_name(name)
        player = players.setdefault(
            key,
            {
                "player_name": name,
                "player_icon": str(row.player_icon or ""),
                "battle_ids": set(),
                "positions": set(),
            },
        )
        player["battle_ids"].add(str(row.battle_id))
        if row.position_desc:
            player["positions"].add(str(row.position_desc))
        if not player["player_icon"] and row.player_icon:
            player["player_icon"] = str(row.player_icon)

    selected = sorted(
        (
            {
                "player_name": player["player_name"],
                "player_icon": player["player_icon"],
                "recorded_battle_count": len(player["battle_ids"]),
                "recorded_positions": sorted(player["positions"]),
            }
            for player in players.values()
        ),
        key=lambda player: (-player["recorded_battle_count"], normalize_name(player["player_name"])),
    )
    if not selected:
        raise LookupError(
            f"No recorded players match team: {arguments.team_id or arguments.team_name}"
        )
    return {
        "league_id": arguments.league_id,
        "source": "sqlite:battle_players",
        "team_id": rows[0].team_id,
        "team_name": rows[0].team_name,
        "rows": selected,
        "result_count": len(selected),
        "warning": "This is the set of players recorded in collected battles for this season, not an official current roster.",
    }
