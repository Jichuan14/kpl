import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from explore_draft_intentions import (
    STANDARD_18, STANDARD_20, analyze, main, rate_summary, source_sha256, target_pick_legal,
)
from run_intention_case_study import describe_action_context, report_text


def match(match_id, when="2026-01-01 12:00:00"):
    return {"match_id": match_id, "start_time": when}


def battle(match_id, battle_id, *, chosen=1, target=99, target_order=16, flagged=False,
           battle_seq=1, signature=STANDARD_18):
    """A fully populated normal 18-action battle with five picks per side."""
    rows, bans, picks = [], [], []
    picks_by_team = {"A": [], "B": []}
    selected = {i: 100 + i for i in range(1, len(signature) + 1)}
    selected[1] = chosen
    if target_order:
        selected[target_order] = target
    for order, (action, side) in enumerate(signature, 1):
        team, opponent = ("A", "B") if side == "blue" else ("B", "A")
        hero = selected[order]
        rows.append({"match_id": match_id, "battle_id": battle_id, "battle_seq": battle_seq,
                     "bp_order": order, "action": action, "side": side,
                     "acting_team_id": team, "opponent_team_id": opponent,
                     "selected_hero_id": hero, "selected_hero_name": str(hero),
                     "quality_flags": ["bad"] if flagged else [],
                     "legal_hero_ids": [hero, target, 1, 2, 3],
                     "all_current_bans": list(bans), "all_current_picks": list(picks),
                     "team_used_in_previous_battles": [], "opponent_used_in_previous_battles": [],
                     "current_team_picks": list(picks_by_team[team]), "current_opponent_picks": list(picks_by_team[opponent])})
        (bans if action == "ban" else picks).append(hero)
        if action == "pick":
            picks_by_team[team].append(hero)
    return rows


def run(rows, matches, **kwargs):
    return analyze(rows, matches, action="ban", hero_id=1, target_id=99, bp_order=1, **kwargs)


def test_target_is_legal_for_the_relevant_side_before_trigger():
    rows = battle("m1", "b1") + battle("m2", "b2", chosen=2)
    # A used target before in this match: own target must be excluded.
    rows[0]["team_used_in_previous_battles"] = [99]
    result = run(rows, [match("m1"), match("m2")])
    assert result["coverage"]["eligible_triggers"] == 1
    assert result["coverage"]["excluded_triggers"]["target_not_pick_legal_pre_action"] == 1


def test_opponent_pre_action_legality_uses_opponent_global_bp_history():
    rows = battle("m1", "b1") + battle("m2", "b2", chosen=2)
    rows[0]["opponent_used_in_previous_battles"] = [99]
    result = run(rows, [match("m1"), match("m2")], perspective="opponent")
    assert result["coverage"]["eligible_triggers"] == 1


def test_later_banned_target_is_retained_as_zero_outcome():
    exposed = battle("m1", "b1", target_order=11)  # order 11 is a later ban
    control = battle("m2", "b2", chosen=2, target_order=15)
    result = run(exposed + control, [match("m1"), match("m2")])
    assert result["coverage"]["exposed"]["battles"] == 1
    assert result["coverage"]["exposed"]["picked_later"] == 0
    assert result["evidence_examples"]["exposed"][0]["suffix_outcome"] == "later_banned"


def test_malformed_schedule_is_excluded():
    first = battle("m1", "b1")
    second = battle("m2", "b2", chosen=2)
    # Swapping sides' first orders creates an unsupported schedule.
    second[0]["bp_order"] = 2
    second[1]["bp_order"] = 1
    result = run(first + second, [match("m1"), match("m2")])
    assert result["association"]["overlap_exposed_triggers"] == 0
    assert result["association"]["standardized_delta"] is None


def test_cutoff_excludes_target_match_and_future_by_timestamp():
    early = battle("early", "b1")
    target = battle("target", "b2", chosen=2)
    future = battle("future", "b3", chosen=2)
    result = run(early + target + future, [match("early", "2026-01-01 10:00:00"), match("target", "2026-01-02 10:00:00"), match("future", "2026-01-03 10:00:00")], before_match_id="target")
    assert result["coverage"]["eligible_triggers"] == 1
    assert result["coverage"]["excluded_battles"]["chronological_cutoff"] == 2


def test_flagged_or_incomplete_battle_is_excluded():
    good = battle("m1", "b1")
    flagged = battle("m2", "b2", chosen=2, flagged=True)
    incomplete = battle("m3", "b3", chosen=2)[:-1]
    result = run(good + flagged + incomplete, [match("m1"), match("m2"), match("m3")])
    assert result["coverage"]["excluded_battles"]["quality_flags"] == 1
    assert result["coverage"]["excluded_battles"]["incomplete_picks"] == 1


def test_zero_arm_has_null_rates_and_no_fake_delta():
    result = run(battle("m1", "b1"), [match("m1")])
    assert result["coverage"]["controls"]["rate"] is None
    assert result["association"]["raw_delta"] is None
    assert result["status"] == "insufficient"


