"""Build one analysis-ready JSONL record per BP decision.

Input is the match-level JSONL produced by ``export_match_data.py``. Each
output record contains the state immediately before one ban or pick.

Inferred availability rules:

* In a standard battle, a team cannot pick a hero it used in an earlier
  battle of the same match.
* Current-battle bans and picks are unavailable to both teams.
* A hero used previously by the opponent remains available to the acting team.
* A ban may target any hero not already banned or picked in this battle.
* A no-ban battle is marked as a peak candidate. Previous-battle usage is not
  applied there. Because peak lineups may be selected independently, only the
  acting team's own current picks are removed from its candidate pool.

These rules only generate analysis fields. Source actions are never omitted or
changed, and questionable rows retain their quality flags.

Examples:

    python3 analysis/build_bp_decisions.py
    python3 analysis/build_bp_decisions.py \
        --input analysis/exports/20260002/matches.jsonl \
        --output analysis/exports/20260002/bp_decisions.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from common import DB_PATH, REPO_ROOT, connect, has_table

DEFAULT_INPUT = (
    REPO_ROOT / "analysis" / "exports" / "20260002" / "matches.jsonl"
)
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "analysis"
    / "exports"
    / "20260002"
    / "bp_decisions.jsonl"
)


def load_hero_roster(db_path: Path) -> tuple[list[int], dict[int, str]]:
    """Load stable hero IDs from ``heroes`` or fall back to BP rows."""
    with connect(db_path) as conn:
        has_heroes = has_table(conn, "heroes")
        if has_heroes:
            rows = conn.execute(
                """
                SELECT hero_id, hero_name
                FROM heroes
                WHERE hero_id > 0
                ORDER BY hero_id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT hero_id, MAX(hero_name) AS hero_name
                FROM battle_bps
                WHERE hero_id > 0
                GROUP BY hero_id
                ORDER BY hero_id
                """
            ).fetchall()

    hero_ids = [int(row["hero_id"]) for row in rows]
    hero_names = {
        int(row["hero_id"]): row["hero_name"] or "" for row in rows
    }
    return hero_ids, hero_names


def read_matches(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Line {line_number} of {path} is not a JSON object"
                )
            yield record


def sorted_unique(values: list[int] | set[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def teams_by_id(match: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(team.get("team_id") or ""): team
        for team in match.get("teams", [])
        if str(team.get("team_id") or "")
    }


def team_for_action(
    action: dict[str, Any],
    battle: dict[str, Any],
) -> tuple[str, str, int]:
    """Resolve the acting team, with camp mapping as a fallback."""
    team_id = str(action.get("team_id") or "")
    team_name = str(action.get("team_name") or "")
    match_camp = int(action.get("match_camp") or 0)
    if team_id:
        return team_id, team_name, match_camp

    camp = int(action.get("camp") or 0)
    camp_team = (battle.get("camp_teams") or {}).get(str(camp), {})
    return (
        str(camp_team.get("team_id") or ""),
        str(camp_team.get("team_name") or ""),
        int(camp_team.get("match_camp") or 0),
    )


def opponent_for_team(
    match_teams: dict[str, dict[str, Any]],
    acting_team_id: str,
) -> tuple[str, str]:
    for team_id, team in match_teams.items():
        if team_id != acting_team_id:
            return team_id, str(team.get("team_name") or "")
    return "", ""


def player_for_pick(
    battle: dict[str, Any],
    team_id: str,
    hero_id: int,
) -> dict[str, Any] | None:
    for player in battle.get("players", []):
        if (
            str(player.get("team_id") or "") == team_id
            and int(player.get("hero_id") or 0) == hero_id
        ):
            return player
    return None


def battle_heroes_by_team(battle: dict[str, Any]) -> dict[str, set[int]]:
    """Get final picks by team; player rows fill incomplete BP API lists."""
    result: dict[str, set[int]] = {}
    players = battle.get("players") or []
    if players:
        for player in players:
            team_id = str(player.get("team_id") or "")
            hero_id = int(player.get("hero_id") or 0)
            if team_id and hero_id > 0:
                result.setdefault(team_id, set()).add(hero_id)
        return result

    for action in battle.get("bp_actions", []):
        if action.get("action") != "pick":
            continue
        team_id = str(action.get("team_id") or "")
        hero_id = int(action.get("hero_id") or 0)
        if team_id and hero_id > 0:
            result.setdefault(team_id, set()).add(hero_id)
    return result


def is_peak_candidate(battle: dict[str, Any]) -> bool:
    actions = battle.get("bp_actions", [])
    bans = sum(action.get("action") == "ban" for action in actions)
    picks = sum(action.get("action") == "pick" for action in actions)
    return bans == 0 and picks > 0


def legal_hero_ids(
    *,
    hero_roster: list[int],
    action_type: str,
    is_peak: bool,
    acting_team_used: set[int],
    current_team_picks: set[int],
    current_bans: set[int],
    current_picks_all: set[int],
) -> list[int]:
    if action_type == "pick" and is_peak:
        return [
            hero_id
            for hero_id in hero_roster
            if hero_id not in current_team_picks
        ]

    unavailable = set(current_bans) | set(current_picks_all)
    if action_type == "pick" and not is_peak:
        unavailable |= acting_team_used
    return [hero_id for hero_id in hero_roster if hero_id not in unavailable]


def build_match_decisions(
    match: dict[str, Any],
    hero_roster: list[int],
    hero_names: dict[int, str],
) -> Iterator[dict[str, Any]]:
    match_teams = teams_by_id(match)
    used_by_team: dict[str, set[int]] = {
        team_id: set() for team_id in match_teams
    }

    battles = sorted(
        match.get("battles", []),
        key=lambda battle: int(battle.get("battle_seq") or 0),
    )
    for battle in battles:
        peak = is_peak_candidate(battle)
        current_bans_by_team: dict[str, list[int]] = {
            team_id: [] for team_id in match_teams
        }
        current_picks_by_team: dict[str, list[int]] = {
            team_id: [] for team_id in match_teams
        }
        current_bans_all: set[int] = set()
        current_picks_all: set[int] = set()
        team_action_counts: dict[str, int] = {
            team_id: 0 for team_id in match_teams
        }
        team_ban_counts: dict[str, int] = {
            team_id: 0 for team_id in match_teams
        }
        team_pick_counts: dict[str, int] = {
            team_id: 0 for team_id in match_teams
        }

        actions = sorted(
            battle.get("bp_actions", []),
            key=lambda action: int(action.get("order") or 0),
        )
        for action in actions:
            action_type = str(action.get("action") or "")
            selected_hero_id = int(action.get("hero_id") or 0)
            acting_team_id, acting_team_name, match_camp = team_for_action(
                action, battle
            )
            opponent_team_id, opponent_team_name = opponent_for_team(
                match_teams, acting_team_id
            )
            camp = int(action.get("camp") or 0)
            side = "blue" if camp == 1 else "red" if camp == 2 else "unknown"

            acting_used = used_by_team.get(acting_team_id, set())
            opponent_used = used_by_team.get(opponent_team_id, set())
            candidates = legal_hero_ids(
                hero_roster=hero_roster,
                action_type=action_type,
                is_peak=peak,
                acting_team_used=acting_used,
                current_team_picks=set(
                    current_picks_by_team.get(acting_team_id, [])
                ),
                current_bans=current_bans_all,
                current_picks_all=current_picks_all,
            )
            selected_player = (
                player_for_pick(battle, acting_team_id, selected_hero_id)
                if action_type == "pick"
                else None
            )

            quality_flags = list(battle.get("quality_flags") or [])
            if not acting_team_id:
                quality_flags.append("unmapped_acting_team")
            if selected_hero_id <= 0:
                quality_flags.append("invalid_selected_hero")
            elif selected_hero_id not in candidates:
                quality_flags.append(
                    "selected_hero_outside_inferred_legal_pool"
                )
            if action_type == "pick" and selected_player is None:
                quality_flags.append("pick_missing_player_mapping")

            winner_team_id = str(battle.get("winner_team_id") or "")
            record = {
                "league_id": match.get("league_id"),
                "match_id": match.get("match_id"),
                "match_stage": match.get("match_stage") or "",
                "battle_id": battle.get("battle_id"),
                "battle_seq": int(battle.get("battle_seq") or 0),
                "is_peak_battle": peak,
                "bp_order": int(action.get("order") or 0),
                "action": action_type,
                "acting_team_id": acting_team_id,
                "acting_team_name": acting_team_name,
                "opponent_team_id": opponent_team_id,
                "opponent_team_name": opponent_team_name,
                "camp": camp,
                "side": side,
                "match_camp": match_camp,
                "team_action_number": (
                    team_action_counts.get(acting_team_id, 0) + 1
                ),
                "team_action_type_number": (
                    (
                        team_ban_counts
                        if action_type == "ban"
                        else team_pick_counts
                    ).get(acting_team_id, 0)
                    + 1
                ),
                "team_used_in_previous_battles": sorted_unique(acting_used),
                "opponent_used_in_previous_battles": sorted_unique(
                    opponent_used
                ),
                "current_team_bans": list(
                    current_bans_by_team.get(acting_team_id, [])
                ),
                "current_opponent_bans": list(
                    current_bans_by_team.get(opponent_team_id, [])
                ),
                "current_team_picks": list(
                    current_picks_by_team.get(acting_team_id, [])
                ),
                "current_opponent_picks": list(
                    current_picks_by_team.get(opponent_team_id, [])
                ),
                "all_current_bans": sorted_unique(current_bans_all),
                "all_current_picks": sorted_unique(current_picks_all),
                "legal_hero_ids": candidates,
                "legal_hero_count": len(candidates),
                "selected_hero_id": selected_hero_id,
                "selected_hero_name": (
                    action.get("hero_name")
                    or hero_names.get(selected_hero_id, "")
                ),
                "selected_player_name": (
                    selected_player.get("player_name", "")
                    if selected_player
                    else ""
                ),
                "selected_player_position": (
                    int(selected_player.get("position") or 0)
                    if selected_player
                    else 0
                ),
                "selected_player_position_desc": (
                    selected_player.get("position_desc", "")
                    if selected_player
                    else ""
                ),
                "battle_winner_team_id": winner_team_id,
                "battle_winner_team_name": (
                    battle.get("winner_team_name") or ""
                ),
                "acting_team_won_battle": (
                    acting_team_id == winner_team_id
                    if acting_team_id and winner_team_id
                    else None
                ),
                "match_winner_team_id": (
                    match.get("match_winner_team_id") or ""
                ),
                "acting_team_won_match": (
                    acting_team_id == match.get("match_winner_team_id")
                    if acting_team_id and match.get("match_winner_team_id")
                    else None
                ),
                "quality_flags": sorted(set(quality_flags)),
            }
            yield record

            if acting_team_id:
                team_action_counts[acting_team_id] = (
                    team_action_counts.get(acting_team_id, 0) + 1
                )
            if selected_hero_id <= 0:
                continue
            if action_type == "ban":
                current_bans_all.add(selected_hero_id)
                current_bans_by_team.setdefault(acting_team_id, []).append(
                    selected_hero_id
                )
                if acting_team_id:
                    team_ban_counts[acting_team_id] = (
                        team_ban_counts.get(acting_team_id, 0) + 1
                    )
            elif action_type == "pick":
                current_picks_all.add(selected_hero_id)
                current_picks_by_team.setdefault(acting_team_id, []).append(
                    selected_hero_id
                )
                if acting_team_id:
                    team_pick_counts[acting_team_id] = (
                        team_pick_counts.get(acting_team_id, 0) + 1
                    )

        final_picks = battle_heroes_by_team(battle)
        for team_id, hero_ids in final_picks.items():
            used_by_team.setdefault(team_id, set()).update(hero_ids)


def write_decisions(
    *,
    input_path: Path,
    output_path: Path,
    hero_roster: list[int],
    hero_names: dict[int, str],
) -> tuple[int, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    match_count = 0
    decision_count = 0
    with output_path.open("w", encoding="utf-8") as output:
        for match in read_matches(input_path):
            match_count += 1
            for decision in build_match_decisions(
                match, hero_roster, hero_names
            ):
                output.write(
                    json.dumps(
                        decision,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                output.write("\n")
                decision_count += 1
    return match_count, decision_count


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one JSONL record per BP ban/pick decision"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.input.exists():
        raise FileNotFoundError(f"Match JSONL not found: {args.input}")

    hero_roster, hero_names = load_hero_roster(args.db)
    match_count, decision_count = write_decisions(
        input_path=args.input,
        output_path=args.output,
        hero_roster=hero_roster,
        hero_names=hero_names,
    )
    print(
        f"Built {decision_count} BP decisions from {match_count} matches "
        f"using {len(hero_roster)} heroes: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
