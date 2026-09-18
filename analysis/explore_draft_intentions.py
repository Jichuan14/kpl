"""Offline, descriptive probe for draft move -> later hero associations.

This intentionally does not estimate intent or a causal effect.  It makes the
denominator explicit: for a move at a particular legal draft state, did a
specified team later pick B?  It is useful for finding hypotheses worth
reviewing, while keeping schedule, slot, Global BP stage, and visible board
size separate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


STANDARD_20 = (
    ("ban", "blue"), ("ban", "red"), ("ban", "blue"), ("ban", "red"),
    ("pick", "blue"), ("pick", "red"), ("pick", "red"), ("pick", "blue"),
    ("pick", "blue"), ("pick", "red"), ("ban", "red"), ("ban", "blue"),
    ("ban", "red"), ("ban", "blue"), ("ban", "red"), ("ban", "blue"),
    ("pick", "red"), ("pick", "blue"), ("pick", "blue"), ("pick", "red"),
)
# The prior four-second-ban format.  Keeping it a distinct signature prevents
# a nominally identical order from the 18 and 20 action formats being pooled.
STANDARD_18 = (
    ("ban", "blue"), ("ban", "red"), ("ban", "blue"), ("ban", "red"),
    ("pick", "blue"), ("pick", "red"), ("pick", "red"), ("pick", "blue"),
    ("pick", "blue"), ("pick", "red"), ("ban", "red"), ("ban", "blue"),
    ("ban", "red"), ("ban", "blue"), ("pick", "red"), ("pick", "blue"),
    ("pick", "blue"), ("pick", "red"),
)
KNOWN_SCHEDULES = {STANDARD_18: "standard_18", STANDARD_20: "standard_20"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no} is not an object")
            rows.append(value)
    return rows


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def match_time_index(matches: Iterable[dict[str, Any]]) -> dict[str, dt.datetime | None]:
    """Reject ambiguous joins instead of letting file order decide chronology."""
    result: dict[str, dt.datetime | None] = {}
    awareness: set[bool] = set()
    for match in matches:
        match_id = str(match.get("match_id") or "")
        if not match_id or match_id in result:
            raise ValueError(f"Missing or duplicate match ID: {match_id!r}")
        timestamp = parse_time(match.get("start_time"))
        if timestamp is not None:
            awareness.add(timestamp.utcoffset() is not None)
        result[match_id] = timestamp
    if len(awareness) > 1:
        raise ValueError("Match timestamps mix timezone-aware and local times")
    return result


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def schedule_signature(rows: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(row.get("action") or ""), str(row.get("side") or ""))
        for row in rows
    )


def battle_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("match_id") or ""), str(row.get("battle_id") or "")


def validate_battle(
    rows: list[dict[str, Any]],
    start_time: dt.datetime | None,
) -> tuple[str | None, str | None]:
    """Return (schedule name, exclusion reason); validation is all-battle."""
    if start_time is None:
        return None, "missing_match_start_time"
    if not rows or any(row.get("quality_flags") for row in rows):
        return None, "quality_flags"
    orders = [int(row.get("bp_order") or 0) for row in rows]
    if not all(battle_key(row)[0] and battle_key(row)[1] for row in rows):
        return None, "missing_ids"
    if len(set(orders)) != len(orders) or sorted(orders) != list(
        range(1, len(rows) + 1)
    ):
        return None, "invalid_orders"
    picks = Counter(
        str(row.get("acting_team_id") or "")
        for row in rows
        if row.get("action") == "pick"
    )
    if len(picks) != 2 or any(not team or count != 5 for team, count in picks.items()):
        return None, "incomplete_picks"
    signature = schedule_signature(rows)
    schedule = KNOWN_SCHEDULES.get(signature)
    if schedule is None:
        return None, "unknown_schedule"
    selected = [int(row.get("selected_hero_id") or 0) for row in rows]
    if any(hero <= 0 for hero in selected) or len(set(selected)) != len(selected):
        return None, "invalid_or_duplicate_selected_hero"
    if any(bool(row.get("is_peak_battle")) for row in rows):
        return None, "peak_battle"
    required = (
        "legal_hero_ids",
        "all_current_bans",
        "all_current_picks",
        "team_used_in_previous_battles",
        "opponent_used_in_previous_battles",
        "current_team_picks",
        "current_opponent_picks",
    )
    if any(
        any(not isinstance(row.get(key), list) for key in required)
        for row in rows
    ):
        return None, "missing_prestate"
    bans: set[int] = set()
    picks_all: set[int] = set()
    picks_by_team: dict[str, set[int]] = defaultdict(set)
    side_teams = {str(row["side"]): str(row.get("acting_team_id") or "") for row in rows}
    if len(set(side_teams.values())) != 2:
        return None, "inconsistent_team_side"
    for row in rows:
        team = str(row.get("acting_team_id") or "")
        opponent = str(row.get("opponent_team_id") or "")
        side = str(row.get("side") or "")
        if (
            not team
            or not opponent
            or team == opponent
            or side_teams[side] != team
            or opponent != side_teams["red" if side == "blue" else "blue"]
        ):
            return None, "inconsistent_team_side"
        if (
            set(map(int, row["all_current_bans"])) != bans
            or set(map(int, row["all_current_picks"])) != picks_all
        ):
            return None, "inconsistent_prestate_prefix"
        if (
            set(map(int, row["current_team_picks"])) != picks_by_team[team]
            or set(map(int, row["current_opponent_picks"]))
            != picks_by_team[opponent]
        ):
            return None, "inconsistent_prestate_prefix"
        selected_hero = int(row["selected_hero_id"])
        if selected_hero not in set(map(int, row["legal_hero_ids"])):
            return None, "selected_hero_outside_legal_pool"
        if row["action"] == "ban":
            bans.add(selected_hero)
        else:
            picks_all.add(selected_hero)
            picks_by_team[team].add(selected_hero)
    return schedule, None


def target_pick_legal(row: dict[str, Any], target_id: int, team_id: str) -> bool:
    """Whether B could be picked by team_id immediately before this action."""
    # A pick's legal pool excludes the ACTING team's previously used heroes.
    # Restore those for opponent eligibility, then apply the target team's own
    # restriction below. Ban pools already contain them. This is inferred
    # roster membership at this decision, not membership in a future export row.
    roster = set(map(int, row["legal_hero_ids"]))
    if row["action"] == "pick":
        roster.update(map(int, row["team_used_in_previous_battles"]))
    if target_id not in roster:
        return False
    unavailable = {int(x) for x in row["all_current_bans"] + row["all_current_picks"]}
    if target_id in unavailable:
        return False
    if team_id == str(row.get("acting_team_id") or ""):
        return target_id not in {int(x) for x in row["team_used_in_previous_battles"]}
    # The decision export only stores prior-game usage for both teams; selecting
    # the correct column avoids treating the acting side's Global BP history as
    # the opponent's restriction.
    if team_id == str(row.get("opponent_team_id") or ""):
        return target_id not in {
            int(x) for x in row["opponent_used_in_previous_battles"]
        }
    return False


def later_target_outcome(
    rows: list[dict[str, Any]],
    trigger_order: int,
    target_id: int,
    target_team_id: str,
    max_order_gap: int | None = None,
) -> tuple[bool, int | None, str]:
    """Later suffix outcome.  A later ban/stolen B remains a zero, never drops it."""
    for row in rows:
        order = int(row["bp_order"])
        if order <= trigger_order:
            continue
        if max_order_gap is not None and order - trigger_order > max_order_gap:
            return False, None, "not_selected_within_max_order_gap"
        if int(row.get("selected_hero_id") or 0) != target_id:
            continue
        if (
            row.get("action") == "pick"
            and str(row.get("acting_team_id") or "") == target_team_id
        ):
            return True, order - trigger_order, "picked_by_target_team"
        if row.get("action") == "ban":
            return False, None, "later_banned"
        if row.get("action") == "pick":
            return False, None, "picked_by_other_team"
    return False, None, "not_selected_later"


def has_future_pick_opportunity(
    rows: list[dict[str, Any]],
    trigger_order: int,
    team_id: str,
    max_order_gap: int | None = None,
) -> bool:
    """A B outcome is meaningful only when the relevant team still can pick."""
    return any(
        int(row["bp_order"]) > trigger_order
        and (
            max_order_gap is None
            or int(row["bp_order"]) - trigger_order <= max_order_gap
        )
        and row.get("action") == "pick"
        and str(row.get("acting_team_id") or "") == team_id
        for row in rows
    )


def wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.959963984540054
    rate = successes / total
    denom = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denom
    half = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denom
    return [centre - half, centre + half]


def rate_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    yes = sum(bool(item["picked_later"]) for item in items)
    return {
        "triggers": total,
        "battles": len({(item["match_id"], item["battle_id"]) for item in items}),
        "matches": len({item["match_id"] for item in items}),
        "picked_later": yes,
        "rate": yes / total if total else None,
        "wilson_95_descriptive_action_level": wilson(yes, total),
        "continuation_outcomes": dict(sorted(Counter(
            item["suffix_outcome"] for item in items if "suffix_outcome" in item
        ).items())),
    }


def stratum_key(row: dict[str, Any], schedule: str) -> tuple[Any, ...]:
    return (row["action"], row["side"], int(row["bp_order"]), schedule,
            int(row.get("battle_seq") or 0), len(row["current_team_picks"]),
            len(row["current_opponent_picks"]))


def analyze(
    decisions: Iterable[dict[str, Any]],
    matches: Iterable[dict[str, Any]],
    *,
    action: str,
    hero_id: int,
    target_id: int,
    perspective: str = "own",
    team_id: str | None = None,
    opponent_team_id: str | None = None,
    before_match_id: str | None = None,
    max_order_gap: int | None = None,
    bp_order: int | None = None,
    side: str | None = None,
) -> dict[str, Any]:
    if action not in {"ban", "pick"} or perspective not in {"own", "opponent"}:
        raise ValueError("Unknown action or perspective")
    if side not in {None, "blue", "red"}:
        raise ValueError("Unknown side")
    if hero_id <= 0 or target_id <= 0:
        raise ValueError("--hero-id and --target-id must be positive")
    if max_order_gap is not None and max_order_gap <= 0:
        raise ValueError("--max-order-gap must be positive")
    if bp_order is not None and bp_order <= 0:
        raise ValueError("--bp-order must be positive")
    if hero_id == target_id and perspective != "opponent":
        raise ValueError("--hero-id equals --target-id only supports --perspective opponent (structural direct denial)")
    decisions = list(decisions)
    matches = list(matches)
    match_times = match_time_index(matches)
    cutoff = match_times.get(str(before_match_id)) if before_match_id else None
    if before_match_id and cutoff is None:
        raise ValueError("--before-match-id has no valid start_time in matches.jsonl")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in decisions:
        grouped[battle_key(row)].append(row)
    raw_battle_keys = {
        (str(match["match_id"]), str(battle.get("battle_id") or ""))
        for match in matches for battle in (match.get("battles") or [])
    }
    has_raw_inventory = any("battles" in match for match in matches)
    excluded_battles: Counter[str] = Counter()
    excluded_triggers: Counter[str] = Counter()
    validated_battles = 0
    candidates: list[dict[str, Any]] = []
    names: dict[int, str] = {}
    known_heroes: set[int] = set()
    for row in decisions:
        selected = int(row.get("selected_hero_id") or 0)
        if selected > 0:
            names.setdefault(selected, str(row.get("selected_hero_name") or ""))
        known_heroes.update(int(hero) for hero in (row.get("legal_hero_ids") or []) if int(hero) > 0)
    if target_id not in known_heroes:
        raise ValueError("--target-id is absent from the exported legal hero roster")
    for key, battle in grouped.items():
        battle.sort(key=lambda r: int(r.get("bp_order") or 0))
        match_id = key[0]
        start = match_times.get(match_id)
        schedule, reason = validate_battle(battle, start)
        if reason:
            excluded_battles[reason] += 1
            continue
        if cutoff is not None and not (start < cutoff):
            excluded_battles["chronological_cutoff"] += 1
            continue
        validated_battles += 1
        for row in battle:
            if (
                row.get("action") != action
                or (bp_order is not None and int(row["bp_order"]) != bp_order)
                or (side and row.get("side") != side)
            ):
                continue
            acting = str(row.get("acting_team_id") or "")
            opponent = str(row.get("opponent_team_id") or "")
            if team_id and acting != str(team_id):
                continue
            if opponent_team_id and opponent != str(opponent_team_id):
                continue
            selected = int(row.get("selected_hero_id") or 0)
            legal = {int(x) for x in row["legal_hero_ids"]}
            if selected not in legal:
                excluded_triggers["trigger_selected_not_legal"] += 1
                continue
            if hero_id not in legal:
                excluded_triggers["hero_not_legal_at_trigger"] += 1
                continue
            target_team = acting if perspective == "own" else opponent
            if not target_pick_legal(row, target_id, target_team):
                excluded_triggers["target_not_pick_legal_pre_action"] += 1
                continue
            if not has_future_pick_opportunity(battle, int(row["bp_order"]), target_team, max_order_gap):
                excluded_triggers["no_future_pick_opportunity"] += 1
                continue
            # A control selecting B at the trigger mechanically removes B. It is
            # not an alternative that leaves B open; later bans and steals remain.
            if hero_id != target_id and selected == target_id:
                excluded_triggers["control_selected_target_at_trigger"] += 1
                continue
            picked, gap, suffix = later_target_outcome(
                battle, int(row["bp_order"]), target_id, target_team, max_order_gap,
            )
            candidates.append({
                "match_id": match_id,
                "battle_id": key[1],
                "battle_seq": int(row["battle_seq"]),
                "match_start_time": start.isoformat(),
                "bp_order": int(row["bp_order"]),
                "acting_team_id": acting,
                "opponent_team_id": opponent,
                "selected_hero_id": selected,
                "selected_hero_name": names.get(selected, ""),
                "picked_later": picked,
                "order_gap": gap,
                "suffix_outcome": suffix,
                "stratum": stratum_key(row, schedule),
            })
    exposed = [x for x in candidates if x["selected_hero_id"] == hero_id]
    controls = [x for x in candidates if x["selected_hero_id"] != hero_id]
    by_stratum: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"exposed": [], "control": []}
    )
    for arm, values in (("exposed", exposed), ("control", controls)):
        for value in values:
            by_stratum[value["stratum"]][arm].append(value)
    overlap = {
        key: arms for key, arms in by_stratum.items()
        if arms["exposed"] and arms["control"]
    }
    overlap_exp = [x for arms in overlap.values() for x in arms["exposed"]]
    overlap_ctrl = [x for arms in overlap.values() for x in arms["control"]]
    total_weight = len(overlap_exp)
    standard_control = (
        sum(
            len(arms["exposed"]) * rate_summary(arms["control"])["rate"]
            for arms in overlap.values()
        ) / total_weight
        if total_weight else None
    )
    standard_exposed = rate_summary(overlap_exp)["rate"] if total_weight else None
    raw_exposed, raw_control = rate_summary(exposed), rate_summary(controls)
    strata_output = []
    for key, arms in sorted(by_stratum.items(), key=lambda item: str(item[0])):
        strata_output.append({
            "stratum": {
                "action": key[0], "side": key[1], "bp_order": key[2],
                "schedule": key[3], "battle_seq": key[4],
                "visible_own_pick_count": key[5], "visible_opponent_pick_count": key[6],
            },
            "exposed": rate_summary(arms["exposed"]),
            "control": rate_summary(arms["control"]),
            "overlap": bool(arms["exposed"] and arms["control"]),
        })

    def examples(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{key: value for key, value in item.items() if key != "stratum"} for item in items[:5]]

    direct = hero_id == target_id
    status = "insufficient" if not exposed or not controls or not overlap else ("structural" if direct else "descriptive")
    query = {
        "action": action, "hero_id": hero_id, "hero_name": names.get(hero_id, ""),
        "target_id": target_id, "target_name": names.get(target_id, ""),
        "perspective": perspective, "team_id": team_id, "opponent_team_id": opponent_team_id,
        "before_match_id": before_match_id,
        "before_match_start_time": cutoff.isoformat() if cutoff else None,
        "max_order_gap": max_order_gap, "bp_order": bp_order, "side": side,
    }
    coverage = {
        "input_battles": len(grouped), "validated_battles_before_cutoff": validated_battles,
        "match_export_battles": len(raw_battle_keys) if has_raw_inventory else None,
        "raw_battles_without_decisions": len(raw_battle_keys - grouped.keys()) if has_raw_inventory else None,
        "decision_battles_outside_raw_inventory": len(grouped.keys() - raw_battle_keys) if has_raw_inventory else None,
        "eligible_triggers": len(candidates), "exposed": raw_exposed, "controls": raw_control,
        "excluded_battles": dict(sorted(excluded_battles.items())),
        "excluded_triggers": dict(sorted(excluded_triggers.items())),
    }
    association = {
        "raw_delta": (
            raw_exposed["rate"] - raw_control["rate"]
            if raw_exposed["rate"] is not None and raw_control["rate"] is not None else None
        ),
        "control_standardized_exposed_rate": standard_exposed,
        "control_standardized_control_rate": standard_control,
        "standardized_delta": (
            standard_exposed - standard_control
            if standard_exposed is not None and standard_control is not None else None
        ),
        "overlap_exposed_triggers": total_weight,
        "dropped_exposed_nonoverlap": len(exposed) - len(overlap_exp),
        "dropped_control_nonoverlap": len(controls) - len(overlap_ctrl),
    }
    return {
        "schema_version": 1,
        "status": status, "model_used": False, "query": query,
        "coverage": coverage, "association": association,
        "support_by_stratum": strata_output,
        "evidence_examples": {"exposed": examples(exposed), "control": examples(controls)},
        "limitations": [
            "Descriptive action-level association only; intervals are not match-adjusted and battles/matches are not independent observations.",
            "Controls are other legal actions, but team preference, opponent, exact board, patch, and unobserved strategy remain confounders.",
            "The suffix is an outcome: later bans, opponent picks, and unavailable targets remain in the denominator. No intent ground truth or causal effect is inferred.",
            "Legal pools are inferred by the existing exporter, not verified per-patch eligibility. Match start times are not completion times.",
        ],
    }


def markdown_report(result: dict[str, Any]) -> str:
    q, c, a = result["query"], result["coverage"], result["association"]

    def pct(value: float | None) -> str:
        return "unavailable" if value is None else f"{value:.1%}"

    def pp(value: float | None) -> str:
        return "unavailable" if value is None else f"{value * 100:+.1f} percentage points"

    lines = [
        "# Draft intention association probe", "",
        f"`{q['action']}` {q['hero_name']} ({q['hero_id']}) → later {q['perspective']} "
        f"{q['target_name']} ({q['target_id']})", "",
        f"Status: **{result['status']}**. This describes an association, not intent or a causal effect.",
        "",
        "| arm | triggers | battles | matches | later target-pick rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (("exposed", "selected A"), ("controls", "other legal actions")):
        arm = c[key]
        lines.append(
            f"| {label} | {arm['triggers']} | {arm['battles']} | {arm['matches']} | {pct(arm['rate'])} |"
        )
    lines += [
        "", f"Raw difference: {pp(a['raw_delta'])}.",
        f"Control-standardized difference in overlapping strata: {pp(a['standardized_delta'])}.",
        f"Overlap includes {a['overlap_exposed_triggers']}/{c['exposed']['triggers']} selected-A triggers.",
        "", "## Query", "", "```json",
        json.dumps(q, ensure_ascii=False, indent=2), "```",
        "", "## Interpretation limits", "",
        *[f"- {text}" for text in result["limitations"]],
        "", "## Battle exclusions", "",
        json.dumps(c["excluded_battles"], ensure_ascii=False, sort_keys=True),
        "", "## Trigger exclusions", "",
        json.dumps(c["excluded_triggers"], ensure_ascii=False, sort_keys=True),
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", default="20260003")
    parser.add_argument("--exports-root", type=Path, default=Path("analysis/exports"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--action", choices=("ban", "pick"), required=True)
    parser.add_argument("--hero-id", type=int, required=True)
    parser.add_argument("--target-id", type=int, required=True)
    parser.add_argument("--perspective", choices=("own", "opponent"), default="own")
    parser.add_argument("--team-id")
    parser.add_argument("--opponent-team-id")
    parser.add_argument("--before-match-id")
    parser.add_argument("--max-order-gap", type=int)
    parser.add_argument("--bp-order", type=int)
    parser.add_argument("--side", choices=("blue", "red"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.exports_root / args.league_id
    decisions_path, matches_path = root / "bp_decisions.jsonl", root / "matches.jsonl"
    result = analyze(
        read_jsonl(decisions_path), read_jsonl(matches_path),
        action=args.action, hero_id=args.hero_id, target_id=args.target_id,
        perspective=args.perspective, team_id=args.team_id,
        opponent_team_id=args.opponent_team_id, before_match_id=args.before_match_id,
        max_order_gap=args.max_order_gap, bp_order=args.bp_order, side=args.side,
    )
    result["sources"] = {
        "bp_decisions": str(decisions_path), "bp_decisions_sha256": source_sha256(decisions_path),
        "matches": str(matches_path), "matches_sha256": source_sha256(matches_path),
    }
    result["options"] = vars(args) | {
        "exports_root": str(args.exports_root), "output": str(args.output), "report": str(args.report),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    args.report.write_text(markdown_report(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