def test_same_hero_target_is_explicit_structural_direct_denial():
    rows = battle("m1", "b1", chosen=1, target=1, target_order=None) + battle("m2", "b2", chosen=2, target=1, target_order=None)
    result = analyze(rows, [match("m1"), match("m2")], action="ban", hero_id=1, target_id=1, perspective="opponent", bp_order=1)
    assert result["status"] == "structural"


def test_target_selected_by_control_at_trigger_is_excluded_but_later_outcomes_are_not():
    exposed = battle("m1", "b1")
    bad_control = battle("m2", "b2", chosen=99, target_order=None)
    ordinary_control = battle("m3", "b3", chosen=2)
    result = run(exposed + bad_control + ordinary_control, [match("m1"), match("m2"), match("m3")])
    assert result["coverage"]["controls"]["battles"] == 1
    assert result["coverage"]["excluded_triggers"]["control_selected_target_at_trigger"] == 1


def test_one_trigger_has_one_denominator_despite_many_later_decisions():
    result = run(battle("m1", "b1") + battle("m2", "b2", chosen=2), [match("m1"), match("m2")])
    assert result["coverage"]["eligible_triggers"] == 2
    assert result["coverage"]["exposed"]["battles"] == 1


def test_hero_a_must_be_an_available_alternative_for_controls():
    exposed = battle("m1", "b1")
    control = battle("m2", "b2", chosen=2)
    control[0]["legal_hero_ids"] = [2, 99]
    result = run(exposed + control, [match("m1"), match("m2")])
    assert result["coverage"]["controls"]["triggers"] == 0
    assert result["coverage"]["excluded_triggers"]["hero_not_legal_at_trigger"] == 1


def test_max_order_gap_requires_a_pick_opportunity_inside_horizon():
    result = run(battle("m1", "b1") + battle("m2", "b2", chosen=2), [match("m1"), match("m2")], max_order_gap=1)
    assert result["coverage"]["eligible_triggers"] == 0
    assert result["coverage"]["excluded_triggers"]["no_future_pick_opportunity"] == 2


def test_standardization_uses_exposed_stratum_weights_not_raw_pooling():
    # Stratum 1: exposed and control succeed. Stratum 2: three exposed
    # and one control all fail. The raw control pool is 50%, but after
    # exposed weighting its expected rate is 25%, matching exposed's 25%.
    rows = battle("e1", "b1", battle_seq=1) + battle("c1", "b2", chosen=2)
    rows += battle("e2", "b3", battle_seq=2, target_order=None) + battle("e3", "b4", battle_seq=2, target_order=None) + battle("e4", "b5", battle_seq=2, target_order=None)
    rows += battle("c2", "b6", chosen=2, battle_seq=2, target_order=None)
    matches = [match(x) for x in ("e1", "c1", "e2", "e3", "e4", "c2")]
    result = run(rows, matches)
    assert result["association"]["raw_delta"] == -0.25
    assert result["association"]["standardized_delta"] == 0.0


def test_rate_summary_reports_unique_battles_separately_from_triggers():
    items = [{"match_id": "m", "battle_id": "b", "picked_later": True}, {"match_id": "m", "battle_id": "b", "picked_later": False}]
    summary = rate_summary(items)
    assert summary["triggers"] == 2
    assert summary["battles"] == summary["matches"] == 1


