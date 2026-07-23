"""Compute availability-adjusted BP response, synergy, and counter statistics.

Input is the decision-level JSONL created by ``build_bp_decisions.py``.
Candidate heroes contribute to a denominator only when they appear in that
decision's ``legal_hero_ids``. If an observed selection is outside the
inferred legal pool, it is retained and counted as a legal override.

Outputs:

* ``ban_response_stats.jsonl``: what the opponent bans next after a ban, plus
  every later pick by the banning team and by the opponent.
* ``pick_synergy_stats.jsonl``: probability of picking hero B when ally hero A
  is already selected in the current battle.
* ``counter_pick_stats.jsonl``: probability and win rate of picking hero B
  after opponent hero A is visible.
* ``counter_ban_stats.jsonl``: probability of banning hero B after opponent
  hero A is visible during the second ban phase.

Each file contains both ``overall`` and ``slot_context`` rows. Slot-context
rows condition on side and that side's ban/pick number. Results are
descriptive associations, not proof of strategic causation.

Example:

    python3 analysis/compute_bp_statistics.py
    python3 analysis/compute_bp_statistics.py --alpha 10 --min-selections 2
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from common import DB_PATH, REPO_ROOT, connect, has_table

DEFAULT_INPUT = (
    REPO_ROOT
    / "analysis"
    / "exports"
    / "20260002"
    / "bp_decisions.jsonl"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "analysis" / "outputs" / "20260002"

CountMap = dict[tuple[Any, ...], int]


def load_hero_names(db_path: Path) -> dict[int, str]:
    with connect(db_path) as conn:
        has_heroes = has_table(conn, "heroes")
        if has_heroes:
            rows = conn.execute(
                "SELECT hero_id, hero_name FROM heroes WHERE hero_id > 0"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT hero_id, MAX(hero_name) AS hero_name
                FROM battle_bps
                WHERE hero_id > 0
                GROUP BY hero_id
                """
            ).fetchall()
    return {int(row["hero_id"]): row["hero_name"] or "" for row in rows}


def read_decisions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Line {line_number} of {path} is not a JSON object"
                )
            rows.append(row)
    return rows


