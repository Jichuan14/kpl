"""Compute availability-adjusted hero pairs preferred by each team.

For each normal-BP pick decision, every allied hero already visible is paired
with every legal candidate. If the team selects that candidate, the pair gets
one selection. Hero order is normalized, so A→B and B→A contribute to the same
team pair across different battles.

Example:

    python3 analysis/compute_team_synergies.py --league-id 20260001
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from statistical_helpers import read_decisions, wilson_interval, load_hero_metadata

from common import DB_PATH, REPO_ROOT

DEFAULT_LEAGUE_ID = "20260001"


def effective_legal_ids(decision: dict[str, Any]) -> set[int]:
    legal = {
        int(hero_id)
        for hero_id in decision.get("legal_hero_ids", [])
        if int(hero_id) > 0
    }
    selected = int(decision.get("selected_hero_id") or 0)
    if selected > 0:
        legal.add(selected)
    return legal


def compute_team_synergy_rows(
    decisions: list[dict[str, Any]],
    *,
    hero_names: dict[int, str],
    hero_icons: dict[int, str],
    alpha: float,
    min_selections: int,
) -> list[dict[str, Any]]:
    normal_picks = [
        decision
        for decision in decisions
        if decision.get("action") == "pick"
        and not decision.get("is_peak_battle")
        and str(decision.get("acting_team_id") or "")
    ]
    league_id = (
        str(normal_picks[0].get("league_id") or "") if normal_picks else ""
    )

    team_names: dict[str, str] = {}
    team_battles: dict[str, set[tuple[str, str]]] = defaultdict(set)
    baseline_opportunities: dict[tuple[str, int], int] = defaultdict(int)
    baseline_selections: dict[tuple[str, int], int] = defaultdict(int)

    for decision in normal_picks:
        team_id = str(decision.get("acting_team_id") or "")
        team_names[team_id] = (
            str(decision.get("acting_team_name") or "")
            or team_names.get(team_id, "")
        )
        team_battles[team_id].add(
            (
                str(decision.get("match_id") or ""),
                str(decision.get("battle_id") or ""),
            )
        )
        legal = effective_legal_ids(decision)
        for hero_id in legal:
            baseline_opportunities[(team_id, hero_id)] += 1
        selected = int(decision.get("selected_hero_id") or 0)
        if selected > 0:
            baseline_selections[(team_id, selected)] += 1

    pair_opportunities: dict[tuple[str, int, int], int] = defaultdict(int)
    pair_selections: dict[tuple[str, int, int], int] = defaultdict(int)
    pair_wins: dict[tuple[str, int, int], int] = defaultdict(int)
    pair_flagged: dict[tuple[str, int, int], int] = defaultdict(int)
    baseline_probability_sums: dict[
        tuple[str, int, int], float
    ] = defaultdict(float)

    for decision in normal_picks:
        team_id = str(decision.get("acting_team_id") or "")
        allied = {
            int(hero_id)
            for hero_id in decision.get("current_team_picks", [])
            if int(hero_id) > 0
        }
        if not allied:
            continue
        legal = effective_legal_ids(decision)
        selected = int(decision.get("selected_hero_id") or 0)

        for source_id in allied:
            for candidate_id in legal:
                if source_id == candidate_id:
                    continue
                hero_a_id, hero_b_id = sorted((source_id, candidate_id))
                key = (team_id, hero_a_id, hero_b_id)
                pair_opportunities[key] += 1
                baseline_key = (team_id, candidate_id)
                baseline_trials = baseline_opportunities[baseline_key]
                baseline_probability_sums[key] += (
                    baseline_selections[baseline_key] / baseline_trials
                    if baseline_trials
                    else 0.0
                )

            if selected <= 0 or selected == source_id:
                continue
            hero_a_id, hero_b_id = sorted((source_id, selected))
            selected_key = (team_id, hero_a_id, hero_b_id)
            pair_selections[selected_key] += 1
            if decision.get("acting_team_won_battle") is True:
                pair_wins[selected_key] += 1
            if decision.get("quality_flags"):
                pair_flagged[selected_key] += 1

    rows: list[dict[str, Any]] = []
    for key, selection_count in pair_selections.items():
        if selection_count < min_selections:
            continue
        team_id, hero_a_id, hero_b_id = key
        opportunity_count = pair_opportunities[key]
        raw_probability = (
            selection_count / opportunity_count if opportunity_count else 0.0
        )
        baseline_probability = (
            baseline_probability_sums[key] / opportunity_count
            if opportunity_count
            else 0.0
        )
        smoothed_probability = (
            (selection_count + alpha * baseline_probability)
            / (opportunity_count + alpha)
            if opportunity_count + alpha > 0
            else 0.0
        )
        lift = (
            smoothed_probability / baseline_probability
            if baseline_probability > 0
            else None
        )
        low, high = wilson_interval(selection_count, opportunity_count)
        win_count = pair_wins[key]
        rows.append(
            {
                "league_id": league_id,
                "team_id": team_id,
                "team_name": team_names.get(team_id, ""),
                "team_battle_count": len(team_battles[team_id]),
                "hero_a_id": hero_a_id,
                "hero_a_name": hero_names.get(hero_a_id, str(hero_a_id)),
                "hero_a_icon": hero_icons.get(hero_a_id, ""),
                "hero_b_id": hero_b_id,
                "hero_b_name": hero_names.get(hero_b_id, str(hero_b_id)),
                "hero_b_icon": hero_icons.get(hero_b_id, ""),
                "pair_name": (
                    f"{hero_names.get(hero_a_id, str(hero_a_id))} + "
                    f"{hero_names.get(hero_b_id, str(hero_b_id))}"
                ),
                "legal_completion_opportunity_count": opportunity_count,
                "selection_count": selection_count,
                "raw_completion_probability": round(
                    raw_probability, 6
                ),
                "smoothed_completion_probability": round(
                    smoothed_probability, 6
                ),
                "team_baseline_completion_probability": round(
                    baseline_probability, 6
                ),
                "smoothed_lift": round(lift, 6) if lift is not None else None,
                "probability_ci95_low": round(low, 6),
                "probability_ci95_high": round(high, 6),
                "battle_win_count_when_paired": win_count,
                "battle_win_rate_when_paired": round(
                    win_count / selection_count, 6
                ),
                "quality_flagged_selection_count": pair_flagged[key],
            }
        )

    rows.sort(
        key=lambda row: (
            row["team_name"],
            row["team_id"],
            -row["selection_count"],
            -(row["smoothed_lift"] or 0.0),
            row["hero_a_id"],
            row["hero_b_id"],
        )
    )
    team_ranks: dict[str, int] = defaultdict(int)
    for row in rows:
        team_ranks[row["team_id"]] += 1
        row["team_pair_rank"] = team_ranks[row["team_id"]]
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            )
            output.write("\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute availability-adjusted hero synergies by team"
    )
    parser.add_argument("--league-id", default=DEFAULT_LEAGUE_ID)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--alpha", type=float, default=10.0)
    parser.add_argument("--min-selections", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.alpha < 0:
        raise ValueError("--alpha must be >= 0")
    if args.min_selections < 1:
        raise ValueError("--min-selections must be >= 1")
    input_path = args.input or (
        REPO_ROOT
        / "analysis"
        / "exports"
        / args.league_id
        / "bp_decisions.jsonl"
    )
    output_path = args.output or (
        REPO_ROOT
        / "analysis"
        / "outputs"
        / args.league_id
        / "team_synergy_stats.jsonl"
    )
    if not input_path.exists():
        raise FileNotFoundError(f"Decision JSONL not found: {input_path}")

    decisions = read_decisions(input_path)
    hero_names, hero_icons = load_hero_metadata(args.db)
    rows = compute_team_synergy_rows(
        decisions,
        hero_names=hero_names,
        hero_icons=hero_icons,
        alpha=args.alpha,
        min_selections=args.min_selections,
    )
    write_jsonl(output_path, rows)
    print(f"Wrote {len(rows)} team synergy rows: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