def test_positive_ids_and_horizon_are_validated():
    try:
        analyze([], [], action="ban", hero_id=0, target_id=1)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected positive ID validation")
    try:
        analyze([], [], action="ban", hero_id=1, target_id=2, max_order_gap=0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected positive horizon validation")


def test_two_valid_rule_formats_do_not_share_a_comparison():
    first = battle("m1", "b1", signature=STANDARD_18)
    second = battle("m2", "b2", chosen=2, signature=STANDARD_20, target_order=18)
    result = run(first + second, [match("m1"), match("m2")])
    assert result["coverage"]["eligible_triggers"] == 2
    assert result["association"]["overlap_exposed_triggers"] == 0
    assert result["status"] == "insufficient"


def test_missing_opponent_history_is_not_an_empty_pool():
    first = battle("m1", "b1")
    del first[0]["opponent_used_in_previous_battles"]
    result = run(first + battle("m2", "b2", chosen=2), [match("m1"), match("m2")])
    assert result["coverage"]["excluded_battles"]["missing_prestate"] == 1


def test_inconsistent_prefix_cannot_supply_an_outcome():
    first = battle("m1", "b1")
    first[5]["all_current_bans"] = []
    result = run(first + battle("m2", "b2", chosen=2), [match("m1"), match("m2")])
    assert result["coverage"]["excluded_battles"]["inconsistent_prestate_prefix"] == 1


def test_cutoff_excludes_concurrent_matches_and_all_games_of_target():
    rows = battle("early", "e") + battle("target", "t1")
    rows += battle("target", "t2", battle_seq=2) + battle("concurrent", "c")
    times = [match("early", "2026-01-01"), match("target", "2026-01-02"), match("concurrent", "2026-01-02")]
    result = run(rows, times, before_match_id="target")
    assert result["coverage"]["exposed"]["triggers"] == 1
    assert result["coverage"]["excluded_battles"]["chronological_cutoff"] == 3


def test_late_target_pick_is_a_failure_at_shorter_horizon():
    rows = battle("m1", "b1") + battle("m2", "b2", chosen=2)
    # Blue does have a pick at order 5, but B is taken at order 16.
    result = run(rows, [match("m1"), match("m2")], max_order_gap=4)
    assert result["coverage"]["eligible_triggers"] == 2
    assert result["coverage"]["exposed"]["picked_later"] == 0
    assert result["evidence_examples"]["exposed"][0]["suffix_outcome"] == "not_selected_within_max_order_gap"


def test_future_hero_roster_cannot_make_past_target_eligible():
    early = battle("early", "b1", target_order=None)
    for row in early:
        row["legal_hero_ids"] = [hero for hero in row["legal_hero_ids"] if hero != 99]
    future = battle("future", "b2", chosen=2)
    result = run(early + future, [match("early", "2026-01-01"), match("future", "2026-02-01")], before_match_id="future")
    assert result["coverage"]["eligible_triggers"] == 0


def test_wrong_opponent_identity_is_rejected_even_before_picks():
    first = battle("m1", "b1")
    first[0]["opponent_team_id"] = "unrelated-team"
    result = run(first + battle("m2", "b2", chosen=2), [match("m1"), match("m2")])
    assert result["coverage"]["excluded_battles"]["inconsistent_team_side"] == 1


def test_context_inventory_includes_targets_already_removed():
    rows = battle("m1", "b1", target_order=11)
    # Hero at order 12 is banned after target B was already banned at 11.
    result = describe_action_context(rows, [match("m1")], action="ban", hero_id=112, target_id=99, perspective="opponent")
    assert result["observed_action_count"] == 1
    assert result["target_state_counts"] == {"already_banned": 1}
    assert len(result["examples_by_state"]["already_banned"]["known_prefix"]) == 11


def test_context_inventory_distinguishes_visible_opponent_target():
    rows = battle("m1", "b1", target_order=5)
    result = describe_action_context(rows, [match("m1")], action="ban", hero_id=111, target_id=99, perspective="opponent")
    assert result["target_state_counts"] == {"already_opponent_pick": 1}


def test_report_handles_missing_comparators_without_fake_zero_rate():
    rows, times = battle("m1", "b1"), [match("m1")]
    result = run(rows, times)
    result.update(league_id="test", case_label="Example")
    result["observed_action_context"] = describe_action_context(rows, times, action="ban", hero_id=1, target_id=99, perspective="own")
    report = report_text([result])
    assert "unavailable" in report
    assert "| test | Example |" in report


def test_conflicting_match_timestamps_are_rejected():
    with pytest.raises(ValueError, match="duplicate match ID"):
        run(battle("m1", "b1"), [match("m1", "2026-01-01"), match("m1", "2026-02-01")])


def test_invalid_suffix_action_cannot_be_used_as_evidence():
    first = battle("m1", "b1")
    first[-1]["legal_hero_ids"] = []
    result = run(first + battle("m2", "b2", chosen=2), [match("m1"), match("m2")])
    assert result["coverage"]["excluded_battles"]["selected_hero_outside_legal_pool"] == 1


def test_opponent_can_pick_a_hero_used_previously_by_acting_team():
    row = battle("m1", "b1")[4]  # blue's first pick
    row["legal_hero_ids"] = [105]
    row["team_used_in_previous_battles"] = [99]
    row["opponent_used_in_previous_battles"] = []
    assert target_pick_legal(row, 99, "B")
    assert not target_pick_legal(row, 99, "A")


def test_winner_fields_never_change_the_research_result():
    rows = battle("m1", "b1") + battle("m2", "b2", chosen=2)
    times = [match("m1"), match("m2")]
    before = run(rows, times)
    for row in rows:
        row["acting_team_won_battle"] = True
        row["battle_winner_team_id"] = row["acting_team_id"]
    assert run(rows, times) == before


def test_cli_writes_source_fingerprints_and_a_readable_report(tmp_path):
    root = tmp_path / "exports" / "sample"
    root.mkdir(parents=True)
    rows = battle("m1", "b1") + battle("m2", "b2", chosen=2)
    times = [match("m1"), match("m2")]
    for name, records in (("bp_decisions", rows), ("matches", times)):
        (root / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8",
        )
    output, report = tmp_path / "evidence.json", tmp_path / "report.md"
    assert main([
        "--league-id", "sample", "--exports-root", str(root.parent),
        "--action", "ban", "--hero-id", "1", "--target-id", "99", "--bp-order", "1",
        "--output", str(output), "--report", str(report),
    ]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["model_used"] is False
    assert payload["coverage"]["eligible_triggers"] == 2
    assert payload["sources"]["bp_decisions_sha256"] == source_sha256(root / "bp_decisions.jsonl")
    assert "percentage points" in report.read_text(encoding="utf-8")
