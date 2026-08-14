"""Deterministic strategic constraints shared by training and inference."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


LANES = ("clash", "mid", "jungle", "farm", "roam")
SECOND_BAN_ORDERS = frozenset(range(11, 17))
STRATEGIC_CONSTRAINT_VERSION = 2


def hero_lane_profiles(
    artifact: Mapping[str, Any],
) -> tuple[dict[int, int], frozenset[int]]:
    """Parse a versioned lane-profile artifact into masks and eligibility."""
    if (
        artifact.get("schema_version") != 1
        or artifact.get("artifact_type") != "hero_lane_profiles"
    ):
        raise ValueError("Unsupported hero lane profile artifact")
    lanes = tuple(str(lane) for lane in artifact.get("lanes", []))
    if lanes != LANES:
        raise ValueError("Hero lane profiles do not define the five canonical lanes")
    lane_bits = {lane: 1 << index for index, lane in enumerate(lanes)}
    masks: dict[int, int] = {}
    eligible: set[int] = set()
    for row in artifact.get("rows", []):
        hero_id = int(row["hero_id"])
        if hero_id in masks:
            raise ValueError(f"Duplicate hero lane profile: {hero_id}")
        row_lanes = [str(lane) for lane in row.get("lanes", [])]
        if len(row_lanes) != len(set(row_lanes)) or any(
            lane not in lane_bits for lane in row_lanes
        ):
            raise ValueError(f"Invalid lanes for hero {hero_id}")
        mask = sum(lane_bits[lane] for lane in row_lanes)
        masks[hero_id] = mask
        if bool(row.get("constraint_eligible")):
            if mask == 0 or mask & (mask - 1):
                raise ValueError(f"Constraint-eligible hero {hero_id} is not single-lane")
            eligible.add(hero_id)
    return masks, frozenset(eligible)


def second_ban_lane_conflicts(
    *,
    action: str,
    bp_order: int,
    opponent_pick_ids: Iterable[int],
    candidate_ids: Iterable[int],
    lane_masks: Mapping[int, int],
    constraint_eligible_ids: frozenset[int] | set[int],
) -> set[int]:
    """Return strategically redundant single-lane bans in all five lanes.

    A lane is locked only by an opponent pick confidently classified as
    single-lane. A candidate is removed only when it is also confidently
    single-lane in a locked lane. Flexible and uncertain heroes are untouched.
    """
    if action != "ban" or int(bp_order) not in SECOND_BAN_ORDERS:
        return set()
    eligible = constraint_eligible_ids
    locked_lanes = 0
    for hero_id_value in opponent_pick_ids:
        hero_id = int(hero_id_value)
        if hero_id in eligible:
            locked_lanes |= lane_masks.get(hero_id, 0)
    if not locked_lanes:
        return set()
    return {
        int(hero_id_value)
        for hero_id_value in candidate_ids
        if (
            int(hero_id_value) in eligible
            and lane_masks.get(int(hero_id_value), 0) & locked_lanes
        )
    }
