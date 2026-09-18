from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

try:
    from analysis.draft_evidence import SCHEMA_VERSION
    from analysis.draft_evidence.corpus import ValidBattle, continuation_evidence, corpus_id, load_corpus, visible_relationship_evidence
except ModuleNotFoundError:  # Direct execution from the analysis directory.
    from draft_evidence import SCHEMA_VERSION
    from draft_evidence.corpus import ValidBattle, continuation_evidence, corpus_id, load_corpus, visible_relationship_evidence


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _hero_name(rows: tuple[dict[str, Any], ...], hero_id: int) -> str:
    for row in rows:
        if int(row["selected_hero_id"]) == hero_id:
            return str(row.get("selected_hero_name") or hero_id)
    return str(hero_id)


def _candidate_targets(
    candidates: list[ValidBattle], battle: ValidBattle, trigger: dict[str, Any], mode: str
) -> list[tuple[str, int]]:
    """Choose targets from the evidence corpus, never the inspected suffix."""
    order = int(trigger["bp_order"])
    values: list[tuple[str, int]] = []
    for perspective in ("own", "opponent"):
        counts = Counter()
        for candidate in candidates:
            if candidate.league_id == battle.league_id and candidate.match_id == battle.match_id:
                continue
            if mode == "as_of_target_match" and not candidate.start_time < battle.start_time:
                continue
            candidate_row = candidate.rows[order - 1]
            selected_id = int(trigger["selected_hero_id"])
            if selected_id not in {int(value) for value in candidate_row["legal_hero_ids"]}:
                continue
            team_id = str(candidate_row["acting_team_id"] if perspective == "own" else candidate_row["opponent_team_id"])
            for later in candidate.rows[order:]:
                if later["action"] == "pick" and str(later["acting_team_id"]) == team_id:
                    target_id = int(later["selected_hero_id"])
                    if target_id != selected_id:
                        counts[target_id] += 1
        values.extend((perspective, hero_id) for hero_id, _ in counts.most_common(1))
    return values


def _target_state(row: dict[str, Any], hero_id: int, perspective: str) -> str:
    if hero_id in {int(value) for value in row["all_current_bans"]}:
        return "already_banned"
    own = {int(value) for value in row["current_team_picks"]}
    enemy = {int(value) for value in row["current_opponent_picks"]}
    if hero_id in own:
        return "already_own_pick" if perspective == "own" else "already_opponent_pick"
    if hero_id in enemy:
        return "already_opponent_pick" if perspective == "own" else "already_own_pick"
    return "available_or_inferred" if hero_id in {int(value) for value in row["legal_hero_ids"]} else "availability_unknown"


def _comparison_key(battle: ValidBattle, row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        battle.schedule, str(row["action"]), str(row["side"]), int(row["bp_order"]),
        int(row.get("battle_seq") or 0), len(row["current_team_picks"]),
        len(row["current_opponent_picks"]),
    )


def move_dossier(
    corpus: list[ValidBattle], battle: ValidBattle, row: dict[str, Any], mode: str,
    *, comparison_index: dict[tuple[Any, ...], list[ValidBattle]] | None = None,
    evidence_cache: dict[tuple[Any, ...], dict[str, Any]] | None = None,
    candidate_cache: dict[tuple[Any, ...], list[tuple[str, int]]] | None = None,
) -> dict[str, Any]:
    selected = int(row["selected_hero_id"])
    evidence = []
    if row["action"] == "ban":
        evidence.append({
            "kind": "direct_access_removal",
            "status": "structural",
            "target_hero_id": selected,
            "affected_side": "both",
            "fact": "The selected hero cannot be picked later in this battle.",
        })
    candidates = (
        comparison_index.get(_comparison_key(battle, row), [])
        if comparison_index is not None else corpus
    )
    candidate_key = (
        battle.league_id, battle.match_id, *_comparison_key(battle, row),
        int(row["selected_hero_id"]), mode,
    )
    targets = candidate_cache.get(candidate_key) if candidate_cache is not None else None
    if targets is None:
        targets = _candidate_targets(candidates, battle, row, mode)
        if candidate_cache is not None:
            candidate_cache[candidate_key] = targets
    for perspective, target_id in targets:
        cache_key = (
            battle.league_id, battle.match_id, *_comparison_key(battle, row),
            int(row["selected_hero_id"]), target_id, perspective, mode,
        )
        cached = evidence_cache.get(cache_key) if evidence_cache is not None else None
        if cached is None:
            cached = continuation_evidence(
                candidates,
                target_battle=battle,
                trigger_row=row,
                target_hero_id=target_id,
                perspective=perspective,
                mode=mode,
            )
            if evidence_cache is not None:
                evidence_cache[cache_key] = cached
        item = dict(cached)
        item["target_hero_name"] = _hero_name(battle.rows, target_id)
        item["target_state_before_action"] = _target_state(row, target_id, perspective)
        evidence.append(item)
    for item in visible_relationship_evidence(
        candidates, target_battle=battle, trigger_row=row, mode=mode
    ):
        item["visible_hero_name"] = _hero_name(battle.rows, item["visible_hero_id"])
        evidence.append(item)
    visible = []
    for scope, ids in (("own", row["current_team_picks"]), ("opponent", row["current_opponent_picks"])):
        for hero_id in ids:
            visible.append({"scope": scope, "hero_id": int(hero_id), "hero_name": _hero_name(battle.rows, int(hero_id))})
    return {
        "schema_version": SCHEMA_VERSION,
        "move_id": f'{battle.league_id}:{battle.match_id}:{battle.battle_id}:{int(row["bp_order"])}',
        "league_id": battle.league_id,
        "match_id": battle.match_id,
        "battle_id": battle.battle_id,
        "battle_seq": int(row.get("battle_seq") or 0),
        "match_start_time": battle.start_time.isoformat(),
        "schedule": battle.schedule,
        "bp_order": int(row["bp_order"]),
        "action": row["action"],
        "side": row["side"],
        "acting_team_id": str(row["acting_team_id"]),
        "opponent_team_id": str(row["opponent_team_id"]),
        "selected_hero_id": selected,
        "selected_hero_name": str(row.get("selected_hero_name") or selected),
        "pre_action_board": {
            "bans": [int(value) for value in row["all_current_bans"]],
            "picks": [int(value) for value in row["all_current_picks"]],
            "own_picks": [int(value) for value in row["current_team_picks"]],
            "opponent_picks": [int(value) for value in row["current_opponent_picks"]],
            "legal_hero_count": len(row["legal_hero_ids"]),
            "availability_basis": "inferred_from_exported_legal_pool",
        },
        "visible_heroes": visible,
        "evidence": evidence,
        "candidate_selection": {
            "rule": "most_common_later_pick_per_side_in_compatible_corpus",
            "uses_inspected_match_suffix": False,
        },
        "retrospective_follow_through": [
            {"perspective": perspective, "hero_id": int(later["selected_hero_id"]), "hero_name": _hero_name(battle.rows, int(later["selected_hero_id"]))}
            for perspective, team_id in (("own", str(row["acting_team_id"])), ("opponent", str(row["opponent_team_id"])))
            for later in battle.rows
            if int(later["bp_order"]) > int(row["bp_order"])
            and later["action"] == "pick"
            and str(later["acting_team_id"]) == team_id
        ],
        "interpretation_limit": "Evidence supports or contradicts possible readings; it does not reveal the coach's private reason.",
    }


