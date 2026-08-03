"""Read-only retrieval of one recorded battle's complete BP sequence."""

from typing import Any

from pydantic import Field
from sqlalchemy import select

from app.agent.tools.common import LeagueArguments
from app.database import SessionLocal
from app.models import Battle, BattleBp


class GetBattleDraftArguments(LeagueArguments):
    battle_id: str = Field(min_length=1, max_length=64)


def get_battle_draft(arguments: GetBattleDraftArguments) -> dict[str, Any]:
    """Return a season-scoped battle and its actions in BP order."""
    with SessionLocal() as db:
        battle = db.scalar(
            select(Battle).where(
                Battle.battle_id == arguments.battle_id,
                Battle.league_id == arguments.league_id,
            )
        )
        if battle is None:
            raise LookupError(
                f"Unknown battle for season {arguments.league_id}: "
                f"{arguments.battle_id}"
            )
        records = db.scalars(
            select(BattleBp)
            .where(
                BattleBp.battle_id == arguments.battle_id,
                BattleBp.league_id == arguments.league_id,
            )
            .order_by(BattleBp.bp_order.asc())
        ).all()

    actions = [
        {
            "bp_order": int(row.bp_order),
            "camp": int(row.camp),
            "side": "blue" if int(row.camp) == 1 else "red",
            "action": "ban" if int(row.action_type) == 0 else "pick",
            "hero_id": int(row.hero_id),
            "hero_name": row.hero_name,
            "position": int(row.position),
        }
        for row in records
    ]
    return {
        "league_id": arguments.league_id,
        "source": "sqlite:battles+battle_bps",
        "battle_id": battle.battle_id,
        "match_id": battle.match_id,
        "battle_sequence_number": int(battle.battle_seq),
        "winning_camp": int(battle.win_camp),
        "action_count": len(actions),
        "actions": actions,
        "result_count": len(actions),
    }
