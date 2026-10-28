"""Concurrency and invalidation contracts for public simulator sharing."""
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest

from app.services.simulator_analysis_cache import (
    Dependencies, FileDigests, SimulatorAnalysisCache, analysis_dependencies,
    analysis_key, default_recommendation_seed,
)


def test_single_flight_copies_and_independent_keys():
    cache = SimulatorAnalysisCache(max_entries=1)
    started, release, joined = Event(), Event(), Event()
    calls = []

    def compute():
        calls.append(1)
        started.set()
        assert release.wait(5)
        return {"rows": [1]}

    real_result = Future.result
    def wait_result(future, *args, **kwargs):
        joined.set()
        return real_result(future, *args, **kwargs)

    with ThreadPoolExecutor(3) as pool, patch.object(Future, "result", wait_result):
        leader = pool.submit(cache.get_or_compute, "same", compute, is_current=lambda: True)
        assert started.wait(5)
        follower = pool.submit(cache.get_or_compute, "same", compute, is_current=lambda: True)
        assert joined.wait(5)
        # Completed-entry eviction/clear never removes an inflight calculation.
        assert cache.get_or_compute("other", lambda: 7, is_current=lambda: True) == 7
        cache.clear()
        release.set()
        first, second = leader.result(5), follower.result(5)
    assert calls == [1]
    first["rows"].append(2)
    assert second == {"rows": [1]}
    assert cache.get_or_compute("same", lambda: pytest.fail("cache miss"), is_current=lambda: True) == second


def test_error_fans_out_and_next_call_retries():
    cache = SimulatorAnalysisCache()
    started, release, joined = Event(), Event(), Event()
    def fail():
        started.set()
        assert release.wait(5)
        raise ValueError("unavailable")
    real_result = Future.result
    def wait_result(future, *args, **kwargs):
        joined.set()
        return real_result(future, *args, **kwargs)
    with ThreadPoolExecutor(2) as pool, patch.object(Future, "result", wait_result):
        first = pool.submit(cache.get_or_compute, "key", fail, is_current=lambda: True)
        assert started.wait(5)
        second = pool.submit(cache.get_or_compute, "key", fail, is_current=lambda: True)
        assert joined.wait(5)
        release.set()
        for result in (first, second):
            with pytest.raises(ValueError, match="unavailable"):
                result.result(5)
    assert cache.get_or_compute("key", lambda: "recovered", is_current=lambda: True) == "recovered"


def test_ttl_from_completion_lru_and_midflight_invalidation():
    now = [0]
    cache = SimulatorAnalysisCache(max_entries=2, ttl_seconds=60, clock=lambda: now[0])
    def slow():
        now[0] = 100
        return [1]
    cache.get_or_compute("a", slow, is_current=lambda: True)
    now[0] = 159
    assert cache.get_or_compute("a", lambda: None, is_current=lambda: True) == [1]
    cache.get_or_compute("b", lambda: 2, is_current=lambda: True)
    cache.get_or_compute("a", lambda: None, is_current=lambda: True)
    cache.get_or_compute("c", lambda: 3, is_current=lambda: True)
    assert cache.get_or_compute("b", lambda: "evicted", is_current=lambda: True) == "evicted"
    now[0] = 160
    assert cache.get_or_compute("a", lambda: "expired", is_current=lambda: True) == "expired"
    assert cache.get_or_compute("stale", lambda: 1, is_current=lambda: False) == 1
    assert cache.get_or_compute("stale", lambda: 2, is_current=lambda: True) == 2
    with pytest.raises(RuntimeError):
        cache.get_or_compute("check-error", lambda: 1, is_current=Mock(side_effect=RuntimeError()))
    assert cache.get_or_compute("check-error", lambda: 2, is_current=lambda: True) == 2


def test_digest_memo_missing_replacement_and_preserved_mtime(tmp_path):
    digests = FileDigests(max_entries=2)
    path = tmp_path / "artifact"
    assert digests.fingerprint(path) == (None, None)
    path.write_text("old")
    first = digests.fingerprint(path)
    with patch.object(Path, "open", side_effect=AssertionError("rehashing unchanged file")):
        assert digests.fingerprint(path) == first
    replacement = tmp_path / "replacement"
    replacement.write_text("new")
    stat = path.stat()
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(path)
    second = digests.fingerprint(path)
    assert first[0] != second[0] and first[1] != second[1]
    path.unlink()
    assert digests.fingerprint(path) == (None, None)
    for index in range(3):
        path.write_text(str(index))
        digests.fingerprint(path)
    assert len(digests._values) == 2