def build(
    *, exports_root: Path, output_root: Path, target_league_id: str,
    mode: str = "retrospective_all_available",
) -> dict[str, Any]:
    corpus, coverage = load_corpus(exports_root)
    if target_league_id not in {battle.league_id for battle in corpus}:
        raise ValueError(f"No validated battles for target league {target_league_id}")
    config = {"schema_version": SCHEMA_VERSION, "mode": mode, "target_match_exclusion": True}
    identifier = corpus_id(coverage, config)
    shared = output_root / "draft_evidence" / identifier
    atomic_json(shared / "coverage.json", coverage)
    atomic_json(shared / "manifest.json", {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": identifier,
        "mode": mode,
        "target_match_exclusion": True,
        "league_ids": [item["league_id"] for item in coverage["leagues"]],
        "coverage_path": "coverage.json",
        "model_used": False,
    })
    target = output_root / target_league_id / "draft_evidence"
    comparison_index: dict[tuple[Any, ...], list[ValidBattle]] = {}
    for battle in corpus:
        for row in battle.rows:
            comparison_index.setdefault(_comparison_key(battle, row), []).append(battle)
    evidence_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    candidate_cache: dict[tuple[Any, ...], list[tuple[str, int]]] = {}
    matches: dict[str, list[ValidBattle]] = {}
    for battle in corpus:
        if battle.league_id == target_league_id:
            matches.setdefault(battle.match_id, []).append(battle)
    match_index = []
    evidence_counts = Counter()
    move_count = 0
    for match_id, battles in sorted(matches.items(), key=lambda item: (item[1][0].start_time, item[0]), reverse=True):
        dossiers = []
        for battle in sorted(battles, key=lambda value: int(value.rows[0].get("battle_seq") or 0)):
            for row in battle.rows:
                dossier = move_dossier(
                    corpus, battle, row, mode,
                    comparison_index=comparison_index,
                    evidence_cache=evidence_cache,
                    candidate_cache=candidate_cache,
                )
                dossiers.append(dossier)
                move_count += 1
                evidence_counts.update(item["status"] for item in dossier["evidence"])
        shard = {"schema_version": SCHEMA_VERSION, "corpus_id": identifier, "mode": mode, "match_id": match_id, "moves": dossiers}
        atomic_json(target / "matches" / f"{match_id}.json", shard)
        match_index.append({
            "match_id": match_id,
            "start_time": battles[0].start_time.isoformat(),
            "battle_count": len(battles),
            "move_count": len(dossiers),
            "teams": sorted({str(row[key]) for battle in battles for row in battle.rows[:1] for key in ("acting_team_id", "opponent_team_id")}),
        })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": identifier,
        "mode": mode,
        "target_league_id": target_league_id,
        "included_league_ids": [item["league_id"] for item in coverage["leagues"]],
        "target_match_exclusion": True,
        "match_count": len(match_index),
        "move_count": move_count,
        "matches": match_index,
        "evidence_status_counts": dict(sorted(evidence_counts.items())),
        "model_used": False,
        "language_model_used": False,
        "limitations": [
            "All rates are descriptive associations from compatible historical draft actions.",
            "Retrospective mode may include matches played after the inspected move; the inspected match is always excluded.",
            "Hero availability is inferred from exported legal pools and is not patch-certified.",
            "No outcome, winner, player-performance, or trained-model field selects or scores evidence.",
        ],
    }
    atomic_json(target / "manifest.json", manifest)
    atomic_json(output_root / target_league_id / "draft_evidence_validation.json", {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": identifier,
        "target_match_exclusion": True,
        "move_count": move_count,
        "match_count": len(match_index),
        "evidence_status_counts": dict(sorted(evidence_counts.items())),
    })
    return manifest
