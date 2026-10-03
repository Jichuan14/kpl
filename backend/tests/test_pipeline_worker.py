"""Queue ordering, fencing, recovery, and subprocess lifetime checks."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app.services import pipeline_worker as worker
from app.services import pipeline_jobs
from app.models import PipelineJob
from app.database import ensure_schema_compatibility
from sqlalchemy import create_engine, text
from concurrent.futures import ThreadPoolExecutor
import test_pipeline_jobs as existing


class WorkerTests(unittest.TestCase):
    def test_supervisor_import_does_not_load_application_or_training(self):
        result = subprocess.check_output([sys.executable, '-c',
            'import sys,json; from app.services import pipeline_worker; print(json.dumps([x for x in sys.modules if x.startswith(("sqlalchemy", "torch", "app.services.pipeline_jobs", "fastapi"))]))'], text=True)
        self.assertEqual(json.loads(result), [])

    def test_pending_selection_respects_due_time_attempt_limit_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'jobs.db')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE pipeline_jobs (id TEXT, status TEXT, attempts INTEGER, next_attempt_at TEXT, created_at TEXT)')
                db.executemany('INSERT INTO pipeline_jobs VALUES (?,?,?,?,?)', [
                    ('future', 'pending', 0, '2999-01-01', '2000'),
                    ('exhausted', 'pending', 3, None, '2000'),
                    ('running', 'running', 1, None, '2000'),
                    ('second', 'pending', 0, None, '2026'),
                    ('first', 'pending', 2, None, '2025')])
            self.assertEqual(worker.due_job(path), 'first')

    def test_old_ledger_migration_preserves_existing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'old.db'}")
            with engine.begin() as db:
                db.execute(text("CREATE TABLE pipeline_jobs (id TEXT PRIMARY KEY, status TEXT)"))
                db.execute(text("INSERT INTO pipeline_jobs VALUES ('existing','pending')"))
            self.assertEqual(ensure_schema_compatibility(engine), ['pipeline_jobs.execution_token'])
            self.assertEqual(ensure_schema_compatibility(engine), [])
            with engine.connect() as db:
                self.assertEqual(tuple(db.execute(text('SELECT * FROM pipeline_jobs')).one()), ('existing', 'pending', None))
            engine.dispose()

    def test_supervisor_enforces_deadline_then_recovers_under_lock(self):
        from unittest.mock import Mock
        completed = Mock(); completed.wait.return_value = 0
        running = Mock(); running.poll.return_value = None; running.returncode = -15
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(worker, 'LOCK_PATH', Path(directory) / 'pipeline.lock'), \
             patch.object(worker, 'running_tokens', return_value=[]), \
             patch.object(worker, 'due_job', return_value='queued'), \
             patch.object(worker, 'child', side_effect=[completed, completed, running, completed]) as launch, \
             patch.object(worker, 'terminate_token') as terminate:
            worker.supervise('unused.db', timeout=.01, once=True)
            terminate.assert_called_once()
            recovery = launch.call_args_list[-1]
            self.assertIn('--recover', recovery.args[0])
            self.assertIn('deadline', recovery.args[0][-1])
            self.assertIsInstance(recovery.args[1], int)

    def test_timeout_terminates_root_process_group(self):
        env = os.environ.copy(); token = 'test-worker-' + str(os.getpid())
        env[worker.TOKEN_ENV] = token
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], env=env, start_new_session=True)
        try:
            worker.terminate_token(token, process, grace=.2)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None: process.kill()
            process.wait()

    @unittest.skipUnless(Path('/proc/self/environ').exists(), 'Linux process environment required')
    def test_orphan_with_separate_session_is_terminated(self):
        env = os.environ.copy(); token = 'test-orphan-' + str(os.getpid())
        env[worker.TOKEN_ENV] = token
        code = 'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],start_new_session=True); print(p.pid,flush=True)'
        parent = subprocess.Popen([sys.executable, '-c', code], env=env, stdout=subprocess.PIPE, text=True, start_new_session=True)
        pid = int(parent.stdout.readline()); parent.wait(); parent.stdout.close()
        try:
            self.assertIn(pid, worker.token_processes(token))
            worker.terminate_token(token, grace=.2)
            self.assertNotIn(pid, worker.token_processes(token))
        finally:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass


class ExecutionSafetyTests(unittest.TestCase):
    setUp = existing.PipelineJobTests.setUp
    make_job = existing.PipelineJobTests.make_job
    def test_duplicate_delivery_executes_only_once(self):
        job = self.make_job()
        with patch.object(pipeline_jobs, '_perform', return_value={'ok': True}) as perform:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(pipeline_jobs.run_job, [job['id']] * 3))
        self.assertEqual(perform.call_count, 1)

    def test_recovery_refuses_live_execution_despite_stale_heartbeat(self):
        job = self.make_job()
        with self.sessions() as db:
            record = db.get(PipelineJob, job['id']); record.status = 'running'
            record.heartbeat_at = None; record.attempts = 1; db.commit()
        with pipeline_jobs.global_pipeline_lock():
            self.assertFalse(pipeline_jobs.recover_jobs(force=True))
        with self.sessions() as db:
            self.assertEqual(db.get(PipelineJob, job['id']).status, 'running')
        self.assertTrue(pipeline_jobs.recover_jobs(force=True))
        with self.sessions() as db:
            record = db.get(PipelineJob, job['id'])
            self.assertEqual(record.status, 'pending'); self.assertGreater(record.next_attempt_at, pipeline_jobs.now())

    def test_previous_execution_token_cannot_write_progress(self):
        job = self.make_job()
        with self.sessions() as db:
            record = db.get(PipelineJob, job['id']); record.status = 'running'
            record.execution_token = 'new'; record.stage = 'new execution'; db.commit()
        pipeline_jobs._update_stage(job['id'], 'stale execution', 'old')
        with self.sessions() as db: self.assertEqual(db.get(PipelineJob, job['id']).stage, 'new execution')
        pipeline_jobs._update_stage(job['id'], 'current execution', 'new')
        with self.sessions() as db: self.assertEqual(db.get(PipelineJob, job['id']).stage, 'current execution')

    def test_exhausted_crash_is_terminal(self):
        job = self.make_job()
        with self.sessions() as db:
            record = db.get(PipelineJob, job['id']); record.status = 'running'; record.attempts = 3; db.commit()
        pipeline_jobs.recover_jobs(force=True)
        with self.sessions() as db: self.assertEqual(db.get(PipelineJob, job['id']).status, 'failed')
