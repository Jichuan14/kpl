"""Export match-level BP and player data as JSONL.

SQLite remains the source of truth. Each output line contains one complete
match. Questionable source data is preserved and described by quality flags.

Camp terminology
----------------
``camp`` and ``win_camp`` are battle-specific colors from the API:

* 1 = blue side
* 2 = red side

Teams can switch colors between battles. ``match_camp`` is different: it is
the team's fixed slot in the match record (1 = ``matches.camp1_*``,
2 = ``matches.camp2_*``). Use ``team_id`` as the stable team identity.

JSON key reference
------------------
Match object (one object per JSONL line):

* ``league_id``: Tournament/season ID.
* ``match_id``: Unique match (series) ID.
* ``match_stage``: Stage name supplied by the API.
* ``start_time``: Match start time supplied by the API.
* ``bo``: Scheduled best-of value, such as 5 or 7.
* ``score``: Final series score.
  * ``camp1``: Wins by the fixed match-camp-1 team.
  * ``camp2``: Wins by the fixed match-camp-2 team.
* ``match_winner_team_id``: Stable ID of the series winner.
* ``match_winner_team_name``: Name of the series winner.
* ``teams``: The two fixed match-level team slots.
  * ``match_camp``: Fixed match slot, 1 or 2; this is not a color.
  * ``team_id``: Stable team ID.
  * ``team_name``: Team display name.
* ``battles``: Games in the match, ordered by ``battle_seq``.

Battle object:

* ``battle_id``: Unique game ID.
* ``battle_seq``: Game number within the match (1, 2, 3, ...).
* ``game_duration_ms``: Game duration in milliseconds.
* ``win_camp``: Winning battle color (1 = blue, 2 = red).
* ``winner_team_id``: Stable ID of the team mapped to ``win_camp``.
* ``winner_team_name``: Name of the team mapped to ``win_camp``.
* ``camp_teams``: Battle color-to-team mapping. Keys ``"1"`` and ``"2"``
  mean blue and red. Each value contains:
  * ``team_id``: Stable team ID.
  * ``team_name``: Team display name.
  * ``match_camp``: That team's fixed match-level slot.
* ``bp_actions``: Ordered ban/pick actions from the BP API.
* ``players``: Final player/hero rows from battle detail.
* ``quality_flags``: Data warnings; rows are never omitted because of flags.

BP action object:

* ``order``: Action sequence from ``bp_order``.
* ``action``: ``"ban"`` or ``"pick"``.
* ``camp``: Acting battle color (1 = blue, 2 = red).
* ``team_id``: Team mapped to that battle color.
* ``team_name``: Name of the mapped team.
* ``match_camp``: Mapped team's fixed match-level slot.
* ``hero_id``: Stable hero ID.
* ``hero_name``: Hero display name.
* ``position``: Position value supplied by the BP API; often 0 for bans.

Player object:

* ``team_id``: Stable team ID.
* ``team_name``: Team display name.
* ``player_name``: Player name supplied by battle detail.
* ``hero_id``: ID of the hero the player used.
* ``hero_name``: Name of the hero the player used.
* ``camp``: Player's battle color (1 = blue, 2 = red).
* ``match_camp``: Player team's fixed match-level slot.
* ``position``: Numeric role/position supplied by the API.
* ``position_desc``: Human-readable role/position supplied by the API.

Possible ``quality_flags`` values:

* ``missing_player_data``: No player-detail rows exist for the battle.
* ``missing_win_camp``: ``win_camp`` is missing or not 1/2.
* ``unmapped_bp_camp``: A BP action's color could not be mapped to a team.
* ``invalid_or_empty_hero_id``: At least one BP action has ``hero_id <= 0``.
* ``incomplete_standard_bp``: A draft with bans does not have 10 bans and
  10 picks.
* ``peak_candidate``: The battle has picks but no bans.
* ``incomplete_peak_bp``: A no-ban battle has fewer/more than 10 BP picks.
* ``missing_bp_data``: The battle has no bans or picks.
* ``bp_player_pick_mismatch``: BP pick heroes do not exactly match the
  player-detail hero lineup.

Examples (run from the repository root):

    python3 analysis/export_match_data.py --match-id 2026042501
    python3 analysis/export_match_data.py --year 2026 --name 挑战者杯
    python3 analysis/export_match_data.py --league-id 20260002 --match-limit 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from common import DB_PATH, REPO_ROOT, connect, resolve_league

DEFAULT_EXPORT_DIR = REPO_ROOT / "analysis" / "exports"


def _has_table(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def _team_from_row(row: Any) -> dict[str, Any]:
    return {
        "team_id": row["team_id"] or "",
        "team_name": row["team_name"] or "",
        "match_camp": int(row["match_camp"] or 0),
    }


def load_camp_teams(conn, battle_id: str) -> dict[int, dict[str, Any]]:
    """Map raw API camp 1/2 to the actual team in one battle."""
    if not _has_table(conn, "battle_players"):
        return {}

    rows = conn.execute(
        """
        SELECT
            camp,
            team_id,
            MAX(team_name) AS team_name,
            match_camp
        FROM battle_players
        WHERE battle_id = ?
        GROUP BY camp, team_id, match_camp
        ORDER BY camp
        """,
        (battle_id,),
    ).fetchall()
    return {int(row["camp"]): _team_from_row(row) for row in rows}


def load_players(conn, battle_id: str) -> list[dict[str, Any]]:
    if not _has_table(conn, "battle_players"):
        return []

    rows = conn.execute(
        """
        SELECT
            team_id,
            team_name,
            player_name,
            hero_id,
            hero_name,
            camp,
            match_camp,
            position,
            position_desc
        FROM battle_players
        WHERE battle_id = ?
        ORDER BY camp, position, player_name
        """,
        (battle_id,),
    ).fetchall()
    return [
        {
            "team_id": row["team_id"] or "",
            "team_name": row["team_name"] or "",
            "player_name": row["player_name"] or "",
            "hero_id": int(row["hero_id"] or 0),
            "hero_name": row["hero_name"] or "",
            "camp": int(row["camp"] or 0),
            "match_camp": int(row["match_camp"] or 0),
            "position": int(row["position"] or 0),
            "position_desc": row["position_desc"] or "",
        }
        for row in rows
    ]


def load_bp_actions(
    conn,
    battle_id: str,
    camp_teams: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            bp_order,
            action_type,
            camp,
            hero_id,
            hero_name,
            position
        FROM battle_bps
        WHERE battle_id = ?
        ORDER BY bp_order
        """,
        (battle_id,),
    ).fetchall()

    actions: list[dict[str, Any]] = []
    for row in rows:
        camp = int(row["camp"] or 0)
        team = camp_teams.get(camp, {})
        actions.append(
            {
                "order": int(row["bp_order"] or 0),
                "action": "ban" if int(row["action_type"]) == 0 else "pick",
                "camp": camp,
                "team_id": team.get("team_id", ""),
                "team_name": team.get("team_name", ""),
                "match_camp": int(team.get("match_camp", 0)),
                "hero_id": int(row["hero_id"] or 0),
                "hero_name": row["hero_name"] or "",
                "position": int(row["position"] or 0),
            }
        )
    return actions


