"""API-owned execution, request isolation, lifecycle and cancellation checks."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api import jobs
from app.database import get_db
from app.models import PipelineJob
from app.services import pipeline_jobs, pipeline_runner, analysis_pipeline
from app.services.pipeline_execution import execution_context, subprocess_options
import test_pipeline_jobs as existing


class ApiRunnerTests(unittest.TestCase):
    setUp = existing.PipelineJobTests.setUp
    make_job = existing.PipelineJobTests.make_job

    def make_runner(self):
        path = str(Path(self.directory.name) / 'jobs.db')
        patched = patch.object(pipeline_runner, 'database_path', return_value=path)
        patched.start(); self.addCleanup(patched.stop)
        runner = pipeline_runner.ApiPipelineRunner(poll_seconds=.01)
        self.addCleanup(runner.stop)
        return runner

    def wait_status(self, job_id, status):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.sessions() as db:
                record = db.get(PipelineJob, job_id)
                if record.status == status:
                    return record
            time.sleep(.01)
        self.fail(f'Job did not reach {status}')

    def test_http_submission_executes_in_api_process_and_health_stays_available(self):
        runner = self.make_runner(); entered = threading.Event(); finish = threading.Event()
        app = FastAPI(); app.include_router(jobs.router)
        def temporary_db():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db] = temporary_db
        app.add_event_handler('startup', runner.start); app.add_event_handler('shutdown', runner.stop)
        @app.get('/health')
        def health(): return {'status': 'ok'}
        observed = {}
        def perform(job):
            observed.update(pid=os.getpid(), thread=threading.current_thread().name)
            entered.set(); finish.wait(3)
            return {'ok': True}
        with patch.object(pipeline_jobs, '_perform', side_effect=perform), TestClient(app) as client:
            response = client.post('/api/jobs/full-update', json={'league_id': '20260003'})
            self.assertEqual(response.status_code, 202)
            job = response.json()['data']; self.assertTrue(entered.wait(2))
            self.assertEqual(observed, {'pid': os.getpid(), 'thread': 'api-pipeline'})
            self.assertEqual(client.get('/health').status_code, 200)
            self.assertEqual(client.get(job['status_url']).json()['data']['status'], 'running')
            finish.set(); self.wait_status(job['id'], 'completed')
        self.assertFalse(runner.thread.is_alive())

    def test_execution_state_does_not_leak_into_unrelated_request_threads(self):
        original = {k:os.environ.get(k) for k in ('KPL_JOB_EXECUTION_TOKEN', 'KPL_PIPELINE_LOCK_FD')}
        with pipeline_jobs.global_pipeline_lock() as fd, execution_context(fd, 'isolated'):
            self.assertEqual(subprocess_options()['env']['KPL_JOB_EXECUTION_TOKEN'], 'isolated')
            with ThreadPoolExecutor(max_workers=1) as pool:
                other = pool.submit(subprocess_options).result()
            self.assertNotIn('KPL_JOB_EXECUTION_TOKEN', other['env'])
            self.assertEqual(other['pass_fds'], ())
        self.assertEqual({k:os.environ.get(k) for k in original}, original)

    def test_shutdown_cancels_trainer_and_preserves_bounded_retry(self):
        runner = self.make_runner(); job = self.make_job('analysis', {'step':'rolling_model'})
        started = Path(self.directory.name) / 'child-started'
        code = f'import pathlib,time; pathlib.Path({str(started)!r}).write_text("ready"); time.sleep(60)'
        def perform(record):
            return analysis_pipeline._run_command([sys.executable,'-c',code], cwd=Path(self.directory.name), timeout_seconds=60)
        with patch.object(pipeline_jobs, '_perform', side_effect=perform):
            runner.start()
            deadline = time.monotonic()+5
            while not started.exists() and time.monotonic()<deadline: time.sleep(.01)
            self.assertTrue(started.exists()); runner.stop()
        self.assertFalse(runner.thread.is_alive())
        record = self.wait_status(job['id'], 'pending')
        self.assertEqual(record.stage, 'retry_wait'); self.assertEqual(record.attempts, 1)
        self.assertIn('API stopped', record.error)
        self.assertIsNone(record.execution_token)
        with pipeline_jobs.global_pipeline_lock(blocking=False) as fd: self.assertIsNotNone(fd)

    def test_duplicate_api_runner_is_rejected(self):
        first = self.make_runner(); second = self.make_runner()
        first.start()
        with self.assertRaisesRegex(RuntimeError, 'could not start'): second.start()
        first.stop()

    def test_queued_job_survives_api_restart(self):
        runner = self.make_runner(); runner.start(); runner.stop()
        job = self.make_job()
        replacement = self.make_runner()
        with patch.object(pipeline_jobs, '_perform', return_value={'ok':True}):
            replacement.start(); record = self.wait_status(job['id'], 'completed'); replacement.stop()
        self.assertEqual(record.attempts, 1)

    def test_health_reports_an_unexpectedly_stopped_runner(self):
        from app import main
        from unittest.mock import Mock
        dead = Mock(); dead.is_alive.return_value = False
        with patch.object(main.pipeline_runner, 'thread', dead):
            response = main.health()
        self.assertEqual(response.status_code, 503)
