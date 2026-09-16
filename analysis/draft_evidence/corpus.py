from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from analysis.explore_draft_intentions import (
        battle_key,
        later_target_outcome,
        match_time_index,
        read_jsonl,
        source_sha256,
        target_pick_legal,
        validate_battle,
        wilson,
    )
except ModuleNotFoundError:  # Direct execution from the analysis directory.
    from explore_draft_intentions import (
        battle_key,
        later_target_outcome,
        match_time_index,
        read_jsonl,
        source_sha256,
        target_pick_legal,
        validate_battle,
        wilson,
    )


@dataclass(frozen=True)
class ValidBattle:
    league_id: str
    match_id: str
    battle_id: str
    start_time: dt.datetime
    schedule: str
    rows: tuple[dict[str, Any], ...]


def context_key(league_id: str, schedule: str, row: dict[str, Any]) -> tuple[Any, ...]:
    """Compatibility key. League is retained so pooling happens after estimation."""
    return (
        league_id,
        schedule,
        str(row["action"]),
        str(row["side"]),
        int(row["bp_order"]),
        int(row.get("battle_seq") or 0),
        len(row["current_team_picks"]),
        len(row["current_opponent_picks"]),
    )


def inventory(exports_root: Path) -> list[dict[str, Any]]:
    rows = []
    for directory in sorted(exports_root.iterdir() if exports_root.is_dir() else []):
        decisions = directory / "bp_decisions.jsonl"
        matches = directory / "matches.jsonl"
        if not directory.is_dir() or not decisions.is_file() or not matches.is_file():
            continue
        rows.append({
            "league_id": directory.name,
            "decisions_path": decisions,
            "matches_path": matches,
            "decisions_sha256": source_sha256(decisions),
            "matches_sha256": source_sha256(matches),
        })
    return rows


def load_corpus(exports_root: Path) -> tuple[list[ValidBattle], dict[str, Any]]:
    battles: list[ValidBattle] = []
    coverage: dict[str, Any] = {"leagues": [], "exclusions": {}}
    seen: set[tuple[str, str, str]] = set()
    for source in inventory(exports_root):
        league_id = source["league_id"]
        decisions = read_jsonl(source["decisions_path"])
        matches = read_jsonl(source["matches_path"])
        times = match_time_index(matches)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in decisions:
            grouped[battle_key(row)].append(row)
        exclusions: Counter[str] = Counter()
        valid = 0
        for (match_id, battle_id), rows in grouped.items():
            rows.sort(key=lambda row: int(row.get("bp_order") or 0))
            schedule, reason = validate_battle(rows, times.get(match_id))
            if reason:
                exclusions[reason] += 1
                continue
            identity = (league_id, match_id, battle_id)
            if identity in seen:
                exclusions["duplicate_identity"] += 1
                continue
            seen.add(identity)
            valid += 1
            battles.append(ValidBattle(
                league_id=league_id,
                match_id=match_id,
                battle_id=battle_id,
                start_time=times[match_id],  # validated above
                schedule=str(schedule),
                rows=tuple(rows),
            ))
        coverage["leagues"].append({
            "league_id": league_id,
            "valid_battles": valid,
            "decision_rows": len(decisions),
            "excluded_battles": dict(sorted(exclusions.items())),
            "bp_decisions_sha256": source["decisions_sha256"],
            "matches_sha256": source["matches_sha256"],
        })
        coverage["exclusions"][league_id] = dict(sorted(exclusions.items()))
    battles.sort(key=lambda battle: (battle.start_time, battle.league_id, battle.match_id, battle.battle_id))
    coverage["valid_battles"] = len(battles)
    coverage["league_count"] = len(coverage["leagues"])
    return battles, coverage