def battle_quality_flags(
    battle: Any,
    actions: list[dict[str, Any]],
    players: list[dict[str, Any]],
    camp_teams: dict[int, dict[str, Any]],
) -> list[str]:
    """Describe anomalies without excluding or changing any source rows."""
    flags: list[str] = []
    bans = [action for action in actions if action["action"] == "ban"]
    picks = [action for action in actions if action["action"] == "pick"]

    if not players:
        flags.append("missing_player_data")
    if int(battle["win_camp"] or 0) not in (1, 2):
        flags.append("missing_win_camp")
    if any(action["camp"] not in camp_teams for action in actions):
        flags.append("unmapped_bp_camp")
    if any(action["hero_id"] <= 0 for action in actions):
        flags.append("invalid_or_empty_hero_id")

    if bans:
        if len(bans) != 10 or len(picks) != 10:
            flags.append("incomplete_standard_bp")
    elif picks:
        flags.append("peak_candidate")
        if len(picks) != 10:
            flags.append("incomplete_peak_bp")
    else:
        flags.append("missing_bp_data")

    bp_pick_counts = Counter(
        action["hero_id"] for action in picks if action["hero_id"] > 0
    )
    player_pick_counts = Counter(
        player["hero_id"] for player in players if player["hero_id"] > 0
    )
    if players and bp_pick_counts != player_pick_counts:
        flags.append("bp_player_pick_mismatch")

    return flags


