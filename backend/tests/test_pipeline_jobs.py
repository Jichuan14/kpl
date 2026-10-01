from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.api import jobs as jobs_api
from app.api import pipeline as pipeline_api
from app.api import sync as sync_api
from app.database import get_db
from app.models import League, PipelineJob
from app.schemas import ApiResponse
from app.services import pipeline_jobs
from app.services.sync import SyncService
from app.services import static_publisher


class PipelineJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'jobs.db'}")
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        with self.sessions() as db:
            db.add(League(league_id="20260003", league_name="Existing season",
                          year=2026, season=3, start_time="2026-06-17 00:00:00"))
            db.commit()
        self.patches = [
            patch.object(pipeline_jobs, "SessionLocal", self.sessions),
            patch.object(pipeline_jobs, "init_db"),
            patch.object(pipeline_jobs, "LOCK_PATH", Path(self.directory.name) / "pipeline.lock"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def make_job(self, kind="scheduled", payload=None):
        with self.sessions() as db, patch.object(pipeline_jobs, "dispatch_job", return_value=True):
            return pipeline_jobs.enqueue_job(db, kind, "20260003", payload or {})

    def test_china_day_key_changes_at_local_midnight(self):
        before = datetime(2026, 9, 26, 15, 59, tzinfo=timezone.utc)
        after = before + timedelta(minutes=1)
        self.assertEqual(pipeline_jobs.china_day_key("20260003", before), "scheduled:20260003:2026-09-26")
        self.assertEqual(pipeline_jobs.china_day_key("20260003", after), "scheduled:20260003:2026-09-27")
        self.assertEqual(pipeline_jobs.china_day_key(None, after), "scheduled:auto:2026-09-27")

    def test_submission_is_idempotent_and_outbox_survives_broker_failure(self):
        with self.sessions() as db, patch.object(pipeline_jobs.celery_app, "send_task", side_effect=OSError("broker down")):
            first = pipeline_jobs.enqueue_job(db, "scheduled", "20260003", idempotency_key="scheduled:20260003:2026-09-27")
            second = pipeline_jobs.enqueue_job(db, "scheduled", "20260003", idempotency_key="scheduled:20260003:2026-09-27")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(db.get(PipelineJob, first["id"]).status, "pending")
            self.assertIsNone(db.get(PipelineJob, first["id"]).dispatched_at)
        with patch.object(pipeline_jobs.celery_app, "send_task") as sent:
            pipeline_jobs.recover_jobs()
        sent.assert_called_once()
        with self.sessions() as db:
            self.assertIsNotNone(db.get(PipelineJob, first["id"]).dispatched_at)

    def test_scheduled_api_returns_same_daily_job(self):
        with self.sessions() as db, patch.object(pipeline_jobs, "dispatch_job", return_value=True):
            request = jobs_api.ScheduledJobRequest(league_id="20260003")
            first = jobs_api.schedule_refresh(request, db=db).data
            second = jobs_api.schedule_refresh(request, db=db).data
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(first["status_url"], f"/api/jobs/{first['id']}")

    def test_job_submission_routes_return_202_and_status_url(self):
        app = FastAPI()
        app.include_router(jobs_api.router)
        app.include_router(pipeline_api.router)
        app.include_router(sync_api.router)
        def temporary_db():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = temporary_db
        with patch.object(pipeline_jobs, "dispatch_job", return_value=True), TestClient(app) as client:
            for endpoint, body in (
                ("/api/jobs/scheduled", {}),
                ("/api/jobs/scheduled", {"league_id": "20260003"}),
                ("/api/jobs/full-update", {"league_id": "20260003"}),
                ("/api/pipeline/run", {"league_id": "20260003", "step": "display"}),
                ("/api/pipeline/publish", {"league_id": "20260003"}),
                ("/api/sync/league-bp", {"league_id": "20260003", "run_analysis": False}),
            ):
                response = client.post(endpoint, json=body)
                self.assertEqual(response.status_code, 202, endpoint)
                self.assertEqual(response.json()["data"]["status_url"],
                                 f"/api/jobs/{response.json()['data']['id']}")
            response = client.post("/api/sync/leagues")
            self.assertEqual(response.status_code, 202)
            self.assertIn("status_url", response.json()["data"])

    def test_dynamic_selection_skips_future_and_switches_after_first_completed_match(self):
        with self.sessions() as db:
            db.add_all([
                League(league_id="20260004", year=2026, season=4,
                       start_time="2026-09-20 00:00:00"),
                League(league_id="20270001", year=2027, season=1,
                       start_time="2027-01-01 00:00:00"),
            ])
            db.commit()
            with patch("app.services.sync.KplApiClient") as api_class:
                service = SyncService(db)
                payloads = {
                    "20260003": {"code": 200, "results": [{"status": 2, "start_time": "2026-09-12 18:00:00"}]},
                    "20260004": {"code": 200, "results": [{"status": 0, "start_time": "2026-09-27 18:00:00"}]},
                }
                api_class.return_value.get_matches.side_effect = lambda league_id: payloads[league_id]
                at = datetime(2026, 9, 26, 19, tzinfo=timezone.utc)
                self.assertEqual(service.select_started_league_id(at), "20260003")
                payloads["20260004"]["results"] = [{"status": 2, "start_time": "2026-09-27 18:00:00"}]
                later = datetime(2026, 9, 27, 19, tzinfo=timezone.utc)
                self.assertEqual(service.select_started_league_id(later), "20260004")
                api_class.return_value.get_matches.assert_any_call("20260004")
                api_class.return_value.get_matches.assert_any_call("20260003")
                self.assertNotIn("20270001", [call.args[0] for call in api_class.return_value.get_matches.call_args_list])

    def test_dynamic_selection_fails_when_no_league_has_completed_match(self):
        with self.sessions() as db, patch("app.services.sync.KplApiClient") as api_class:
            api_class.return_value.get_matches.return_value = {"code": 200, "results": []}
            service = SyncService(db)
            with self.assertRaisesRegex(RuntimeError, "completed match"):
                service.select_started_league_id(datetime(2026, 9, 27, tzinfo=timezone.utc))

    def test_published_catalog_excludes_unpublished_future_league(self):
        root = Path(self.directory.name) / "published"
        with self.sessions() as db:
            db.add_all([
                League(league_id="20260004", year=2026, season=4,
                       start_time="2026-09-20 00:00:00"),
                League(league_id="20270001", year=2027, season=1,
                       start_time="2027-01-01 00:00:00"),
            ])
            db.commit()
            for league_id in ("20260003", "20260004"):
                directory = root / league_id
                directory.mkdir(parents=True)
                (directory / "overview.json").write_text("{}")
            with patch.object(static_publisher, "DATA_ROOT", root):
                static_publisher._publish_seasons(db)
        seasons = json.loads((root / "seasons.json").read_text())
        self.assertEqual([season["league_id"] for season in seasons],
                         ["20260004", "20260003"])
        self.assertEqual(seasons[0]["start_time"], "2026-09-20 00:00:00")

    def test_duplicate_delivery_executes_once(self):
        job = self.make_job()
        with patch.object(pipeline_jobs, "_perform", return_value={"ok": True}) as perform:
            pipeline_jobs.run_job(job["id"])
            pipeline_jobs.run_job(job["id"])
        perform.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.get(PipelineJob, job["id"]).status, "completed")

    def test_failure_is_bounded_and_stale_worker_is_recovered(self):
        job = self.make_job()
        with patch.object(pipeline_jobs, "_perform", side_effect=OSError("network down")):
            pipeline_jobs.run_job(job["id"])
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            self.assertEqual(record.status, "pending")
            self.assertEqual(record.attempts, 1)
            record.next_attempt_at = pipeline_jobs.now() - timedelta(seconds=1)
            db.commit()
        with patch.object(pipeline_jobs, "_perform", return_value={"ok": True}):
            pipeline_jobs.run_job(job["id"])
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.attempts, 2)
            record.status = "running"
            record.heartbeat_at = pipeline_jobs.now() - timedelta(minutes=3)
            db.commit()
        with patch.object(pipeline_jobs, "dispatch_job", return_value=True) as dispatched:
            pipeline_jobs.recover_jobs()
        with self.sessions() as db:
            self.assertEqual(db.get(PipelineJob, job["id"]).status, "pending")
        dispatched.assert_called_once()

    def test_deterministic_failure_is_not_retried(self):
        job = self.make_job("analysis", {"step": "display"})
        with patch.object(pipeline_jobs, "_perform", side_effect=ValueError("invalid source")):
            pipeline_jobs.run_job(job["id"])
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            self.assertEqual(record.status, "failed")
            self.assertEqual(record.attempts, 1)

    def test_scheduled_skips_current_analysis_but_repairs_publication(self):
        job = self.make_job()
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            db.commit()
            db.refresh(record)
            db.expunge(record)
        sync_result = {"league_id": "20260003", "data_changed": False}
        status = {"analysis_ready": True, "frontend_assets": [{"ready": False}]}
        with (
            patch("app.services.sync.SyncService") as sync_class,
            patch("app.api.data.data_status", return_value=ApiResponse(data=status)),
            patch("app.services.analysis_pipeline.AnalysisPipeline") as analysis,
            patch("app.services.static_publisher.publish_league", return_value={"files": ["overview.json"]}) as publish,
        ):
            sync_class.return_value.sync_league_bp.return_value = sync_result
            result = pipeline_jobs._perform(record)
        analysis.assert_not_called()
        publish.assert_called_once()
        self.assertIn("published", result)

    def test_full_update_forces_analysis_even_when_current(self):
        job = self.make_job("full_update")
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            db.commit()
            db.refresh(record)
            db.expunge(record)
        status = {"analysis_ready": True, "frontend_assets": [{"ready": True}]}
        with (
            patch("app.services.sync.SyncService") as sync_class,
            patch("app.api.data.data_status", return_value=ApiResponse(data=status)),
            patch("app.services.analysis_pipeline.AnalysisPipeline") as analysis,
            patch("app.services.static_publisher.publish_league", return_value={"files": []}) as publish,
        ):
            sync_class.return_value.sync_league_bp.return_value = {"league_id": "20260003", "data_changed": False}
            result = pipeline_jobs._perform(record)
        self.assertEqual([call.args[0] for call in analysis.return_value.run.call_args_list], ["display", "rolling_model"])
        publish.assert_called_once()
        self.assertIn("analysis", result)

    def test_candidate_failure_occurs_after_factual_publication_and_fails_job(self):
        job = self.make_job("full_update")
        events=[];active=Path(self.directory.name)/"current.json";active.write_text('{"version":"incumbent"}')
        facts=Path(self.directory.name)/"overview.json"
        def run(step):
            events.append(step)
            if step=="rolling_model":raise ValueError("candidate gate failed")
            return {"ok":True}
        def publish(db,league):
            events.append("publish");facts.write_text('{"season":"20260003"}')
            return {"files":["overview.json"]}
        with (
            patch("app.services.sync.SyncService") as sync,
            patch("app.api.data.data_status",return_value=ApiResponse(data={"analysis_ready":True,"frontend_assets":[]})),
            patch("app.services.analysis_pipeline.AnalysisPipeline") as analysis,
            patch("app.services.static_publisher.publish_league",side_effect=publish),
        ):
            sync.return_value.sync_league_bp.return_value={"league_id":"20260003","data_changed":True}
            analysis.return_value.run.side_effect=run
            pipeline_jobs.run_job(job["id"])
        self.assertEqual(events,["display","publish","rolling_model"])
        self.assertEqual(json.loads(facts.read_text())["season"],"20260003")
        self.assertEqual(json.loads(active.read_text())["version"],"incumbent")
        with self.sessions() as db:
            record=db.get(PipelineJob,job["id"])
            self.assertEqual(record.status,"failed")
            self.assertIn("candidate gate failed",record.error)

    def test_scheduled_auto_resolution_is_persisted_before_sync(self):
        with self.sessions() as db, patch.object(pipeline_jobs, "dispatch_job", return_value=True):
            db.add(League(league_id="20260004", year=2026, season=4,
                          start_time="2026-09-20 00:00:00"))
            db.commit()
            job = pipeline_jobs.enqueue_job(db, "scheduled", None, {"auto": True})
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            record.attempts = 1
            db.commit()
            db.refresh(record)
            db.expunge(record)
        with (
            patch("app.services.sync.SyncService") as sync_class,
            patch("app.api.data.data_status", return_value=ApiResponse(data={"analysis_ready": True, "frontend_assets": [{"ready": True}]})),
        ):
            sync_class.return_value.sync_leagues.return_value = {"updated": 2}
            sync_class.return_value.select_started_league_id.return_value = "20260004"
            sync_class.return_value.sync_league_bp.return_value = {"league_id": "20260004", "data_changed": False}
            result = pipeline_jobs._perform(record)
        self.assertEqual(result["selected_league_id"], "20260004")
        self.assertEqual(result["download"]["league_id"], "20260004")
        sync_class.return_value.sync_league_bp.assert_called_once_with(
            league_id="20260004", match_limit=None, recompute_stats=True, incremental=True)
        with self.sessions() as db:
            self.assertEqual(db.get(PipelineJob, job["id"]).league_id, "20260004")

    def test_scheduled_override_keeps_pinned_league(self):
        job = self.make_job("scheduled", {"auto": False})
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            db.commit()
            db.refresh(record)
            db.expunge(record)
        with (
            patch("app.services.sync.SyncService") as sync_class,
            patch("app.api.data.data_status", return_value=ApiResponse(data={"analysis_ready": True, "frontend_assets": [{"ready": True}]})),
        ):
            sync_class.return_value.sync_league_bp.return_value = {"league_id": "20260003", "data_changed": False}
            result = pipeline_jobs._perform(record)
        sync_class.return_value.sync_leagues.assert_called_once()
        sync_class.return_value.select_started_league_id.assert_not_called()
        self.assertEqual(result["selected_league_id"], "20260003")

    def test_unknown_pinned_league_fails_before_sync_or_publish(self):
        with self.sessions() as db, patch.object(pipeline_jobs, "dispatch_job", return_value=True):
            job = pipeline_jobs.enqueue_job(db, "scheduled", "20990001", {"auto": False})
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            db.commit()
            db.refresh(record)
            db.expunge(record)
        with patch("app.services.sync.SyncService") as sync_class:
            with self.assertRaisesRegex(ValueError, "absent from the official catalog"):
                pipeline_jobs._perform(record)
        sync_class.return_value.sync_league_bp.assert_not_called()

    def test_scheduled_rebuilds_stale_analysis(self):
        job = self.make_job()
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            db.commit()
            db.refresh(record)
            db.expunge(record)
        with (
            patch("app.services.sync.SyncService") as sync_class,
            patch("app.api.data.data_status", return_value=ApiResponse(data={"analysis_ready": False, "frontend_assets": [{"ready": False}]})),
            patch("app.services.analysis_pipeline.AnalysisPipeline") as analysis,
            patch("app.services.static_publisher.publish_league", return_value={"files": []}) as publish,
        ):
            sync_class.return_value.sync_league_bp.return_value = {"league_id": "20260003", "data_changed": False}
            pipeline_jobs._perform(record)
        self.assertEqual([call.args[0] for call in analysis.return_value.run.call_args_list], ["display", "rolling_model"])
        publish.assert_called_once()

    def test_retry_after_post_sync_crash_rebuilds_even_if_sync_now_reports_no_change(self):
        job = self.make_job()
        with self.sessions() as db:
            record = db.get(PipelineJob, job["id"])
            record.status = "running"
            record.attempts = 2
            db.commit()
            db.refresh(record)
            db.expunge(record)
        with (
            patch("app.services.sync.SyncService") as sync_class,
            patch("app.api.data.data_status", return_value=ApiResponse(data={"analysis_ready": True, "frontend_assets": [{"ready": True}]})),
            patch("app.services.analysis_pipeline.AnalysisPipeline") as analysis,
            patch("app.services.static_publisher.publish_league", return_value={}) as publish,
        ):
            sync_class.return_value.sync_league_bp.return_value = {"league_id": "20260003", "data_changed": False}
            pipeline_jobs._perform(record)
        self.assertEqual([call.args[0] for call in analysis.return_value.run.call_args_list], ["display", "rolling_model"])
        publish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
