from __future__ import annotations

import datetime as dt
import sys
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any

# The documented development command runs Uvicorn from ``backend/``.  The
# evidence calculations intentionally reuse the repository-level analysis
# package, so make that package importable independently of the caller's cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis.draft_evidence.corpus import (
    ValidBattle,
    continuation_evidence,
    load_corpus,
    visible_relationship_evidence,
)
from analysis.explore_draft_intentions import STANDARD_18, STANDARD_20
from app.services.analysis_pipeline import EXPORT_ROOT


_CACHE_LOCK = Lock()
CONTINUATION_TARGETS_PER_PERSPECTIVE = 5
_CORPUS_CACHE: dict[
    Path,
    tuple[
        tuple[tuple[str, int, int], ...],
        list[ValidBattle],
        dict[str, Any],
        dict[tuple[Any, ...], list[ValidBattle]],
        dict[int, str],
    ],
] = {}


def _source_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    files = sorted(root.glob("*/bp_decisions.jsonl")) + sorted(root.glob("*/matches.jsonl"))
    return tuple(
        (str(path.relative_to(root)), path.stat().st_mtime_ns, path.stat().st_size)
        for path in files
    )


def _comparison_key(schedule: str, row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        schedule,
        str(row["action"]),
        str(row["side"]),
        int(row["bp_order"]),
        int(row.get("battle_seq") or 0),
        len(row["current_team_picks"]),
        len(row["current_opponent_picks"]),
    )


def _load_index(
    exports_root: Path,
) -> tuple[list[ValidBattle], dict[str, Any], dict[tuple[Any, ...], list[ValidBattle]], dict[int, str]]:
    root = exports_root.resolve()
    signature = _source_signature(root)
    with _CACHE_LOCK:
        cached = _CORPUS_CACHE.get(root)
        if cached and cached[0] == signature:
            return cached[1], cached[2], cached[3], cached[4]

        corpus, coverage = load_corpus(root)
        comparison_index: dict[tuple[Any, ...], list[ValidBattle]] = {}
        hero_names: dict[int, str] = {}
        for battle in corpus:
            for row in battle.rows:
                comparison_index.setdefault(_comparison_key(battle.schedule, row), []).append(battle)
                hero_names.setdefault(
                    int(row["selected_hero_id"]),
                    str(row.get("selected_hero_name") or row["selected_hero_id"]),
                )
        _CORPUS_CACHE[root] = (signature, corpus, coverage, comparison_index, hero_names)
        return corpus, coverage, comparison_index, hero_names


def _candidate_targets(
    candidates: list[ValidBattle], row: dict[str, Any], selected_hero_id: int
) -> list[tuple[str, int, int]]:
    order = int(row["bp_order"])
    values: list[tuple[str, int, int]] = []
    for perspective in ("own", "opponent"):
        counts: Counter[int] = Counter()
        for battle in candidates:
            candidate_row = battle.rows[order - 1]
            if selected_hero_id not in {int(value) for value in candidate_row["legal_hero_ids"]}:
                continue
            team_id = str(
                candidate_row["acting_team_id"]
                if perspective == "own"
                else candidate_row["opponent_team_id"]
            )
            for later in battle.rows[order:]:
                if later["action"] == "pick" and str(later["acting_team_id"]) == team_id:
                    target_id = int(later["selected_hero_id"])
                    if target_id != selected_hero_id:
                        counts[target_id] += 1
        values.extend(
            (perspective, hero_id, rank)
            for rank, (hero_id, _) in enumerate(
                counts.most_common(CONTINUATION_TARGETS_PER_PERSPECTIVE), 1
            )
        )
    return values


def _target_state(row: dict[str, Any], hero_id: int, perspective: str) -> str:
    if hero_id in {int(value) for value in row["all_current_bans"]}:
        return "already_banned"
    own = {int(value) for value in row["current_team_picks"]}
    opponent = {int(value) for value in row["current_opponent_picks"]}
    if hero_id in own:
        return "already_own_pick" if perspective == "own" else "already_opponent_pick"
    if hero_id in opponent:
        return "already_opponent_pick" if perspective == "own" else "already_own_pick"
    return "available_or_inferred" if hero_id in row["legal_hero_ids"] else "availability_unknown"


