import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from draft_evidence.builder import build
from draft_evidence.corpus import continuation_evidence, load_corpus
from test_draft_intentions import STANDARD_18, battle, match


def write_season(root: Path, league_id: str, decisions, matches):
    directory = root / league_id
    directory.mkdir(parents=True)
    for name, rows in (("bp_decisions", decisions), ("matches", matches)):
        (directory / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )


def test_all_seasons_contribute_but_entire_target_match_is_excluded(tmp_path):
    exports = tmp_path / "exports"
    output = tmp_path / "outputs"
    target_rows = battle("target", "t1", target_order=16) + battle("target", "t2", target_order=16, battle_seq=2)
    past_rows = battle("past", "p", target_order=16)
    future_rows = battle("future", "f", chosen=2, target_order=None)
    # The fixture's compact legal pools otherwise omit the later order-5
    # candidate (105) from order 1. Make the counterfactual legal at the
    # trigger in both supporting seasons.
    for rows in (past_rows, future_rows):
        rows[0]["legal_hero_ids"].append(105)
    write_season(exports, "s1", target_rows, [match("target", "2026-02-01")])
    write_season(exports, "s2", past_rows, [match("past", "2026-01-01")])
    write_season(exports, "s3", future_rows, [match("future", "2026-03-01")])

    manifest = build(exports_root=exports, output_root=output, target_league_id="s1")
    assert manifest["included_league_ids"] == ["s1", "s2", "s3"]
    assert manifest["target_match_exclusion"] is True
    shard = json.loads((output / "s1/draft_evidence/matches/target.json").read_text())
    item = next(e for e in shard["moves"][0]["evidence"] if e.get("target_hero_id") == 105)
    assert item["excluded"]["target_match"] == 1
    assert all(row["league_id"] != "s1" for row in item["season_contributions"])
    assert {row["league_id"] for row in item["season_contributions"]} == {"s2", "s3"}


def test_as_of_mode_excludes_future_matches(tmp_path):
    exports = tmp_path / "exports"
    write_season(exports, "target", battle("m", "b"), [match("m", "2026-02-01")])
    write_season(exports, "past", battle("p", "b"), [match("p", "2026-01-01")])
    write_season(exports, "future", battle("f", "b"), [match("f", "2026-03-01")])
    corpus, _ = load_corpus(exports)
    target = next(item for item in corpus if item.league_id == "target")
    evidence = continuation_evidence(
        corpus, target_battle=target, trigger_row=target.rows[0], target_hero_id=99,
        perspective="own", mode="as_of_target_match",
    )
    assert {item["league_id"] for item in evidence["season_contributions"]} == {"past"}
    assert evidence["excluded"]["not_before_target"] == 1


def test_exact_hero_ids_are_not_merged(tmp_path):
    exports = tmp_path / "exports"
    rows = battle("m", "b", target=581, target_order=16)
    write_season(exports, "s", rows, [match("m")])
    manifest = build(exports_root=exports, output_root=tmp_path / "out", target_league_id="s")
    shard = json.loads((tmp_path / "out/s/draft_evidence/matches/m.json").read_text())
    selected_ids = {move["selected_hero_id"] for move in shard["moves"]}
    assert 581 in selected_ids
    assert 585 not in selected_ids
    assert manifest["model_used"] is False


def test_unknown_schedule_is_reported_in_corpus_coverage(tmp_path):
    exports = tmp_path / "exports"
    malformed = battle("bad", "b", signature=STANDARD_18)
    malformed[0]["side"] = "red"
    write_season(exports, "bad", malformed, [match("bad")])
    write_season(exports, "good", battle("ok", "b"), [match("ok")])
    _, coverage = load_corpus(exports)
    bad = next(item for item in coverage["leagues"] if item["league_id"] == "bad")
    assert bad["excluded_battles"]["unknown_schedule"] == 1


def test_visible_board_relationship_is_descriptive_and_season_scoped(tmp_path):
    exports = tmp_path / "exports"
    write_season(exports, "target", battle("t", "b"), [match("t")])
    write_season(exports, "history", battle("h", "b", chosen=2), [match("h")])
    output = tmp_path / "out"
    build(exports_root=exports, output_root=output, target_league_id="target")
    shard = json.loads((output / "target/draft_evidence/matches/t.json").read_text())
    # By order 6, blue's order-5 pick is visible to red.
    move = next(item for item in shard["moves"] if item["bp_order"] == 6)
    visible = [item for item in move["evidence"] if item["kind"] == "visible_board_association"]
    assert visible
    assert visible[0]["claim_limit"].startswith("The visible hero")
    assert all("intent" in item["claim_limit"] for item in visible)
