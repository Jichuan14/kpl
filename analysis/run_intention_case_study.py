"""Reproduce the motivating BP-intention probes without pooling seasons.

Run from the repository root. Outputs are research artifacts, not website or
model inputs. Each probe uses the same eligibility and comparison rules as
explore_draft_intentions.py; no policy is loaded or trained.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from explore_draft_intentions import (
    analyze, battle_key, match_time_index, read_jsonl, source_sha256,
    target_pick_legal, validate_battle,
)


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ("haiyue_own_guanyu", "ban", 521, 140, "own", "Ban 海月 → own 关羽"),
    ("haiyue_opponent_guanyu", "ban", 521, 140, "opponent", "Ban 海月 → opponent 关羽"),
    ("yuantan_opponent_luban", "ban", 581, 525, "opponent", "Ban 元坦 → opponent 鲁班大师"),
    ("yuantan_own_luban", "ban", 581, 525, "own", "Ban 元坦 → own 鲁班大师"),
    ("luban_own_yuantan", "pick", 525, 581, "own", "Pick 鲁班大师 → own 元坦"),
    ("yuantan_own_luban_pick", "pick", 581, 525, "own", "Pick 元坦 → own 鲁班大师"),
    ("haiyue_direct_denial", "ban", 521, 521, "opponent", "Ban 海月 → opponent 海月 (structural)"),
)


def describe_action_context(
    decisions: list[dict[str, Any]], matches: list[dict[str, Any]], *,
    action: str, hero_id: int, target_id: int, perspective: str,
) -> dict[str, Any]:
    """Inventory B's state at actual A moves, before continuation eligibility.

    An already banned or previously used target cannot motivate a future pick
    in this game. A visible target instead calls for a protection/package
    hypothesis. This classification describes state, not the coach's reason.
    """
    match_times = match_time_index(matches)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in decisions:
        grouped[battle_key(row)].append(row)
    counts: Counter[str] = Counter()
    orders: dict[str, Counter[int]] = defaultdict(Counter)
    examples: dict[str, dict[str, Any]] = {}
    previous_key = "team_used_in_previous_battles" if perspective == "own" else "opponent_used_in_previous_battles"
    for (match_id, battle_id), battle in grouped.items():
        battle.sort(key=lambda row: int(row.get("bp_order") or 0))
        _, reason = validate_battle(battle, match_times.get(match_id))
        if reason:
            continue
        for row in battle:
            if row["action"] != action or int(row["selected_hero_id"]) != hero_id:
                continue
            if target_id in row["current_opponent_picks"]:
                state = "already_opponent_pick"
            elif target_id in row["current_team_picks"]:
                state = "already_own_pick"
            elif target_id in row["all_current_bans"]:
                state = "already_banned"
            elif target_id in row[previous_key]:
                state = "target_team_used_in_previous_game"
            else:
                target_team = row["acting_team_id"] if perspective == "own" else row["opponent_team_id"]
                state = (
                    "still_open_for_target_team" if target_pick_legal(row, target_id, target_team)
                    else "absent_from_inferred_roster"
                )
            counts[state] += 1
            orders[state][int(row["bp_order"])] += 1
            if state not in examples:
                examples[state] = {
                    "match_id": match_id, "battle_id": battle_id,
                    "battle_seq": row["battle_seq"], "bp_order": row["bp_order"],
                    "acting_team_id": row["acting_team_id"],
                    "opponent_team_id": row["opponent_team_id"],
                    "known_prefix": [
                        {key: step[key] for key in ("bp_order", "action", "side", "selected_hero_id", "selected_hero_name")}
                        for step in battle if step["bp_order"] < row["bp_order"]
                    ],
                }
    return {
        "observed_action_count": sum(counts.values()),
        "target_state_counts": dict(sorted(counts.items())),
        "orders_by_state": {key: dict(sorted(value.items())) for key, value in sorted(orders.items())},
        "examples_by_state": examples,
        "interpretation": "Pre-action state inventory; no explanation or intention is inferred.",
    }


def pct(value: float | None) -> str:
    return "unavailable" if value is None else f"{100 * value:.1f}%"


def points(value: float | None) -> str:
    return "unavailable" if value is None else f"{100 * value:+.1f} pp"


def report_text(results: list[dict[str, Any]]) -> str:
    lines = [
        "# BP intention case study",
        "",
        "These are observed continuation associations, not intention probabilities or causal effects.",
        "Each league is analyzed separately. A trial is one eligible trigger decision;",
        "multiple decisions may come from the same battle or match.",
        "The adjusted comparison uses only strata containing both the selected hero",
        "and other legal choices, standardized to the selected-hero arm's slot distribution.",
        "",
        "| League | Probe | A triggers / distinct matches | Later B / A triggers | Raw other-choice rate | Adjusted A rate | Adjusted other-choice rate | Adjusted difference | A triggers in overlap |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        exposed = result["coverage"]["exposed"]
        controls = result["coverage"]["controls"]
        assoc = result["association"]
        n = exposed["triggers"]
        lines.append(
            f"| {result['league_id']} | {result['case_label']} | {n} / {exposed['matches']} "
            f"| {exposed['picked_later']}/{n} ({pct(exposed['rate'])}) "
            f"| {pct(controls['rate'])} | {pct(assoc['control_standardized_exposed_rate'])} "
            f"| {pct(assoc['control_standardized_control_rate'])} "
            f"| {points(assoc['standardized_delta'])} "
            f"| {assoc['overlap_exposed_triggers']}/{n} |"
        )
    lines += [
        "",
        "## Was the target still available at the observed ban?",
        "",
        "All validated actual A bans are counted here, before restricting to future B opportunities.",
        "A target already banned cannot be picked in this game. A target already picked calls for",
        "a different hypothesis, such as partner restriction or protecting a visible hero.",
        "Prior-game exclusions apply to the perspective named in the probe.",
        "",
        "| League | Probe | All A bans | B already banned | Own B visible | Opponent B visible | B used previously by target team | B still open | B outside roster |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        query = result["query"]
        if query["action"] != "ban" or query["hero_id"] == query["target_id"]:
            continue
        context = result["observed_action_context"]
        counts = context["target_state_counts"]
        lines.append(
            f"| {result['league_id']} | {result['case_label']} | {context['observed_action_count']} "
            f"| {counts.get('already_banned', 0)} | {counts.get('already_own_pick', 0)} "
            f"| {counts.get('already_opponent_pick', 0)} | {counts.get('target_team_used_in_previous_game', 0)} "
            f"| {counts.get('still_open_for_target_team', 0)} | {counts.get('absent_from_inferred_roster', 0)} |"
        )
    lines += [
        "",
        "## Reading the comparisons",
        "",
        "For an own-team preparation hypothesis, a positive difference is a candidate signal.",
        "For an opponent-pick discouragement hypothesis, the proposed signal is negative.",
        "A positive opponent rate does not disprove a package-weakening motive: the opponent",
        "may still pick B with different partners. These probes do not evaluate partner strength.",
        "The same-hero direct-denial row is structural: a banned hero cannot be picked.",
        "Its alternative-arm rate measures observed access under other bans, not the coach's motive.",
        "",
        "Results can change by season, team, exact board, roster, or patch. The strata do not",
        "control all those factors. Sparse strata and lack of overlap are visible in each JSON",
        "artifact, together with exclusions, source fingerprints, and example match/battle IDs.",
        "No scan-wide significance or independent replication is claimed. These examples were",
        "chosen to investigate the user's hypotheses, not as a statistically selected discovery set.",
        "",
        "See analysis/DRAFT_INTENTION_RESEARCH.md for the study's scope and next validation steps.",
        "",
    ]
    return "\n".join(lines)


def run(league_ids: list[str], exports_root: Path, output_dir: Path) -> list[dict[str, Any]]:
    if len(set(league_ids)) != len(league_ids):
        raise ValueError("league IDs must be unique")
    results = []
    for league_id in league_ids:
        if not league_id or not all(char.isalnum() or char in "-_" for char in league_id):
            raise ValueError(f"Invalid league ID: {league_id!r}")
        decisions_path = exports_root / league_id / "bp_decisions.jsonl"
        matches_path = exports_root / league_id / "matches.jsonl"
        decisions, matches = read_jsonl(decisions_path), read_jsonl(matches_path)
        sources = {
            "bp_decisions": str(decisions_path),
            "bp_decisions_sha256": source_sha256(decisions_path),
            "matches": str(matches_path),
            "matches_sha256": source_sha256(matches_path),
        }
        for case, action, hero_id, target_id, perspective, label in CASES:
            result = analyze(
                decisions, matches, action=action, hero_id=hero_id,
                target_id=target_id, perspective=perspective,
            )
            result.update(league_id=league_id, case=case, case_label=label, sources=sources)
            result["observed_action_context"] = describe_action_context(
                decisions, matches, action=action, hero_id=hero_id,
                target_id=target_id, perspective=perspective,
            )
            results.append(result)
    # Finish reading and analyzing every source before producing the report.
    output_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        destination = output_dir / f"{result['league_id']}_{result['case']}.json"
        destination.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    (output_dir / "summary.md").write_text(report_text(results), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-ids", nargs="+", default=["20250001", "20260001", "20260003"])
    parser.add_argument("--exports-root", type=Path, default=ROOT / "analysis" / "exports")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "analysis" / "outputs" / "draft_intention_research")
    args = parser.parse_args()
    results = run(args.league_ids, args.exports_root, args.output_dir)
    print(f"Completed {len(results)} probes. Report: {args.output_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