def build_simulator_move_evidence(
    state: dict[str, Any], *, exports_root: Path = EXPORT_ROOT
) -> dict[str, Any]:
    corpus, coverage, comparison_index, hero_names = _load_index(exports_root)
    resolved_hero_names = {
        **hero_names,
        **{int(hero_id): str(name) for hero_id, name in state.get("hero_names", {}).items()},
    }
    schedule = str(state["schedule"])
    order = int(state["bp_order"])
    signature = STANDARD_18 if schedule == "standard_18" else STANDARD_20
    if order > len(signature) or signature[order - 1] != (state["action"], state["side"]):
        raise ValueError("The move does not match the selected BP schedule")
    side = str(state["side"])
    acting_team_id = str(state[f"{side}_team_id"])
    opponent_side = "red" if side == "blue" else "blue"
    opponent_team_id = str(state[f"{opponent_side}_team_id"])
    current_team_picks = [int(value) for value in state[f"{side}_picks"]]
    current_opponent_picks = [int(value) for value in state[f"{opponent_side}_picks"]]
    all_picks = [int(value) for value in state["blue_picks"] + state["red_picks"]]
    all_bans = [int(value) for value in state["blue_bans"] + state["red_bans"]]
    occupied = set(all_picks + all_bans)
    legal_ids = [int(value) for value in state["available_hero_ids"] if int(value) not in occupied]
    selected_id = int(state["selected_hero_id"])
    if selected_id not in legal_ids:
        raise ValueError("The selected hero is not available on the pre-move board")

    row = {
        "match_id": "simulator",
        "battle_id": "simulator",
        "battle_seq": int(state["battle_seq"]),
        "bp_order": order,
        "action": str(state["action"]),
        "side": side,
        "acting_team_id": acting_team_id,
        "opponent_team_id": opponent_team_id,
        "selected_hero_id": selected_id,
        "selected_hero_name": resolved_hero_names.get(selected_id, str(selected_id)),
        "legal_hero_ids": legal_ids,
        "all_current_bans": all_bans,
        "all_current_picks": all_picks,
        "current_team_picks": current_team_picks,
        "current_opponent_picks": current_opponent_picks,
        "team_used_in_previous_battles": [int(value) for value in state[f"{side}_used_previous_battles"]],
        "opponent_used_in_previous_battles": [int(value) for value in state[f"{opponent_side}_used_previous_battles"]],
    }
    key = _comparison_key(schedule, row)
    candidates = comparison_index.get(key, [])
    synthetic = ValidBattle(
        league_id="simulator",
        match_id="simulator",
        battle_id="simulator",
        start_time=dt.datetime.max,
        schedule=schedule,
        rows=(row,),
    )

    evidence: list[dict[str, Any]] = []
    if row["action"] == "ban":
        evidence.append({
            "kind": "direct_access_removal",
            "status": "structural",
            "target_hero_id": selected_id,
            "affected_side": "both",
            "fact": "The selected hero cannot be picked later in this battle.",
        })
    for perspective, target_id, rank in _candidate_targets(candidates, row, selected_id):
        item = continuation_evidence(
            candidates,
            target_battle=synthetic,
            trigger_row=row,
            target_hero_id=target_id,
            perspective=perspective,
            mode="retrospective_all_available",
        )
        item["target_hero_name"] = resolved_hero_names.get(target_id, str(target_id))
        item["target_state_before_action"] = _target_state(row, target_id, perspective)
        item["perspective_rank"] = rank
        evidence.append(item)
    for item in visible_relationship_evidence(
        candidates,
        target_battle=synthetic,
        trigger_row=row,
        mode="retrospective_all_available",
    ):
        item["visible_hero_name"] = resolved_hero_names.get(
            int(item["visible_hero_id"]), str(item["visible_hero_id"])
        )
        evidence.append(item)

    return {
        "schema_version": 1,
        "mode": "retrospective_all_available",
        "source_league_ids": [item["league_id"] for item in coverage["leagues"]],
        "model_used": False,
        "language_model_used": False,
        "move": {
            "battle_seq": int(state["battle_seq"]),
            "bp_order": order,
            "action": row["action"],
            "side": side,
            "selected_hero_id": selected_id,
            "selected_hero_name": row["selected_hero_name"],
            "acting_team_id": acting_team_id,
            "opponent_team_id": opponent_team_id,
            "pre_action_board": {
                "bans": all_bans,
                "picks": all_picks,
                "own_picks": current_team_picks,
                "opponent_picks": current_opponent_picks,
                "legal_hero_count": len(legal_ids),
                "availability_basis": "simulator_season_pool_and_pre_action_board",
                "hero_names": {
                    str(hero_id): resolved_hero_names.get(hero_id, str(hero_id))
                    for hero_id in all_bans + all_picks
                },
            },
            "evidence": evidence,
        },
        "comparison_context": {
            "schedule": schedule,
            "compatible_battles": len(candidates),
            "target_match_exclusion": "not_applicable_to_synthetic_move",
        },
        "interpretation_limit": "Evidence supports or contradicts possible readings; it does not reveal the coach's private reason.",
    }


def clear_draft_evidence_cache() -> None:
    with _CACHE_LOCK:
        _CORPUS_CACHE.clear()