def corpus_id(coverage: dict[str, Any], config: dict[str, Any]) -> str:
    source = json.dumps(
        {"sources": coverage["leagues"], "config": config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(source).hexdigest()[:20]


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    successes = sum(bool(item["picked_later"]) for item in items)
    return {
        "actions": total,
        "matches": len({(item["league_id"], item["match_id"]) for item in items}),
        "picked_later": successes,
        "rate": successes / total if total else None,
        "wilson_95_action_level": wilson(successes, total),
    }


def continuation_evidence(
    battles: Iterable[ValidBattle],
    *,
    target_battle: ValidBattle,
    trigger_row: dict[str, Any],
    target_hero_id: int,
    perspective: str,
    mode: str,
) -> dict[str, Any]:
    if mode not in {"retrospective_all_available", "as_of_target_match"}:
        raise ValueError("Unknown evidence mode")
    target_team = (
        str(trigger_row["acting_team_id"])
        if perspective == "own"
        else str(trigger_row["opponent_team_id"])
    )
    selected_hero = int(trigger_row["selected_hero_id"])
    action = str(trigger_row["action"])
    side = str(trigger_row["side"])
    order = int(trigger_row["bp_order"])
    observations: list[dict[str, Any]] = []
    excluded = Counter()
    for battle in battles:
        if battle.match_id == target_battle.match_id and battle.league_id == target_battle.league_id:
            excluded["target_match"] += 1
            continue
        if mode == "as_of_target_match" and not battle.start_time < target_battle.start_time:
            excluded["not_before_target"] += 1
            continue
        row = battle.rows[order - 1] if len(battle.rows) >= order else None
        if row is None or row["action"] != action or row["side"] != side:
            excluded["incompatible_slot"] += 1
            continue
        # Rule, game number, and visible-count matching are mandatory inside a season.
        if (
            battle.schedule != target_battle.schedule
            or int(row.get("battle_seq") or 0) != int(trigger_row.get("battle_seq") or 0)
            or len(row["current_team_picks"]) != len(trigger_row["current_team_picks"])
            or len(row["current_opponent_picks"]) != len(trigger_row["current_opponent_picks"])
        ):
            excluded["incompatible_context"] += 1
            continue
        relevant_team = str(row["acting_team_id"] if perspective == "own" else row["opponent_team_id"])
        if not target_pick_legal(row, target_hero_id, relevant_team):
            excluded["target_not_legal"] += 1
            continue
        legal = {int(value) for value in row["legal_hero_ids"]}
        if selected_hero not in legal:
            excluded["selected_action_not_available"] += 1
            continue
        observed_selected = int(row["selected_hero_id"])
        if observed_selected == target_hero_id and selected_hero != target_hero_id:
            excluded["control_selected_target"] += 1
            continue
        picked, gap, suffix = later_target_outcome(
            list(battle.rows), order, target_hero_id, relevant_team
        )
        observations.append({
            "league_id": battle.league_id,
            "match_id": battle.match_id,
            "battle_id": battle.battle_id,
            "selected": observed_selected == selected_hero,
            "picked_later": picked,
            "order_gap": gap,
            "suffix_outcome": suffix,
        })
    seasons = []
    for league_id in sorted({item["league_id"] for item in observations}):
        local = [item for item in observations if item["league_id"] == league_id]
        exposed = [item for item in local if item["selected"]]
        controls = [item for item in local if not item["selected"]]
        exp, ctrl = _summary(exposed), _summary(controls)
        seasons.append({
            "league_id": league_id,
            "selected_action": exp,
            "other_legal_actions": ctrl,
            "difference": exp["rate"] - ctrl["rate"] if exp["rate"] is not None and ctrl["rate"] is not None else None,
            "overlap": bool(exposed and controls),
        })
    overlap = [item for item in seasons if item["overlap"]]
    weight = sum(item["selected_action"]["actions"] for item in overlap)
    selected_rate = (
        sum(item["selected_action"]["picked_later"] for item in overlap) / weight
        if weight else None
    )
    control_rate = (
        sum(item["selected_action"]["actions"] * item["other_legal_actions"]["rate"] for item in overlap) / weight
        if weight else None
    )
    differences = [item["difference"] for item in overlap if item["difference"] is not None]
    directions = {0 if value == 0 else (1 if value > 0 else -1) for value in differences}
    return {
        "kind": "continuation_association",
        "status": "descriptive" if weight else "insufficient",
        "perspective": perspective,
        "target_hero_id": target_hero_id,
        "mode": mode,
        "selected_action_rate": selected_rate,
        "standardized_other_action_rate": control_rate,
        "standardized_difference": selected_rate - control_rate if selected_rate is not None and control_rate is not None else None,
        "overlap_selected_actions": weight,
        "season_contributions": seasons,
        "season_direction_disagreement": len(directions - {0}) > 1,
        "contradiction": bool(selected_rate is not None and control_rate is not None and selected_rate < control_rate),
        "excluded": dict(sorted(excluded.items())),
        "denominator": "one compatible draft action",
        "claim_limit": "Descriptive continuation evidence; it does not identify coach intent or a causal effect.",
    }


def visible_relationship_evidence(
    battles: Iterable[ValidBattle], *, target_battle: ValidBattle,
    trigger_row: dict[str, Any], mode: str,
) -> list[dict[str, Any]]:
    """Describe whether A is selected more often when each visible hero is present."""
    selected_id = int(trigger_row["selected_hero_id"])
    order = int(trigger_row["bp_order"])
    visible = [
        ("own", int(hero)) for hero in trigger_row["current_team_picks"]
    ] + [
        ("opponent", int(hero)) for hero in trigger_row["current_opponent_picks"]
    ]
    if not visible:
        return []
    observations: dict[tuple[str, int], list[dict[str, Any]]] = {
        key: [] for key in visible
    }
    for battle in battles:
        if battle.league_id == target_battle.league_id and battle.match_id == target_battle.match_id:
            continue
        if mode == "as_of_target_match" and not battle.start_time < target_battle.start_time:
            continue
        row = battle.rows[order - 1] if len(battle.rows) >= order else None
        if row is None or selected_id not in {int(value) for value in row["legal_hero_ids"]}:
            continue
        chosen = int(row["selected_hero_id"]) == selected_id
        pools = {
            "own": {int(value) for value in row["current_team_picks"]},
            "opponent": {int(value) for value in row["current_opponent_picks"]},
        }
        for key in visible:
            observations[key].append({
                "league_id": battle.league_id,
                "match_id": battle.match_id,
                "visible": key[1] in pools[key[0]],
                "picked_later": chosen,
            })
    results = []
    for (scope, hero_id), items in observations.items():
        contributions = []
        for league_id in sorted({item["league_id"] for item in items}):
            local = [item for item in items if item["league_id"] == league_id]
            present = _summary([item for item in local if item["visible"]])
            absent = _summary([item for item in local if not item["visible"]])
            contributions.append({
                "league_id": league_id,
                "visible": present,
                "not_visible": absent,
                "difference": present["rate"] - absent["rate"] if present["rate"] is not None and absent["rate"] is not None else None,
                "overlap": bool(present["actions"] and absent["actions"]),
            })
        overlap = [item for item in contributions if item["overlap"]]
        weight = sum(item["visible"]["actions"] for item in overlap)
        visible_rate = sum(item["visible"]["picked_later"] for item in overlap) / weight if weight else None
        baseline = sum(item["visible"]["actions"] * item["not_visible"]["rate"] for item in overlap) / weight if weight else None
        directions = {1 if item["difference"] > 0 else -1 if item["difference"] < 0 else 0 for item in overlap}
        results.append({
            "kind": "visible_board_association",
            "status": "descriptive" if weight else "insufficient",
            "visible_scope": scope,
            "visible_hero_id": hero_id,
            "selected_action_rate": visible_rate,
            "standardized_without_visible_hero_rate": baseline,
            "standardized_difference": visible_rate - baseline if visible_rate is not None and baseline is not None else None,
            "overlap_visible_actions": weight,
            "season_contributions": contributions,
            "season_direction_disagreement": len(directions - {0}) > 1,
            "denominator": "one compatible draft action where the selected hero was legal",
            "claim_limit": "The visible hero and action co-occur historically; this does not establish synergy, counter strength, or intent.",
        })
    return results
