#!/usr/bin/env python3
"""Build evidence-backed hero lane profiles for draft constraints."""

from __future__ import annotations

import hashlib
import json
import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = REPO_ROOT / "analysis"
FEATURES_PATH = ANALYSIS_DIR / "hero_draft_feature_vectors.json"
OUTPUT_PATH = ANALYSIS_DIR / "hero_lane_profiles.json"
LANES = ("clash", "mid", "jungle", "farm", "roam")
POSITION_LANES = {6: "clash", 2: "mid", 5: "jungle", 7: "farm", 4: "roam"}
MIN_SECONDARY_PICKS = 10
MIN_SECONDARY_SHARE = 0.10
MIN_SECONDARY_TEAMS = 2
MIN_SECONDARY_SEASONS = 2
SECOND_BAN_ORDERS = frozenset(range(11, 17))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_decisions(
    through_season: str | None,
) -> tuple[list[str], list[dict[str, Any]], list[Path]]:
    paths = sorted((ANALYSIS_DIR / "exports").glob("*/bp_decisions.jsonl"))
    if through_season is not None:
        paths = [path for path in paths if path.parent.name <= through_season]
    rows: list[dict[str, Any]] = []
    seasons: list[str] = []
    for path in paths:
        season = path.parent.name
        seasons.append(season)
        with path.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    row = json.loads(line)
                    row["_season"] = season
                    rows.append(row)
    return seasons, rows, paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through-season")
    args = parser.parse_args()
    feature_artifact = json.loads(FEATURES_PATH.read_text(encoding="utf-8"))
    lane_feature_indices = {
        lane: feature_artifact["feature_names"].index(f"lane__{lane}")
        for lane in LANES
    }
    canonical: dict[int, str | None] = {}
    names: dict[int, str] = {}
    for row in feature_artifact["rows"]:
        hero_id = int(row["hero_id"])
        active = [
            lane
            for lane, index in lane_feature_indices.items()
            if float(row["vector"][index]) > 0
        ]
        canonical[hero_id] = active[0] if len(active) == 1 else None
        names[hero_id] = str(row.get("hero_name") or hero_id)

    seasons, decisions, decision_paths = load_decisions(args.through_season)
    counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    teams: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    lane_seasons: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in decisions:
        if row.get("action") != "pick":
            continue
        hero_id = int(row.get("selected_hero_id") or 0)
        lane = POSITION_LANES.get(int(row.get("selected_player_position") or 0))
        if hero_id not in canonical or lane is None:
            continue
        counts[hero_id][lane] += 1
        teams[hero_id][lane].add(str(row.get("acting_team_id") or ""))
        lane_seasons[hero_id][lane].add(str(row["_season"]))

    primary_lanes: dict[int, str | None] = {}
    profile_lanes: dict[int, list[str]] = {}
    for hero_id, feature_primary in canonical.items():
        total = sum(counts[hero_id].values())
        primary = feature_primary
        if primary is None and total:
            primary = max(LANES, key=lambda lane: (counts[hero_id][lane], -LANES.index(lane)))
        primary_lanes[hero_id] = primary
        if primary is None:
            profile_lanes[hero_id] = []
            continue
        qualified = {primary}
        for lane in LANES:
            lane_count = counts[hero_id][lane]
            share = lane_count / total if total else 0.0
            if (
                lane_count >= MIN_SECONDARY_PICKS
                and share >= MIN_SECONDARY_SHARE
                and len(teams[hero_id][lane] - {""}) >= MIN_SECONDARY_TEAMS
                and len(lane_seasons[hero_id][lane]) >= MIN_SECONDARY_SEASONS
            ):
                qualified.add(lane)
        profile_lanes[hero_id] = [lane for lane in LANES if lane in qualified]

    # A real same-lane second-round ban is evidence that the candidate must not
    # be hard-removed. Keep its lane label, but explicitly mark it uncertain.
    counterexamples: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in decisions:
        if row.get("action") != "ban" or int(row.get("bp_order") or 0) not in SECOND_BAN_ORDERS:
            continue
        selected = int(row.get("selected_hero_id") or 0)
        selected_lanes = profile_lanes.get(selected, [])
        if len(selected_lanes) != 1:
            continue
        lane = selected_lanes[0]
        locking_picks = [
            int(hero_id)
            for hero_id in row.get("current_opponent_picks", [])
            if profile_lanes.get(int(hero_id)) == [lane]
        ]
        if locking_picks:
            counterexamples[selected].append(
                {
                    "season": str(row["_season"]),
                    "match_id": str(row["match_id"]),
                    "battle_id": str(row["battle_id"]),
                    "bp_order": int(row["bp_order"]),
                    "locking_opponent_pick_ids": locking_picks,
                }
            )

    artifact_rows = []
    for hero_id in sorted(canonical):
        total = sum(counts[hero_id].values())
        lanes = profile_lanes[hero_id]
        examples = counterexamples.get(hero_id, [])
        constraint_eligible = len(lanes) == 1 and not examples
        evidence = {
            lane: {
                "picks": counts[hero_id][lane],
                "share": round(counts[hero_id][lane] / total, 6) if total else 0.0,
                "teams": len(teams[hero_id][lane] - {""}),
                "seasons": len(lane_seasons[hero_id][lane]),
            }
            for lane in LANES
            if counts[hero_id][lane]
        }
        artifact_rows.append(
            {
                "hero_id": hero_id,
                "hero_name": names[hero_id],
                "primary_lane": primary_lanes[hero_id],
                "lanes": lanes,
                "classification": (
                    "unknown" if not lanes else "flex" if len(lanes) > 1 else "single_lane"
                ),
                "constraint_eligible": constraint_eligible,
                "constraint_exemption_reason": (
                    "unknown_lane" if not lanes
                    else "multi_lane" if len(lanes) > 1
                    else "observed_same_lane_second_ban" if examples
                    else None
                ),
                "observed_pick_count": total,
                "lane_evidence": evidence,
                "same_lane_second_ban_counterexamples": examples,
            }
        )

    artifact = {
        "schema_version": 1,
        "artifact_type": "hero_lane_profiles",
        "lanes": list(LANES),
        "source": {
            "feature_path": str(FEATURES_PATH.relative_to(REPO_ROOT)),
            "feature_sha256": sha256(FEATURES_PATH),
            "decision_paths": [str(path.relative_to(REPO_ROOT)) for path in decision_paths],
            "decision_sha256": {
                str(path.relative_to(REPO_ROOT)): sha256(path)
                for path in decision_paths
            },
            "seasons": seasons,
        },
        "policy": {
            "secondary_lane_minimum_picks": MIN_SECONDARY_PICKS,
            "secondary_lane_minimum_share": MIN_SECONDARY_SHARE,
            "secondary_lane_minimum_teams": MIN_SECONDARY_TEAMS,
            "secondary_lane_minimum_seasons": MIN_SECONDARY_SEASONS,
            "observed_same_lane_second_ban_exempts_candidate": True,
        },
        "rows": artifact_rows,
    }
    OUTPUT_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    flexible = sum(row["classification"] == "flex" for row in artifact_rows)
    exempt = sum(not row["constraint_eligible"] for row in artifact_rows)
    print(f"Wrote {OUTPUT_PATH}: {len(artifact_rows)} heroes, {flexible} flex, {exempt} exempt")


if __name__ == "__main__":
    main()