def test_full_context_keys_and_content_only_seed():
    state = {"blue_team_id": "b", "red_team_id": "r", "blue_team_name": "Blue", "red_team_name": "Red",
             "bp_order": 1, "blue_picks": [1, 2], "red_picks": [], "blue_bans": [], "red_bans": [],
             "blue_used_previous_battles": [], "red_used_previous_battles": [], "legal_hero_ids": None}
    dep = Dependencies("legacy", "generation-a", "same-content")
    def key(current=state, dependencies=dep, endpoint="recommend-lineup", league="20260004", model="personalized", **settings):
        return analysis_key(endpoint, league, model, current, dependencies, **settings)
    baseline = key()
    for field in state:
        changed = deepcopy(state)
        changed[field] = [9] if isinstance(state[field], list) else "changed"
        assert key(changed) != baseline, field
    assert key({**state, "legal_hero_ids": []}) != baseline
    assert key({**state, "blue_picks": [2, 1]}) != baseline
    for settings in ({"endpoint": "next-action"}, {"league": "other"}, {"model": "stats"},
                     {"top_k": 2}, {"risk_mode": "safe"}, {"seed": 0}):
        assert key(**settings) != baseline
    copied = Dependencies({"version": "v2"}, "new-path-and-time", dep.content)
    assert key(dependencies=copied) != baseline
    seed = default_recommendation_seed("20260004", "personalized", state, dep)
    assert seed == default_recommendation_seed("20260004", "personalized", state, copied)
    assert seed != default_recommendation_seed("20260004", "personalized", state, Dependencies("legacy", "g", "new-content"))


