"""Rank opening-priority heroes from first-phase bans and Blue first picks.

The script calculates three related measures for normal BP battles:

* Opening ban rate: share of eligible battles where a hero was banned in
  BP orders 1-4.
* Blue first-pick conversion: share of Blue first-pick decisions where the
  hero survived the opening bans and was legal, then was selected.
* Early priority rate: share of eligible battles where the hero was either
  opening-banned or Blue first-picked.

Example:

    python3 analysis/compute_meta_heroes.py --league-id 20260002
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from common import DB_PATH, REPO_ROOT, connect

DEFAULT_LEAGUE_ID = "20260002"


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
        if decision.get("is_peak_battle"):
            continue
        key = (
            str(decision.get("match_id") or ""),
            str(decision.get("battle_id") or ""),
        )
        grouped[key].append(decision)
    battles = list(grouped.values())
    for battle in battles:
        battle.sort(key=lambda row: int(row.get("bp_order") or 0))
    return battles


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


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    probability = successes / trials
    denominator = 1 + z * z / trials
    center = (
        probability + z * z / (2 * trials)
    ) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1 - probability) / trials
            + z * z / (4 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def load_hero_metadata(
    db_path: Path,
) -> tuple[dict[int, str], dict[int, str]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT hero_id, hero_name, hero_icon FROM heroes WHERE hero_id > 0"
        ).fetchall()
    names = {int(row["hero_id"]): row["hero_name"] or "" for row in rows}
    icons = {int(row["hero_id"]): row["hero_icon"] or "" for row in rows}
    return names, icons


def compute_meta_rows(
    decisions: list[dict[str, Any]],
    *,
    hero_names: dict[int, str],
    hero_icons: dict[int, str],
    min_battles: int,
) -> list[dict[str, Any]]:
    league_id = str(decisions[0].get("league_id") or "") if decisions else ""
    eligible_battles: dict[int, int] = defaultdict(int)
    opening_bans: dict[int, int] = defaultdict(int)
    blue_first_pick_opportunities: dict[int, int] = defaultdict(int)
    blue_first_picks: dict[int, int] = defaultdict(int)
    early_priority: dict[int, int] = defaultdict(int)
    flagged_priority: dict[int, int] = defaultdict(int)
    observed_names: dict[int, str] = {}

    for battle in group_battles(decisions):
        opening = [
            decision
            for decision in battle
            if decision.get("action") == "ban"
            and 1 <= int(decision.get("bp_order") or 0) <= 4
        ]
        if not opening:
            continue

        eligible = set().union(
            *(effective_legal_ids(decision) for decision in opening)
        )
        banned = {
            int(decision.get("selected_hero_id") or 0)
            for decision in opening
            if int(decision.get("selected_hero_id") or 0) > 0
        }
        for decision in opening:
            hero_id = int(decision.get("selected_hero_id") or 0)
            if hero_id > 0 and decision.get("selected_hero_name"):
                observed_names[hero_id] = decision["selected_hero_name"]

        blue_first = next(
            (
                decision
                for decision in battle
                if decision.get("action") == "pick"
                and decision.get("side") == "blue"
                and int(decision.get("team_action_type_number") or 0) == 1
            ),
            None,
        )
        blue_legal = effective_legal_ids(blue_first) if blue_first else set()
        blue_selected = (
            int(blue_first.get("selected_hero_id") or 0)
            if blue_first
            else 0
        )
        if blue_first and blue_selected > 0 and blue_first.get(
            "selected_hero_name"
        ):
            observed_names[blue_selected] = blue_first["selected_hero_name"]

        for hero_id in eligible:
            eligible_battles[hero_id] += 1
        for hero_id in banned:
            opening_bans[hero_id] += 1
            early_priority[hero_id] += 1
        for hero_id in blue_legal:
            blue_first_pick_opportunities[hero_id] += 1
        if blue_selected > 0:
            blue_first_picks[blue_selected] += 1
            if blue_selected not in banned:
                early_priority[blue_selected] += 1

        flagged = any(decision.get("quality_flags") for decision in opening)
        if blue_first and blue_first.get("quality_flags"):
            flagged = True
        if flagged:
            for hero_id in banned | ({blue_selected} if blue_selected > 0 else set()):
                flagged_priority[hero_id] += 1

    rows: list[dict[str, Any]] = []
    for hero_id, battle_count in eligible_battles.items():
        if battle_count < min_battles:
            continue
        ban_count = opening_bans[hero_id]
        first_pick_count = blue_first_picks[hero_id]
        first_pick_opportunities = blue_first_pick_opportunities[hero_id]
        priority_count = early_priority[hero_id]
        low, high = wilson_interval(priority_count, battle_count)
        rows.append(
            {
                "league_id": league_id,
                "hero_id": hero_id,
                "hero_name": (
                    hero_names.get(hero_id)
                    or observed_names.get(hero_id)
                    or str(hero_id)
                ),
                "hero_icon": hero_icons.get(hero_id, ""),
                "eligible_battle_count": battle_count,
                "opening_ban_count": ban_count,
                "opening_ban_rate": round(ban_count / battle_count, 6),
                "blue_first_pick_legal_opportunity_count": (
                    first_pick_opportunities
                ),
                "blue_first_pick_count": first_pick_count,
                "blue_first_pick_rate_given_legal": round(
                    first_pick_count / first_pick_opportunities, 6
                )
                if first_pick_opportunities
                else 0.0,
                "early_priority_count": priority_count,
                "early_priority_rate": round(
                    priority_count / battle_count, 6
                ),
                "early_priority_ci95_low": round(low, 6),
                "early_priority_ci95_high": round(high, 6),
                "opening_ban_share_of_priority": round(
                    ban_count / priority_count, 6
                )
                if priority_count
                else 0.0,
                "blue_first_pick_share_of_priority": round(
                    first_pick_count / priority_count, 6
                )
                if priority_count
                else 0.0,
                "quality_flagged_priority_count": flagged_priority[hero_id],
            }
        )

    rows.sort(
        key=lambda row: (
            -row["early_priority_rate"],
            -row["early_priority_count"],
            -row["opening_ban_rate"],
            row["hero_id"],
        )
    )
    for rank, row in enumerate(rows, 1):
        row["priority_rank"] = rank
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
        description="Rank opening-priority heroes by early bans and Blue first picks"
    )
    parser.add_argument("--league-id", default=DEFAULT_LEAGUE_ID)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--min-battles", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.min_battles < 1:
        raise ValueError("--min-battles must be >= 1")
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
        / "meta_hero_stats.jsonl"
    )
    if not input_path.exists():
        raise FileNotFoundError(f"Decision JSONL not found: {input_path}")

    decisions = read_decisions(input_path)
    hero_names, hero_icons = load_hero_metadata(args.db)
    rows = compute_meta_rows(
        decisions,
        hero_names=hero_names,
        hero_icons=hero_icons,
        min_battles=args.min_battles,
    )
    write_jsonl(output_path, rows)
    print(f"Wrote {len(rows)} meta hero rows: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
