from concurrent.futures import Future, ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import simulation
from app.schemas import DraftSimulationRequest, LineupRecommendationRequest
from app.services.coach_rate_limit import CoachRateLimiter
from app.services.model_registry import BundleHandle, current_bundle
from app.services.simulator_analysis_cache import Dependencies, SimulatorAnalysisCache


def payload(**changes):
    return {"league_id": "20260004", "blue_team_id": "blue", "blue_team_name": "Client Blue",
            "red_team_id": "red", "red_team_name": "Client Red", "bp_order": 1, **changes}


def request(visitor="visitor"):
    return Request({"type": "http", "client": (visitor, 123), "headers": []})


@pytest.fixture
def setup_api(monkeypatch):
    cache = SimulatorAnalysisCache()
    limiter = CoachRateLimiter(per_ip_per_minute=100, per_ip_per_day=1000, server_per_minute=100,
                               server_per_day=1000, max_active_per_ip=10, max_active_server=20)
    validate = Mock(return_value={"blue": {"team_name": "Official Blue"}, "red": {"team_name": "Official Red"}})
    monkeypatch.setattr(simulation, "simulator_analysis_cache", cache)
    monkeypatch.setattr(simulation, "simulation_rate_limiter", limiter)
    monkeypatch.setattr(simulation, "_simulation_client_key", lambda req: req.client.host)
    monkeypatch.setattr(simulation, "validate_season_team_pair", validate)
    monkeypatch.setattr(simulation, "analysis_dependencies", lambda *args, **kwargs: Dependencies("legacy", "generation", "content"))
    return cache, limiter, validate


@pytest.mark.parametrize("route,schema,calculator", [
    (simulation.draft_next_action, DraftSimulationRequest, "predict_next_action"),
    (simulation.lineup_recommendation, LineupRecommendationRequest, "recommend_lineup"),
])
def test_cross_visitor_single_flight_at_boundary(setup_api, route, schema, calculator):
    _, limiter, validate = setup_api
    entered, release, joined = Event(), Event(), Event()
    def calculate(*args, **kwargs):
        assert args[1]["blue_team_name"] == "Official Blue"
        entered.set()
        assert release.wait(5)
        return {"rows": [1]}
    real_result = Future.result
    def wait_result(future, *args, **kwargs):
        joined.set()
        return real_result(future, *args, **kwargs)
    with patch.object(simulation, calculator, side_effect=calculate) as compute, patch.object(Future, "result", wait_result), ThreadPoolExecutor(2) as pool:
        leader = pool.submit(route, schema(**payload()), request("a"), object())
        assert entered.wait(5)
        follower = pool.submit(route, schema(**payload()), request("b"), object())
        assert joined.wait(5)
        assert validate.call_count == 2
        release.set()
        first, second = leader.result(5), follower.result(5)
        first.data["rows"].append(2)
        assert second.data == {"rows": [1]}
        assert route(schema(**payload()), request("c"), object()).data == {"rows": [1]}
    assert compute.call_count == 1
    assert validate.call_count == 3
    # Cached hits and followers were each admitted and released.
    assert limiter._active_server == 0


def test_recommendation_seeds_and_presentation_settings(setup_api):
    cache, _, _ = setup_api
    with patch.object(simulation, "recommend_lineup", side_effect=lambda *args, **kw: {"seed": kw["seed"]}) as compute:
        body = LineupRecommendationRequest(**payload())
        first = simulation.lineup_recommendation(body, request(), object()).data
        assert isinstance(first["seed"], int)
        cache.clear()
        assert simulation.lineup_recommendation(body, request(), object()).data == first
        for changes in ({"top_k": 5}, {"risk_mode": "safe"}):
            changed = LineupRecommendationRequest(**{**payload(), **changes})
            assert simulation.lineup_recommendation(changed, request(), object()).data == first
        for seed in (0, -17, 123):
            changed = LineupRecommendationRequest(**payload(seed=seed))
            assert simulation.lineup_recommendation(changed, request(), object()).data == {"seed": seed}
        # Changed presentation choices compute independently but preserve sampling.
        assert compute.call_count == 7


def test_cache_hit_revalidates_and_consumes_existing_budget(setup_api):
    _, _, validate = setup_api
    limiter = CoachRateLimiter(per_ip_per_minute=2, per_ip_per_day=100, server_per_minute=100,
                               server_per_day=100, max_active_per_ip=1, max_active_server=2)
    body = DraftSimulationRequest(**payload())
    with patch.object(simulation, "simulation_rate_limiter", limiter), patch.object(simulation, "predict_next_action", return_value={}) as compute:
        simulation.draft_next_action(body, request(), object())
        simulation.draft_next_action(body, request(), object())
        with pytest.raises(HTTPException) as error:
            simulation.draft_next_action(body, request(), object())
        assert error.value.status_code == 429
        compute.assert_called_once()
        assert validate.call_count == 2
    validate.side_effect = ValueError("team no longer belongs to selected season")
    with pytest.raises(HTTPException, match="400"):
        simulation.draft_next_action(body, request("different"), object())


def test_authoritative_names_and_missing_season_do_not_reuse_stale_data(setup_api):
    _, _, validate = setup_api
    body = DraftSimulationRequest(**payload())
    with patch.object(simulation, "predict_next_action", side_effect=lambda *args, **kw: {"team": args[1]["blue_team_name"]}) as compute:
        assert simulation.draft_next_action(body, request(), object()).data["team"] == "Official Blue"
        validate.return_value["blue"]["team_name"] = "Renamed Blue"
        assert simulation.draft_next_action(body, request(), object()).data["team"] == "Renamed Blue"
        assert compute.call_count == 2
    with patch.object(simulation, "predict_next_action", side_effect=FileNotFoundError("No draft model for 20260004")) as compute:
        for _ in range(2):
            with pytest.raises(HTTPException) as error:
                simulation.draft_next_action(DraftSimulationRequest(**payload(bp_order=2)), request(), object())
            assert error.value.status_code == 404
        assert compute.call_count == 2


def test_concurrent_pinned_versions_keep_context_and_metadata(setup_api, tmp_path, monkeypatch):
    from app.services.simulator_analysis_cache import analysis_dependencies
    monkeypatch.setattr(simulation, "analysis_dependencies", analysis_dependencies)
    manifest = {"components": {"draft_model.json": "digest"}, "source_seasons": ["historical"], "promotion_status": "eligible"}
    handles = {version: BundleHandle(version, tmp_path / version, manifest) for version in ("v1", "v2")}
    barrier = Barrier(2)
    def compute(*args, **kwargs):
        version = current_bundle().version
        barrier.wait(5)
        assert current_bundle().version == version
        return {"computed_version": version}
    with patch("app.services.model_tool_scope.resolve_bundle", side_effect=lambda v: handles[v]), patch.object(simulation, "predict_next_action", side_effect=compute) as calculate, ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(simulation.draft_next_action, DraftSimulationRequest(**payload(model_version=v)), request(v), object()) for v in handles]
        for version, future in zip(handles, futures):
            result = future.result(5).data
            assert result["model_version"] == version == result["computed_version"]
        # Same version hits a separate cached result, with decorator metadata fresh.
        for version in handles:
            result = simulation.draft_next_action(DraftSimulationRequest(**payload(model_version=version)), request(), object()).data
            assert result["model_version"] == version == result["computed_version"]
        assert calculate.call_count == 2
