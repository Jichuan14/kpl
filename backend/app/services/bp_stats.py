from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Battle, BattleBp, HeroBpStats


def recompute_hero_bp_stats(db: Session, league_id: str) -> dict[str, Any]:
    """Aggregate ban/pick/win rates from battle_bps + battles into hero_bp_stats."""
    battle_count = db.scalar(
        select(func.count()).select_from(Battle).where(Battle.league_id == league_id)
    ) or 0
    if battle_count == 0:
        db.execute(delete(HeroBpStats).where(HeroBpStats.league_id == league_id))
        db.commit()
        return {"league_id": league_id, "battle_count": 0, "heroes": 0}

    win_by_battle = {
        b.battle_id: b.win_camp
        for b in db.scalars(select(Battle).where(Battle.league_id == league_id))
    }

    # hero_id -> counters
    stats: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "hero_name": "",
            "hero_icon": "",
            "ban_count": 0,
            "pick_count": 0,
            "win_count": 0,
            "banned_battles": set(),
            "picked_battles": set(),
            "present_battles": set(),
        }
    )

    rows = db.scalars(select(BattleBp).where(BattleBp.league_id == league_id))
    for row in rows:
        s = stats[row.hero_id]
        if row.hero_name:
            s["hero_name"] = row.hero_name
        if row.hero_icon:
            s["hero_icon"] = row.hero_icon
        s["present_battles"].add(row.battle_id)

        if row.action_type == 0:
            s["ban_count"] += 1
            s["banned_battles"].add(row.battle_id)
        elif row.action_type == 1:
            s["pick_count"] += 1
            s["picked_battles"].add(row.battle_id)
            if win_by_battle.get(row.battle_id) == row.camp:
                s["win_count"] += 1

    db.execute(delete(HeroBpStats).where(HeroBpStats.league_id == league_id))

    for hero_id, s in stats.items():
        pick_count = s["pick_count"]
        ban_battles = len(s["banned_battles"])
        pick_battles = len(s["picked_battles"])
        present = len(s["present_battles"])
        db.add(
            HeroBpStats(
                league_id=league_id,
                hero_id=hero_id,
                hero_name=s["hero_name"],
                hero_icon=s["hero_icon"],
                battle_count=battle_count,
                ban_count=s["ban_count"],
                pick_count=pick_count,
                win_count=s["win_count"],
                ban_rate=round(ban_battles / battle_count, 4) if battle_count else 0.0,
                pick_rate=round(pick_battles / battle_count, 4) if battle_count else 0.0,
                presence_rate=round(present / battle_count, 4) if battle_count else 0.0,
                win_rate=round(s["win_count"] / pick_count, 4) if pick_count else 0.0,
            )
        )

    db.commit()
    return {"league_id": league_id, "battle_count": battle_count, "heroes": len(stats)}
