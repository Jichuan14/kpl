"""Build team-aware Phase 2 artifacts from exported matches and BP decisions."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from common import CURRENT_LEAGUE_ID, REPO_ROOT

DEFAULT_EXPORT_DIR = REPO_ROOT / "analysis" / "exports" / CURRENT_LEAGUE_ID
DEFAULT_OUTPUT_DIR = REPO_ROOT / "analysis" / "outputs" / CURRENT_LEAGUE_ID
DEFAULT_RECENT_MATCHES = 5


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected an object in {path}:{line_number}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    probability = successes / trials
    denominator = 1 + z * z / trials
    center = (probability + z * z / (2 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1 - probability) / trials
            + z * z / (4 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def normal_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in decisions
        if not row.get("is_peak_battle")
        and str(row.get("acting_team_id") or "")
        and str(row.get("action") or "") in {"pick", "ban"}
        and int(row.get("selected_hero_id") or 0) > 0
    ]


def effective_legal_ids(decision: dict[str, Any]) -> set[int]:
    legal = {
        int(hero_id)
        for hero_id in decision.get("legal_hero_ids") or []
        if int(hero_id) > 0
    }
    selected = int(decision.get("selected_hero_id") or 0)
    if selected > 0:
        legal.add(selected)
    return legal


def _context_specs(decision: dict[str, Any]) -> list[tuple[str, str, int, str]]:
    side = str(decision.get("side") or "")
    slot = int(decision.get("team_action_type_number") or 0)
    opponent_id = str(decision.get("opponent_team_id") or "")
    return [
        ("overall", "", 0, ""),
        ("side", side, 0, ""),
        ("slot", side, slot, ""),
        ("opponent", side, 0, opponent_id),
        ("opponent_slot", side, slot, opponent_id),
    ]


def _baseline_key(
    context_level: str,
    decision: dict[str, Any],
    hero_id: int,
) -> tuple[str, str, int, int]:
    action = str(decision.get("action") or "")
    side = str(decision.get("side") or "")
    slot = int(decision.get("team_action_type_number") or 0)
    if context_level == "overall":
        return action, "", 0, hero_id
    if context_level in {"side", "opponent"}:
        return action, side, 0, hero_id
    return action, side, slot, hero_id


def compute_tendency_rows(
    decisions: list[dict[str, Any]],
    *,
    alpha: float = 8.0,
    context_levels: set[str] | None = None,
) -> list[dict[str, Any]]:
    usable = normal_decisions(decisions)
    levels = context_levels or {
        "overall",
        "side",
        "slot",
        "opponent",
        "opponent_slot",
    }
    hero_names = {
        int(row.get("selected_hero_id") or 0): str(
            row.get("selected_hero_name") or row.get("selected_hero_id") or ""
        )
        for row in usable
    }
    team_names: dict[str, str] = {}
    opponent_names: dict[str, str] = {}
    context_decisions: Counter[tuple[str, str, str, int, str, str]] = Counter()
    opportunities: Counter[tuple[str, str, str, int, str, str, int]] = Counter()
    selections: Counter[tuple[str, str, str, int, str, str, int]] = Counter()
    wins: Counter[tuple[str, str, str, int, str, str, int]] = Counter()
    baseline_opportunities: Counter[tuple[str, str, int, int]] = Counter()
    baseline_selections: Counter[tuple[str, str, int, int]] = Counter()

    for row in usable:
        team_id = str(row.get("acting_team_id") or "")
        team_names[team_id] = str(row.get("acting_team_name") or team_id)
        opponent_id = str(row.get("opponent_team_id") or "")
        opponent_names[opponent_id] = str(row.get("opponent_team_name") or opponent_id)
        action = str(row.get("action") or "")
        legal = effective_legal_ids(row)
        selected = int(row.get("selected_hero_id") or 0)
        for context_level, side, slot, context_opponent_id in _context_specs(row):
            if context_level not in levels:
                continue
            context_key = (
                team_id,
                context_level,
                side,
                slot,
                context_opponent_id,
                action,
            )
            context_decisions[context_key] += 1
            for hero_id in legal:
                key = (*context_key, hero_id)
                opportunities[key] += 1
                baseline_opportunities[_baseline_key(context_level, row, hero_id)] += 1
            selected_key = (*context_key, selected)
            selections[selected_key] += 1
            baseline_selections[_baseline_key(context_level, row, selected)] += 1
            if row.get("acting_team_won_battle") is True:
                wins[selected_key] += 1

    rows: list[dict[str, Any]] = []
    for key, selection_count in selections.items():
        (
            team_id,
            context_level,
            side,
            slot,
            opponent_id,
            action,
            hero_id,
        ) = key
        opportunity_count = opportunities[key]
        baseline_key = _baseline_key(
            context_level,
            {
                "action": action,
                "side": side,
                "team_action_type_number": slot,
            },
            hero_id,
        )
        baseline_trials = baseline_opportunities[baseline_key]
        baseline_probability = (
            baseline_selections[baseline_key] / baseline_trials
            if baseline_trials
            else 0.0
        )
        raw_probability = selection_count / opportunity_count
        smoothed_probability = (
            (selection_count + alpha * baseline_probability)
            / (opportunity_count + alpha)
        )
        lift = (
            smoothed_probability / baseline_probability
            if baseline_probability > 0
            else None
        )
        low, high = wilson_interval(selection_count, opportunity_count)
        rows.append(
            {
                "league_id": str(usable[0].get("league_id") or "") if usable else "",
                "team_id": team_id,
                "team_name": team_names.get(team_id, team_id),
                "context_level": context_level,
                "side": side or None,
                "team_action_type_number": slot or None,
                "opponent_team_id": opponent_id or None,
                "opponent_team_name": opponent_names.get(opponent_id) if opponent_id else None,
                "action": action,
                "hero_id": hero_id,
                "hero_name": hero_names.get(hero_id, str(hero_id)),
                "context_decision_count": context_decisions[key[:-1]],
                "legal_opportunity_count": opportunity_count,
                "selection_count": selection_count,
                "raw_probability_given_legal": round(raw_probability, 6),
                "smoothed_probability_given_legal": round(smoothed_probability, 6),
                "league_baseline_probability_given_legal": round(baseline_probability, 6),
                "smoothed_lift": round(lift, 6) if lift is not None else None,
                "probability_ci95_low": round(low, 6),
                "probability_ci95_high": round(high, 6),
                "battle_win_count_when_selected": wins[key],
                "descriptive_battle_win_rate": round(wins[key] / selection_count, 6),
            }
        )

    rows.sort(
        key=lambda row: (
            row["team_name"],
            row["context_level"],
            row.get("side") or "",
            row.get("team_action_type_number") or 0,
            row.get("opponent_team_name") or "",
            row["action"],
            -row["selection_count"],
            -row["smoothed_probability_given_legal"],
        )
    )
    return rows


def compute_opening_rows(
    decisions: list[dict[str, Any]],
    *,
    sequence_length: int = 3,
) -> list[dict[str, Any]]:
    usable = normal_decisions(decisions)
    by_team_battle: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        by_team_battle[
            (str(row.get("acting_team_id") or ""), str(row.get("battle_id") or ""))
        ].append(row)

    counts: Counter[tuple[str, str, str, str, tuple[tuple[str, int, str], ...]]] = Counter()
    eligible: Counter[tuple[str, str, str, str]] = Counter()
    wins: Counter[tuple[str, str, str, str, tuple[tuple[str, int, str], ...]]] = Counter()
    team_names: dict[str, str] = {}
    opponent_names: dict[str, str] = {}
    for (team_id, _battle_id), battle_rows in by_team_battle.items():
        ordered = sorted(
            battle_rows,
            key=lambda row: int(row.get("team_action_number") or 0),
        )
        if len(ordered) < sequence_length:
            continue
        first = ordered[0]
        team_names[team_id] = str(first.get("acting_team_name") or team_id)
        opponent_id = str(first.get("opponent_team_id") or "")
        opponent_names[opponent_id] = str(first.get("opponent_team_name") or opponent_id)
        side = str(first.get("side") or "")
        sequence = tuple(
            (
                str(row.get("action") or ""),
                int(row.get("selected_hero_id") or 0),
                str(row.get("selected_hero_name") or ""),
            )
            for row in ordered[:sequence_length]
        )
        for context_level, context_side, context_opponent in (
            ("overall", "", ""),
            ("side", side, ""),
            ("opponent", side, opponent_id),
        ):
            context = (team_id, context_level, context_side, context_opponent)
            key = (*context, sequence)
            eligible[context] += 1
            counts[key] += 1
            if first.get("acting_team_won_battle") is True:
                wins[key] += 1

    rows: list[dict[str, Any]] = []
    for key, occurrence_count in counts.items():
        team_id, context_level, side, opponent_id, sequence = key
        context = (team_id, context_level, side, opponent_id)
        win_count = wins[key]
        rows.append(
            {
                "league_id": str(usable[0].get("league_id") or "") if usable else "",
                "team_id": team_id,
                "team_name": team_names.get(team_id, team_id),
                "context_level": context_level,
                "side": side or None,
                "opponent_team_id": opponent_id or None,
                "opponent_team_name": opponent_names.get(opponent_id) if opponent_id else None,
                "sequence_length": sequence_length,
                "sequence": [
                    {"order": index + 1, "action": action, "hero_id": hero_id, "hero_name": hero_name}
                    for index, (action, hero_id, hero_name) in enumerate(sequence)
                ],
                "eligible_battle_count": eligible[context],
                "occurrence_count": occurrence_count,
                "sequence_rate": round(occurrence_count / eligible[context], 6),
                "battle_win_count": win_count,
                "descriptive_battle_win_rate": round(win_count / occurrence_count, 6),
            }
        )
    rows.sort(
        key=lambda row: (
            row["team_name"],
            row["context_level"],
            row.get("side") or "",
            row.get("opponent_team_name") or "",
            -row["occurrence_count"],
            -row["sequence_rate"],
        )
    )
    return rows


def _battle_team_records(match: dict[str, Any], battle: dict[str, Any]) -> list[dict[str, Any]]:
    match_teams = {
        str(team.get("team_id") or ""): str(team.get("team_name") or "")
        for team in match.get("teams") or []
    }
    records: list[dict[str, Any]] = []
    for camp_text, team in (battle.get("camp_teams") or {}).items():
        team_id = str(team.get("team_id") or "")
        if not team_id:
            continue
        opponent_id = next((value for value in match_teams if value != team_id), "")
        player_rows = [
            player
            for player in battle.get("players") or []
            if str(player.get("team_id") or "") == team_id
            and int(player.get("hero_id") or 0) > 0
        ]
        records.append(
            {
                "league_id": str(match.get("league_id") or ""),
                "match_id": str(match.get("match_id") or ""),
                "start_time": str(match.get("start_time") or ""),
                "battle_id": str(battle.get("battle_id") or ""),
                "team_id": team_id,
                "team_name": str(team.get("team_name") or match_teams.get(team_id) or team_id),
                "opponent_team_id": opponent_id,
                "opponent_team_name": match_teams.get(opponent_id, opponent_id),
                "side": "blue" if int(camp_text) == 1 else "red",
                "won": str(battle.get("winner_team_id") or "") == team_id,
                "players": player_rows,
                "hero_ids": sorted({int(player["hero_id"]) for player in player_rows}),
            }
        )
    return records


def all_battle_team_records(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for match in matches
        for battle in match.get("battles") or []
        for record in _battle_team_records(match, battle)
    ]


def compute_combo_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = all_battle_team_records(matches)
    context_battles: Counter[tuple[str, str, str, str]] = Counter()
    pair_battles: Counter[tuple[str, str, str, str, int, int]] = Counter()
    pair_wins: Counter[tuple[str, str, str, str, int, int]] = Counter()
    names: dict[int, str] = {}
    team_names: dict[str, str] = {}
    opponent_names: dict[str, str] = {}
    for record in records:
        team_id = record["team_id"]
        team_names[team_id] = record["team_name"]
        opponent_names[record["opponent_team_id"]] = record["opponent_team_name"]
        for player in record["players"]:
            names[int(player["hero_id"])] = str(player.get("hero_name") or player["hero_id"])
        for context_level, side, opponent_id in (
            ("overall", "", ""),
            ("side", record["side"], ""),
            ("opponent", "", record["opponent_team_id"]),
            ("side_opponent", record["side"], record["opponent_team_id"]),
        ):
            context = (team_id, context_level, side, opponent_id)
            context_battles[context] += 1
            for hero_a_id, hero_b_id in combinations(record["hero_ids"], 2):
                key = (*context, hero_a_id, hero_b_id)
                pair_battles[key] += 1
                if record["won"]:
                    pair_wins[key] += 1

    rows: list[dict[str, Any]] = []
    for key, battle_count in pair_battles.items():
        team_id, context_level, side, opponent_id, hero_a_id, hero_b_id = key
        context = (team_id, context_level, side, opponent_id)
        wins = pair_wins[key]
        rows.append(
            {
                "league_id": str(records[0].get("league_id") or "") if records else "",
                "team_id": team_id,
                "team_name": team_names.get(team_id, team_id),
                "context_level": context_level,
                "side": side or None,
                "opponent_team_id": opponent_id or None,
                "opponent_team_name": opponent_names.get(opponent_id) if opponent_id else None,
                "hero_a_id": hero_a_id,
                "hero_a_name": names.get(hero_a_id, str(hero_a_id)),
                "hero_b_id": hero_b_id,
                "hero_b_name": names.get(hero_b_id, str(hero_b_id)),
                "team_battle_count": context_battles[context],
                "pair_battle_count": battle_count,
                "pair_battle_rate": round(battle_count / context_battles[context], 6),
                "battle_win_count": wins,
                "descriptive_battle_win_rate": round(wins / battle_count, 6),
            }
        )
    rows.sort(
        key=lambda row: (
            row["team_name"],
            row["context_level"],
            row.get("side") or "",
            row.get("opponent_team_name") or "",
            -row["pair_battle_count"],
            row["hero_a_id"],
            row["hero_b_id"],
        )
    )
    return rows


def compute_player_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = all_battle_team_records(matches)
    counts: Counter[tuple[str, str, int]] = Counter()
    wins: Counter[tuple[str, str, int]] = Counter()
    player_battles: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    roles: defaultdict[tuple[str, str, int], set[str]] = defaultdict(set)
    team_names: dict[str, str] = {}
    hero_names: dict[int, str] = {}
    for record in records:
        team_id = record["team_id"]
        team_names[team_id] = record["team_name"]
        for player in record["players"]:
            player_name = str(player.get("player_name") or "")
            hero_id = int(player.get("hero_id") or 0)
            if not player_name or hero_id <= 0:
                continue
            key = (team_id, player_name, hero_id)
            counts[key] += 1
            player_battles[(team_id, player_name)].add(record["battle_id"])
            hero_names[hero_id] = str(player.get("hero_name") or hero_id)
            role = str(player.get("position_desc") or "")
            if role:
                roles[key].add(role)
            if record["won"]:
                wins[key] += 1
    rows = [
        {
            "league_id": str(records[0].get("league_id") or "") if records else "",
            "team_id": team_id,
            "team_name": team_names.get(team_id, team_id),
            "player_name": player_name,
            "player_battle_count": len(player_battles[(team_id, player_name)]),
            "hero_id": hero_id,
            "hero_name": hero_names.get(hero_id, str(hero_id)),
            "pick_count": pick_count,
            "pick_share": round(
                pick_count / len(player_battles[(team_id, player_name)]), 6
            ),
            "battle_win_count": wins[(team_id, player_name, hero_id)],
            "descriptive_battle_win_rate": round(
                wins[(team_id, player_name, hero_id)] / pick_count, 6
            ),
            "positions": sorted(roles[(team_id, player_name, hero_id)]),
        }
        for (team_id, player_name, hero_id), pick_count in counts.items()
    ]
    rows.sort(
        key=lambda row: (
            row["team_name"],
            row["player_name"],
            -row["pick_count"],
            row["hero_name"],
        )
    )
    return rows


def compute_team_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match_ids: defaultdict[str, set[str]] = defaultdict(set)
    battle_ids: defaultdict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    league_id = str(matches[0].get("league_id") or "") if matches else ""
    for match in matches:
        for team in match.get("teams") or []:
            team_id = str(team.get("team_id") or "")
            if not team_id:
                continue
            names[team_id] = str(team.get("team_name") or team_id)
            match_ids[team_id].add(str(match.get("match_id") or ""))
        for battle in match.get("battles") or []:
            for team in (battle.get("camp_teams") or {}).values():
                team_id = str(team.get("team_id") or "")
                if team_id:
                    names[team_id] = str(team.get("team_name") or names.get(team_id) or team_id)
                    battle_ids[team_id].add(str(battle.get("battle_id") or ""))
    return sorted(
        [
            {
                "league_id": league_id,
                "team_id": team_id,
                "team_name": team_name,
                "match_count": len(match_ids[team_id]),
                "battle_count": len(battle_ids[team_id]),
            }
            for team_id, team_name in names.items()
        ],
        key=lambda row: (-row["battle_count"], row["team_name"]),
    )


def compute_recent_rows(
    decisions: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    season_rows: list[dict[str, Any]],
    *,
    window: int,
) -> list[dict[str, Any]]:
    team_matches: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for match in matches:
        match_id = str(match.get("match_id") or "")
        start_time = str(match.get("start_time") or "")
        for team in match.get("teams") or []:
            team_id = str(team.get("team_id") or "")
            if team_id:
                team_matches[team_id].append((start_time, match_id))
    recent_match_ids = {
        team_id: {
            match_id
            for _start_time, match_id in sorted(values, reverse=True)[:window]
        }
        for team_id, values in team_matches.items()
    }
    recent_decisions = [
        row
        for row in decisions
        if str(row.get("match_id") or "")
        in recent_match_ids.get(str(row.get("acting_team_id") or ""), set())
    ]
    rows = compute_tendency_rows(
        recent_decisions,
        context_levels={"overall", "side", "slot"},
    )
    season_index = {
        (
            row["team_id"],
            row["context_level"],
            row.get("side"),
            row.get("team_action_type_number"),
            row["action"],
            row["hero_id"],
        ): row
        for row in season_rows
        if row["context_level"] in {"overall", "side", "slot"}
    }
    for row in rows:
        key = (
            row["team_id"],
            row["context_level"],
            row.get("side"),
            row.get("team_action_type_number"),
            row["action"],
            row["hero_id"],
        )
        season_probability = float(
            (season_index.get(key) or {}).get("smoothed_probability_given_legal") or 0.0
        )
        row["recent_match_window"] = window
        row["season_smoothed_probability"] = season_probability
        row["probability_change_vs_season"] = round(
            float(row["smoothed_probability_given_legal"]) - season_probability,
            6,
        )
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", default=CURRENT_LEAGUE_ID)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--matches", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--recent-matches", type=int, default=DEFAULT_RECENT_MATCHES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.recent_matches < 1:
        raise ValueError("--recent-matches must be positive")
    export_dir = REPO_ROOT / "analysis" / "exports" / args.league_id
    decisions_path = args.decisions or export_dir / "bp_decisions.jsonl"
    matches_path = args.matches or export_dir / "matches.jsonl"
    output_dir = args.output_dir or REPO_ROOT / "analysis" / "outputs" / args.league_id
    decisions = read_jsonl(decisions_path)
    matches = read_jsonl(matches_path)
    tendencies = compute_tendency_rows(decisions)
    outputs = {
        "season_teams.jsonl": compute_team_rows(matches),
        "team_action_tendencies.jsonl": tendencies,
        "team_opening_sequences.jsonl": compute_opening_rows(decisions),
        "team_combo_performance.jsonl": compute_combo_rows(matches),
        "player_hero_pools.jsonl": compute_player_rows(matches),
        "team_recent_trends.jsonl": compute_recent_rows(
            decisions,
            matches,
            tendencies,
            window=args.recent_matches,
        ),
    }
    for filename, rows in outputs.items():
        path = output_dir / filename
        write_jsonl(path, rows)
        print(f"Wrote {len(rows)} rows: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