def export_battle(conn, battle: Any) -> dict[str, Any]:
    battle_id = battle["battle_id"]
    camp_teams = load_camp_teams(conn, battle_id)
    players = load_players(conn, battle_id)
    actions = load_bp_actions(conn, battle_id, camp_teams)
    win_camp = int(battle["win_camp"] or 0)
    winner = camp_teams.get(win_camp, {})

    return {
        "battle_id": battle_id,
        "battle_seq": int(battle["battle_seq"] or 0),
        "game_duration_ms": int(battle["game_duration"] or 0),
        "win_camp": win_camp,
        "winner_team_id": winner.get("team_id", ""),
        "winner_team_name": winner.get("team_name", ""),
        "camp_teams": {
            str(camp): team for camp, team in sorted(camp_teams.items())
        },
        "bp_actions": actions,
        "players": players,
        "quality_flags": battle_quality_flags(
            battle, actions, players, camp_teams
        ),
    }


def export_match(conn, match: Any) -> dict[str, Any]:
    battles = conn.execute(
        """
        SELECT battle_id, battle_seq, win_camp, game_duration, status
        FROM battles
        WHERE match_id = ?
        ORDER BY battle_seq
        """,
        (match["match_id"],),
    ).fetchall()

    match_win_camp = int(match["win_camp"] or 0)
    winner_team_id = ""
    winner_team_name = ""
    if match_win_camp == 1:
        winner_team_id = match["camp1_team_id"] or ""
        winner_team_name = match["camp1_team_name"] or ""
    elif match_win_camp == 2:
        winner_team_id = match["camp2_team_id"] or ""
        winner_team_name = match["camp2_team_name"] or ""

    return {
        "league_id": match["league_id"],
        "match_id": match["match_id"],
        "match_stage": match["match_stage"] or "",
        "start_time": match["start_time"],
        "bo": int(match["bo"] or 0),
        "score": {
            "camp1": int(match["camp1_score"] or 0),
            "camp2": int(match["camp2_score"] or 0),
        },
        "match_winner_team_id": winner_team_id,
        "match_winner_team_name": winner_team_name,
        "teams": [
            {
                "match_camp": 1,
                "team_id": match["camp1_team_id"] or "",
                "team_name": match["camp1_team_name"] or "",
            },
            {
                "match_camp": 2,
                "team_id": match["camp2_team_id"] or "",
                "team_name": match["camp2_team_name"] or "",
            },
        ],
        "battles": [export_battle(conn, battle) for battle in battles],
    }


def list_matches(
    conn,
    league_id: str,
    *,
    match_id: str | None = None,
    match_limit: int | None = None,
) -> list[Any]:
    sql = "SELECT * FROM matches WHERE league_id = ?"
    params: list[Any] = [league_id]
    if match_id:
        sql += " AND match_id = ?"
        params.append(match_id)
    sql += " ORDER BY start_time, match_id"
    if match_limit is not None:
        sql += " LIMIT ?"
        params.append(max(0, match_limit))

    rows = conn.execute(sql, params).fetchall()
    if match_id and not rows:
        raise ValueError(
            f"Match {match_id!r} does not exist in league {league_id!r}"
        )
    return rows


def write_jsonl(
    conn,
    matches: list[Any],
    output_path: Path,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for match in matches:
            record = export_match(conn, match)
            output.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            )
            output.write("\n")
    return len(matches)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export ordered match BP + player data to JSONL"
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--league-id", default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--name", dest="name_contains", default=None)
    parser.add_argument("--match-id", default=None)
    parser.add_argument("--match-limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.league_id and args.year is None and not args.name_contains:
        args.year = 2026
        args.name_contains = "挑战者杯"

    with connect(args.db) as conn:
        league = resolve_league(
            conn,
            league_id=args.league_id,
            year=args.year,
            name_contains=args.name_contains,
        )
        matches = list_matches(
            conn,
            league["league_id"],
            match_id=args.match_id,
            match_limit=args.match_limit,
        )
        output_path = args.output
        if output_path is None:
            suffix = f"_{args.match_id}" if args.match_id else ""
            output_path = (
                DEFAULT_EXPORT_DIR
                / league["league_id"]
                / f"matches{suffix}.jsonl"
            )
        count = write_jsonl(conn, matches, output_path)

    print(
        f"Exported {count} match(es) from {league['league_name']} "
        f"({league['league_id']}) to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
