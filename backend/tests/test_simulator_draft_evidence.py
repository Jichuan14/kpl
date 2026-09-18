import json
from collections import Counter

from analysis.explore_draft_intentions import STANDARD_18
from app.services.draft_evidence import build_simulator_move_evidence, clear_draft_evidence_cache


def _match(match_id, when):
    return {"match_id": match_id, "start_time": when}


def _battle(match_id, battle_id, *, chosen):
    rows = []
    bans = []
    picks = []
    picks_by_team = {"A": [], "B": []}
    for order, (action, side) in enumerate(STANDARD_18, 1):
        team, opponent = (("A", "B") if side == "blue" else ("B", "A"))
        hero_id = chosen if order == 1 else 100 + order
        row = {
            "match_id": match_id,
            "battle_id": battle_id,
            "battle_seq": 1,
            "bp_order": order,
            "action": action,
            "side": side,
            "acting_team_id": team,
            "opponent_team_id": opponent,
            "selected_hero_id": hero_id,
            "selected_hero_name": str(hero_id),
            "quality_flags": [],
            "legal_hero_ids": [hero_id, 1, 2, 105, 106],
            "all_current_bans": list(bans),
            "all_current_picks": list(picks),
            "current_team_picks": list(picks_by_team[team]),
            "current_opponent_picks": list(picks_by_team[opponent]),
            "team_used_in_previous_battles": [],
            "opponent_used_in_previous_battles": [],
        }
        rows.append(row)
        if action == "ban":
            bans.append(hero_id)
        else:
            picks.append(hero_id)
            picks_by_team[team].append(hero_id)
    return rows


def _write_season(root, league_id, rows, matches):
    directory = root / league_id
    directory.mkdir(parents=True)
    for name, records in (("bp_decisions", rows), ("matches", matches)):
        (directory / f"{name}.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )


def test_simulator_move_uses_all_seasons_without_a_model(tmp_path):
    exports = tmp_path / "exports"
    _write_season(exports, "s1", _battle("m1", "b1", chosen=1), [_match("m1", "2026-01-01")])
    _write_season(exports, "s2", _battle("m2", "b2", chosen=2), [_match("m2", "2026-02-01")])
    clear_draft_evidence_cache()

    payload = build_simulator_move_evidence(
        {
            "schedule": "standard_18",
            "battle_seq": 1,
            "bp_order": 1,
            "action": "ban",
            "side": "blue",
            "selected_hero_id": 1,
            "blue_team_id": "blue",
            "red_team_id": "red",
            "blue_picks": [],
            "red_picks": [],
            "blue_bans": [],
            "red_bans": [],
            "blue_used_previous_battles": [],
            "red_used_previous_battles": [],
            "available_hero_ids": [1, 2, 105, 106],
        },
        exports_root=exports,
    )

    assert payload["source_league_ids"] == ["s1", "s2"]
    assert payload["model_used"] is False
    assert payload["language_model_used"] is False
    assert payload["move"]["evidence"][0]["status"] == "structural"
    continuation = next(
        item
        for item in payload["move"]["evidence"]
        if item["kind"] == "continuation_association" and item["perspective"] == "own"
    )
    assert {item["league_id"] for item in continuation["season_contributions"]} == {"s1", "s2"}
    continuations = [
        item for item in payload["move"]["evidence"]
        if item["kind"] == "continuation_association"
    ]
    assert Counter(item["perspective"] for item in continuations) == {"own": 5, "opponent": 5}
    assert {item["perspective_rank"] for item in continuations if item["perspective"] == "own"} == {1, 2, 3, 4, 5}
    assert payload["comparison_context"]["target_match_exclusion"] == "not_applicable_to_synthetic_move"


def test_simulator_move_rejects_a_slot_that_does_not_match_schedule(tmp_path):
    exports = tmp_path / "exports"
    _write_season(exports, "s1", _battle("m1", "b1", chosen=1), [_match("m1", "2026-01-01")])
    clear_draft_evidence_cache()
    state = {
        "schedule": "standard_18",
        "battle_seq": 1,
        "bp_order": 1,
        "action": "pick",
        "side": "blue",
        "selected_hero_id": 1,
        "blue_team_id": "blue",
        "red_team_id": "red",
        "blue_picks": [],
        "red_picks": [],
        "blue_bans": [],
        "red_bans": [],
        "blue_used_previous_battles": [],
        "red_used_previous_battles": [],
        "available_hero_ids": [1, 2],
    }
    try:
        build_simulator_move_evidence(state, exports_root=exports)
    except ValueError as exc:
        assert "schedule" in str(exc)
    else:
        raise AssertionError("expected schedule validation")
