"""Deterministic strategic constraints shared by training and inference."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


LANE_FEATURE_PREFIX = "lane__"
FARM_LANE_FEATURE = "lane__farm"
SECOND_BAN_ORDERS = frozenset(range(11, 17))
STRATEGIC_CONSTRAINT_VERSION = 1


def hero_lane_masks(
    hero_ids: Sequence[int],
    feature_names: Sequence[str],
    feature_rows: Sequence[Sequence[float]],
) -> dict[int, int]:
    """Build compact canonical-lane masks from the draft feature artifact."""
    if len(hero_ids) != len(feature_rows):
        raise ValueError("Hero ids and feature rows must have matching lengths")
    lane_indices = [
        index
        for index, feature_name in enumerate(feature_names)
        if str(feature_name).startswith(LANE_FEATURE_PREFIX)
    ]
    if FARM_LANE_FEATURE not in feature_names:
        raise ValueError("Draft features do not define the farm lane")
    if any(len(row) != len(feature_names) for row in feature_rows):
        raise ValueError("Draft feature rows have inconsistent widths")
    return {
        int(hero_id): sum(
            1 << lane_index
            for lane_index, feature_index in enumerate(lane_indices)
            if float(feature_rows[row_index][feature_index]) > 0
        )
        for row_index, hero_id in enumerate(hero_ids)
    }


def second_ban_farm_conflicts(
    *,
    action: str,
    bp_order: int,
    opponent_pick_ids: Iterable[int],
    candidate_ids: Iterable[int],
    lane_masks: Mapping[int, int],
    feature_names: Sequence[str],
) -> set[int]:
    """Return farm-only bans made irrelevant by an opponent farm-only pick.

    These heroes remain legal under the game rules.  The constraint is limited
    to the second ban phase and canonical farm-only heroes because historical
    data contains real same-role bans for other lanes when picks flex between
    roles.  Candidates with any non-farm lane remain available for that reason.
    """
    if action != "ban" or int(bp_order) not in SECOND_BAN_ORDERS:
        return set()
    lane_features = [
        str(feature_name)
        for feature_name in feature_names
        if str(feature_name).startswith(LANE_FEATURE_PREFIX)
    ]
    try:
        farm_bit = 1 << lane_features.index(FARM_LANE_FEATURE)
    except ValueError as exc:
        raise ValueError("Draft features do not define the farm lane") from exc
    if not any(
        lane_masks.get(int(hero_id), 0) == farm_bit
        for hero_id in opponent_pick_ids
    ):
        return set()
    return {
        int(hero_id)
        for hero_id in candidate_ids
        if lane_masks.get(int(hero_id), 0) == farm_bit
    }