def group_battles(
    decisions: Iterable[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        key = (
            str(decision.get("match_id") or ""),
            str(decision.get("battle_id") or ""),
        )
        grouped[key].append(decision)
    battles = list(grouped.values())
    for battle in battles:
        battle.sort(key=lambda row: int(row.get("bp_order") or 0))
    battles.sort(
        key=lambda battle: (
            str(battle[0].get("match_id") or ""),
            int(battle[0].get("battle_seq") or 0),
        )
    )
    return battles


def effective_legal_ids(decision: dict[str, Any]) -> tuple[set[int], bool]:
    legal = {
        int(hero_id)
        for hero_id in decision.get("legal_hero_ids", [])
        if int(hero_id) > 0
    }
    selected = int(decision.get("selected_hero_id") or 0)
    override = selected > 0 and selected not in legal
    if override:
        legal.add(selected)
    return legal, override


def round6(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    p = successes / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            p * (1 - p) / trials + z * z / (4 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            )
            output.write("\n")
    return len(rows)


def context_groups(
    decision: dict[str, Any],
    *,
    action: str,
) -> list[tuple[Any, ...]]:
    peak = bool(decision.get("is_peak_battle"))
    return [
        ("overall", peak, action),
        (
            "slot_context",
            peak,
            action,
            str(decision.get("side") or "unknown"),
            int(decision.get("team_action_type_number") or 0),
        ),
    ]


def build_action_baselines(
    decisions: Iterable[dict[str, Any]],
) -> tuple[CountMap, CountMap, CountMap]:
    decision_counts: CountMap = defaultdict(int)
    opportunities: CountMap = defaultdict(int)
    selections: CountMap = defaultdict(int)

    for decision in decisions:
        action = str(decision.get("action") or "")
        selected = int(decision.get("selected_hero_id") or 0)
        legal, _ = effective_legal_ids(decision)
        for group in context_groups(decision, action=action):
            decision_counts[group] += 1
            for hero_id in legal:
                opportunities[group + (hero_id,)] += 1
            if selected > 0:
                selections[group + (selected,)] += 1
    return decision_counts, opportunities, selections


def relation_group(
    *,
    level: str,
    decision: dict[str, Any],
    source_hero_id: int,
) -> tuple[Any, ...]:
    peak = bool(decision.get("is_peak_battle"))
    action = str(decision.get("action") or "")
    if level == "overall":
        return (level, peak, action, source_hero_id)
    return (
        level,
        peak,
        action,
        source_hero_id,
        str(decision.get("side") or "unknown"),
        int(decision.get("team_action_type_number") or 0),
    )


def baseline_group_for_relation(
    relation_key: tuple[Any, ...],
) -> tuple[Any, ...]:
    level, peak, action = relation_key[:3]
    if level == "overall":
        return (level, peak, action)
    return (level, peak, action, relation_key[4], relation_key[5])


def build_relation_rows(
    *,
    decisions: Iterable[dict[str, Any]],
    response_action: str,
    source_field: str,
    relation_name: str,
    source_label: str,
    hero_names: dict[int, str],
    baseline_opportunities: CountMap,
    baseline_selections: CountMap,
    alpha: float,
    min_selections: int,
) -> list[dict[str, Any]]:
    group_counts: CountMap = defaultdict(int)
    opportunities: CountMap = defaultdict(int)
    selections: CountMap = defaultdict(int)
    wins: CountMap = defaultdict(int)
    overrides: CountMap = defaultdict(int)
    flagged: CountMap = defaultdict(int)

    for decision in decisions:
        if decision.get("action") != response_action:
            continue
        source_heroes = {
            int(hero_id)
            for hero_id in decision.get(source_field, [])
            if int(hero_id) > 0
        }
        if not source_heroes:
            continue

        selected = int(decision.get("selected_hero_id") or 0)
        legal, was_override = effective_legal_ids(decision)
        acting_won = decision.get("acting_team_won_battle") is True
        has_flags = bool(decision.get("quality_flags"))

        for source_hero_id in source_heroes:
            for level in ("overall", "slot_context"):
                group = relation_group(
                    level=level,
                    decision=decision,
                    source_hero_id=source_hero_id,
                )
                group_counts[group] += 1
                for candidate_id in legal:
                    opportunities[group + (candidate_id,)] += 1
                if selected <= 0:
                    continue
                pair_key = group + (selected,)
                selections[pair_key] += 1
                if acting_won:
                    wins[pair_key] += 1
                if was_override:
                    overrides[pair_key] += 1
                if has_flags:
                    flagged[pair_key] += 1

    rows: list[dict[str, Any]] = []
    for pair_key, selected_count in selections.items():
        if selected_count < min_selections:
            continue
        group = pair_key[:-1]
        candidate_id = int(pair_key[-1])
        level, peak, action, source_hero_id = group[:4]
        side = group[4] if level == "slot_context" else None
        slot = int(group[5]) if level == "slot_context" else None
        opportunity_count = opportunities[pair_key]
        context_count = group_counts[group]

        baseline_group = baseline_group_for_relation(group)
        baseline_key = baseline_group + (candidate_id,)
        baseline_opportunity_count = baseline_opportunities[baseline_key]
        baseline_selection_count = baseline_selections[baseline_key]
        baseline_probability = (
            baseline_selection_count / baseline_opportunity_count
            if baseline_opportunity_count
            else 0.0
        )
        raw_probability = (
            selected_count / opportunity_count if opportunity_count else 0.0
        )
        smoothed_probability = (
            (selected_count + alpha * baseline_probability)
            / (opportunity_count + alpha)
            if opportunity_count + alpha > 0
            else 0.0
        )
        lift = (
            smoothed_probability / baseline_probability
            if baseline_probability > 0
            else None
        )
        low, high = wilson_interval(selected_count, opportunity_count)
        win_count = wins[pair_key]

        rows.append(
            {
                "relation": relation_name,
                "context_level": level,
                "is_peak_battle": peak,
                "response_action": action,
                f"{source_label}_hero_id": int(source_hero_id),
                f"{source_label}_hero_name": hero_names.get(
                    int(source_hero_id), ""
                ),
                "response_side": side,
                "response_slot": slot,
                "candidate_hero_id": candidate_id,
                "candidate_hero_name": hero_names.get(candidate_id, ""),
                "context_decision_count": context_count,
                "legal_opportunity_count": opportunity_count,
                "selection_count": selected_count,
                "availability_rate": round6(
                    opportunity_count / context_count if context_count else 0.0
                ),
                "raw_probability_given_legal": round6(raw_probability),
                "smoothed_probability_given_legal": round6(
                    smoothed_probability
                ),
                "baseline_probability_given_legal": round6(
                    baseline_probability
                ),
                "smoothed_lift": round6(lift),
                "probability_ci95_low": round6(low),
                "probability_ci95_high": round6(high),
                "battle_win_count_when_selected": win_count,
                "battle_win_rate_when_selected": round6(
                    win_count / selected_count if selected_count else 0.0
                ),
                "legal_override_count": overrides[pair_key],
                "quality_flagged_selection_count": flagged[pair_key],
            }
        )

    rows.sort(
        key=lambda row: (
            row["context_level"],
            row[f"{source_label}_hero_id"],
            row["response_side"] or "",
            row["response_slot"] or 0,
            -row["selection_count"],
            row["candidate_hero_id"],
        )
    )
    return rows


def response_event_groups(
    *,
    level: str,
    scope: str,
    trigger: dict[str, Any],
    response: dict[str, Any],
) -> tuple[Any, ...]:
    peak = bool(trigger.get("is_peak_battle"))
    trigger_hero = int(trigger.get("selected_hero_id") or 0)
    response_action = str(response.get("action") or "")
    if level == "overall":
        return (
            level,
            scope,
            peak,
            trigger_hero,
            response_action,
        )
    return (
        level,
        scope,
        peak,
        trigger_hero,
        response_action,
        str(trigger.get("side") or "unknown"),
        int(trigger.get("team_action_type_number") or 0),
        str(response.get("side") or "unknown"),
        int(response.get("team_action_type_number") or 0),
    )


def response_baseline_group(
    event_group: tuple[Any, ...],
) -> tuple[Any, ...]:
    level, scope, peak, _, response_action = event_group[:5]
    if level == "overall":
        return (level, scope, peak, response_action)
    return (
        level,
        scope,
        peak,
        response_action,
        event_group[7],
        event_group[8],
    )


def build_ban_response_rows(
    *,
    battles: Iterable[list[dict[str, Any]]],
    hero_names: dict[int, str],
    alpha: float,
    min_selections: int,
) -> list[dict[str, Any]]:
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for battle in battles:
        for index, trigger in enumerate(battle):
            if trigger.get("action") != "ban":
                continue
            trigger_team = str(trigger.get("acting_team_id") or "")
            later_actions = battle[index + 1 :]

            for response in later_actions:
                response_team = str(response.get("acting_team_id") or "")
                if (
                    response.get("action") == "ban"
                    and response_team != trigger_team
                ):
                    events.append(
                        ("opponent_next_ban", trigger, response)
                    )
                    break

            for response in later_actions:
                if response.get("action") != "pick":
                    continue
                response_team = str(response.get("acting_team_id") or "")
                scope = (
                    "banning_team_later_pick"
                    if response_team == trigger_team
                    else "opponent_later_pick"
                )
                events.append((scope, trigger, response))

    group_counts: CountMap = defaultdict(int)
    opportunities: CountMap = defaultdict(int)
    selections: CountMap = defaultdict(int)
    wins: CountMap = defaultdict(int)
    overrides: CountMap = defaultdict(int)
    flagged: CountMap = defaultdict(int)
    baseline_opportunities: CountMap = defaultdict(int)
    baseline_selections: CountMap = defaultdict(int)

    for scope, trigger, response in events:
        selected = int(response.get("selected_hero_id") or 0)
        legal, was_override = effective_legal_ids(response)
        acting_won = response.get("acting_team_won_battle") is True
        has_flags = bool(response.get("quality_flags"))

        for level in ("overall", "slot_context"):
            group = response_event_groups(
                level=level,
                scope=scope,
                trigger=trigger,
                response=response,
            )
            base_group = response_baseline_group(group)
            group_counts[group] += 1
            for candidate_id in legal:
                opportunities[group + (candidate_id,)] += 1
                baseline_opportunities[base_group + (candidate_id,)] += 1
            if selected <= 0:
                continue
            pair_key = group + (selected,)
            selections[pair_key] += 1
            baseline_selections[base_group + (selected,)] += 1
            if acting_won:
                wins[pair_key] += 1
            if was_override:
                overrides[pair_key] += 1
            if has_flags:
                flagged[pair_key] += 1

    rows: list[dict[str, Any]] = []
    for pair_key, selected_count in selections.items():
        if selected_count < min_selections:
            continue
        group = pair_key[:-1]
        candidate_id = int(pair_key[-1])
        level, scope, peak, trigger_hero, response_action = group[:5]
        trigger_side = group[5] if level == "slot_context" else None
        trigger_slot = int(group[6]) if level == "slot_context" else None
        response_side = group[7] if level == "slot_context" else None
        response_slot = int(group[8]) if level == "slot_context" else None
        context_count = group_counts[group]
        opportunity_count = opportunities[pair_key]

        base_group = response_baseline_group(group)
        baseline_key = base_group + (candidate_id,)
        baseline_opportunity_count = baseline_opportunities[baseline_key]
        baseline_selection_count = baseline_selections[baseline_key]
        baseline_probability = (
            baseline_selection_count / baseline_opportunity_count
            if baseline_opportunity_count
            else 0.0
        )
        raw_probability = (
            selected_count / opportunity_count if opportunity_count else 0.0
        )
        smoothed_probability = (
            (selected_count + alpha * baseline_probability)
            / (opportunity_count + alpha)
            if opportunity_count + alpha > 0
            else 0.0
        )
        lift = (
            smoothed_probability / baseline_probability
            if baseline_probability > 0
            else None
        )
        low, high = wilson_interval(selected_count, opportunity_count)
        win_count = wins[pair_key]

        rows.append(
            {
                "relation": "ban_response",
                "context_level": level,
                "response_scope": scope,
                "is_peak_battle": peak,
                "trigger_action": "ban",
                "trigger_hero_id": int(trigger_hero),
                "trigger_hero_name": hero_names.get(int(trigger_hero), ""),
                "trigger_side": trigger_side,
                "trigger_slot": trigger_slot,
                "response_action": response_action,
                "response_side": response_side,
                "response_slot": response_slot,
                "response_hero_id": candidate_id,
                "response_hero_name": hero_names.get(candidate_id, ""),
                "trigger_event_count": context_count,
                "legal_opportunity_count": opportunity_count,
                "selection_count": selected_count,
                "availability_rate": round6(
                    opportunity_count / context_count if context_count else 0.0
                ),
                "raw_probability_given_legal": round6(raw_probability),
                "smoothed_probability_given_legal": round6(
                    smoothed_probability
                ),
                "baseline_probability_given_legal": round6(
                    baseline_probability
                ),
                "smoothed_lift": round6(lift),
                "probability_ci95_low": round6(low),
                "probability_ci95_high": round6(high),
                "response_team_battle_win_count": win_count,
                "response_team_battle_win_rate": round6(
                    win_count / selected_count if selected_count else 0.0
                ),
                "legal_override_count": overrides[pair_key],
                "quality_flagged_selection_count": flagged[pair_key],
            }
        )

    rows.sort(
        key=lambda row: (
            row["context_level"],
            row["response_scope"],
            row["trigger_hero_id"],
            row["trigger_side"] or "",
            row["trigger_slot"] or 0,
            row["response_action"],
            -row["selection_count"],
            row["response_hero_id"],
        )
    )
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute availability-adjusted statistical BP relations"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument(
        "--alpha",
        type=float,
        default=10.0,
        help="Prior strength for baseline-probability smoothing",
    )
    parser.add_argument(
        "--min-selections",
        type=int,
        default=1,
        help="Only output pairs selected at least this many times",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.input.exists():
        raise FileNotFoundError(f"Decision JSONL not found: {args.input}")
    if args.alpha < 0:
        raise ValueError("--alpha must be >= 0")
    if args.min_selections < 1:
        raise ValueError("--min-selections must be >= 1")

    decisions = read_decisions(args.input)
    battles = group_battles(decisions)
    hero_names = load_hero_names(args.db)
    _, baseline_opportunities, baseline_selections = build_action_baselines(
        decisions
    )

    ban_response_rows = build_ban_response_rows(
        battles=battles,
        hero_names=hero_names,
        alpha=args.alpha,
        min_selections=args.min_selections,
    )
    synergy_rows = build_relation_rows(
        decisions=decisions,
        response_action="pick",
        source_field="current_team_picks",
        relation_name="pick_synergy",
        source_label="ally",
        hero_names=hero_names,
        baseline_opportunities=baseline_opportunities,
        baseline_selections=baseline_selections,
        alpha=args.alpha,
        min_selections=args.min_selections,
    )
    counter_pick_rows = build_relation_rows(
        decisions=decisions,
        response_action="pick",
        source_field="current_opponent_picks",
        relation_name="counter_pick",
        source_label="opponent",
        hero_names=hero_names,
        baseline_opportunities=baseline_opportunities,
        baseline_selections=baseline_selections,
        alpha=args.alpha,
        min_selections=args.min_selections,
    )
    counter_ban_rows = build_relation_rows(
        decisions=decisions,
        response_action="ban",
        source_field="current_opponent_picks",
        relation_name="counter_ban",
        source_label="opponent",
        hero_names=hero_names,
        baseline_opportunities=baseline_opportunities,
        baseline_selections=baseline_selections,
        alpha=args.alpha,
        min_selections=args.min_selections,
    )

    outputs = {
        "ban_response_stats.jsonl": ban_response_rows,
        "pick_synergy_stats.jsonl": synergy_rows,
        "counter_pick_stats.jsonl": counter_pick_rows,
        "counter_ban_stats.jsonl": counter_ban_rows,
    }
    for filename, rows in outputs.items():
        path = args.output_dir / filename
        count = write_jsonl(path, rows)
        print(f"{filename}: {count} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