def test_dependencies_watch_optional_inputs_and_copy_stable_seed(tmp_path, monkeypatch):
    from app.services import draft_simulator as draft, lineup_value, ban_recommender
    monkeypatch.setattr(draft, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(draft, "OFFICIAL_HEROLIST_PATH", tmp_path / "herolist.json")
    monkeypatch.setattr(draft, "DRAFT_FEATURES_PATH", tmp_path / "features.json")
    monkeypatch.setattr(draft, "LEGACY_SPECIALTY_FEATURES_PATH", tmp_path / "specialty.json")
    monkeypatch.setattr(lineup_value, "GENERATED_MODEL_ROOT", tmp_path)
    monkeypatch.setattr(lineup_value, "DEFAULT_MODEL_PATH", tmp_path / "fallback.json")
    monkeypatch.setattr(lineup_value, "TACTICAL_ROLES_PATH", tmp_path / "roles.json")
    monkeypatch.setattr(ban_recommender, "MODEL_ROOT", tmp_path)
    root = tmp_path / "20260004"
    root.mkdir()
    before = analysis_dependencies("20260004", "personalized", recommendation=True)
    # Each optional/selected dependency participates, including appearance/deletion.
    for path in [root / name for name in ("draft_model.json", "personalized_draft_choice_model.json",
                 "player_draft_context.json", "personalized_draft_probability_calibration.json",
                 "team_action_tendencies.jsonl", "ban_value_model.json", "lineup_value_model.json")] + [tmp_path / "roles.json", tmp_path / "herolist.json", tmp_path / "fallback.json"]:
        path.write_text("{}")
        after = analysis_dependencies("20260004", "personalized", recommendation=True)
        # Fallback is irrelevant while the preferred season model exists.
        if path.name != "fallback.json":
            assert before != after, path
        path.unlink()
        assert analysis_dependencies("20260004", "personalized", recommendation=True) == before
    before = analysis_dependencies("20260004", "sequence", recommendation=False)
    monkeypatch.setenv("DRAFT_SEQUENCE_CALIBRATION", "eligible")
    assert analysis_dependencies("20260004", "sequence", recommendation=False) != before


def test_pinned_dependencies_reuse_validated_hashes(tmp_path):
    from app.services.model_registry import BundleHandle, bundle_scope
    manifest = {"components": {"model": "validated-digest"}, "source_seasons": ["historical"], "promotion_status": "eligible"}
    with bundle_scope(BundleHandle("v1", tmp_path / "a", manifest)), patch.object(Path, "open", side_effect=AssertionError("rehash")):
        first = analysis_dependencies("20260004", "personalized", recommendation=True)
    with bundle_scope(BundleHandle("v2", tmp_path / "b", manifest)):
        second = analysis_dependencies("20260004", "personalized", recommendation=True)
    assert first.generation != second.generation
    assert first.content == second.content


def test_lineup_loader_reloads_tactical_roles_appearance_update_and_deletion(tmp_path, monkeypatch):
    import json
    from app.services import lineup_value
    model_path, roles = tmp_path / "model.json", tmp_path / "roles.json"
    model_path.write_text("{}")
    monkeypatch.setattr(lineup_value, "TACTICAL_ROLES_PATH", roles)
    monkeypatch.setattr(lineup_value, "_MODEL_CACHE", None)
    def read_sources(path, tactical_path):
        return json.loads(tactical_path.read_text()) if tactical_path.is_file() else "missing"
    with patch.object(lineup_value.LineupValueModel, "from_path", side_effect=read_sources) as load:
        assert lineup_value.load_lineup_value_model(path=model_path) == "missing"
        roles.write_text('{"role": 1}')
        assert lineup_value.load_lineup_value_model(path=model_path) == {"role": 1}
        assert lineup_value.load_lineup_value_model(path=model_path) == {"role": 1}
        roles.write_text('{"role": 2}')
        assert lineup_value.load_lineup_value_model(path=model_path) == {"role": 2}
        roles.unlink()
        assert lineup_value.load_lineup_value_model(path=model_path) == "missing"
        assert load.call_count == 4


def test_learnable_loader_reloads_changed_feature_vectors(tmp_path, monkeypatch):
    import json
    from app.services import draft_simulator as draft
    model_path, features = tmp_path / "model.json", tmp_path / "features.json"
    artifact = {
        "schema_version": 2, "model_type": "team_aware_recency_weighted_hybrid_bilinear_choice",
        "target_season": "20260004", "feature_artifact": "features.json", "feature_names": ["value", "feature_known"],
        "hero_ids": [101], "team_ids": ["team"], "context_keys": [["pick", "blue", 1]],
        "role_fields": ["current_team_picks", "current_opponent_picks", "current_team_bans", "current_opponent_bans"],
        "parameters": {"feature_projection": [[1.0], [0.0]], "hero_residual": [[0.0]],
                       "context_embedding": [[0.0]], "state_projection": [[0.0]] * 20,
                       "source_embedding": [[[0.0]]] * 4, "acting_team_embedding": [[0.0]],
                       "opponent_team_embedding": [[0.0]], "hero_bias": [0.0]},
    }
    model_path.write_text(json.dumps(artifact))
    def write_feature(value):
        features.write_text(json.dumps({"feature_names": ["value"], "rows": [{"hero_id": 101, "vector": [value]}]}))
    write_feature(1.0)
    monkeypatch.setattr(draft, "learnable_model_path", lambda league: model_path)
    monkeypatch.setattr(draft, "DRAFT_FEATURES_PATH", features)
    monkeypatch.setattr(draft, "LEGACY_SPECIALTY_FEATURES_PATH", tmp_path / "absent")
    monkeypatch.setattr(draft, "_LEARNABLE_CACHE", {})
    first = draft.load_learnable_model("20260004")
    assert draft.load_learnable_model("20260004") is first
    assert first["_candidate_representations"] == [[1.0]]
    write_feature(2.0)
    assert draft.load_learnable_model("20260004")["_candidate_representations"] == [[2.0]]
    features.unlink()
    with pytest.raises(FileNotFoundError, match="feature vectors"):
        draft.load_learnable_model("20260004")


def test_dependency_change_during_calculation_is_not_retained(tmp_path, monkeypatch):
    from app.services import draft_simulator as draft
    monkeypatch.setattr(draft, "OUTPUT_ROOT", tmp_path)
    root = tmp_path / "20260004"
    root.mkdir()
    path = root / "draft_model.json"
    path.write_text("first")
    dependencies = analysis_dependencies("20260004", "stats", recommendation=False)
    key = analysis_key("next-action", "20260004", "stats", {}, dependencies)
    cache = SimulatorAnalysisCache()
    def compute():
        path.write_text("second")
        return "old generation"
    assert cache.get_or_compute(key, compute, is_current=lambda: dependencies == analysis_dependencies("20260004", "stats", recommendation=False)) == "old generation"
    assert cache.get_or_compute(key, lambda: "recomputed", is_current=lambda: True) == "recomputed"


def test_nested_player_context_loader_reloads_replacements_and_validates_vocabulary(tmp_path, monkeypatch):
    import json
    from app.services import player_draft_context as context
    path = tmp_path / "context.json"
    payload = {"schema_version": 1, "context_contract_version": "player_context_v1", "hero_ids": [101], "pairs": {}}
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(context, "_CACHE", {})
    first = context.load_context_snapshot(path, [101])
    assert context.load_context_snapshot(path, [101]) is first
    stat = path.stat()
    replacement = tmp_path / "replacement"
    replacement.write_text(json.dumps({**payload, "marker": "new"}))
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(path)
    assert context.load_context_snapshot(path, [101])["marker"] == "new"
    with pytest.raises(ValueError, match="incompatible"):
        context.load_context_snapshot(path, [102])
