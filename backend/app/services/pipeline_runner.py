"""Execute durable update jobs sequentially in the API process."""
from __future__ import annotations

import fcntl
import logging
import threading
from uuid import uuid4

from app.services import pipeline_jobs
from app.services.pipeline_execution import execution_context
from app.services.pipeline_worker import database_path, due_job, running_tokens, terminate_token

logger = logging.getLogger(__name__)


class ApiPipelineRunner:
    def __init__(self, *, poll_seconds: float = 2):
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None
        self.startup_error: Exception | None = None

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.ready.clear()
        self.startup_error = None
        self.thread = threading.Thread(target=self._run, name='api-pipeline', daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            self.stop_event.set()
            raise RuntimeError('API pipeline runner did not initialize')
        if self.startup_error is not None:
            raise RuntimeError('API pipeline runner could not start') from self.startup_error

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                logger.error('Pipeline still stopping; execution lock remains held')

    def _run(self) -> None:
        try:
            path = database_path()
            lock_path = pipeline_jobs.LOCK_PATH
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with (lock_path.parent / 'pipeline-worker.lock').open('a+') as owner:
                try:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError('Another update runner owns this workspace; use one API process') from exc
                # Trainers surviving a native API crash may have their own
                # sessions. Clear them before recovering the durable ledger.
                for token in running_tokens(path):
                    terminate_token(token)
                self.ready.set()
                needs_recovery = True
                while not self.stop_event.is_set():
                    with pipeline_jobs.global_pipeline_lock(blocking=False) as fd:
                        if fd is not None:
                            token = str(uuid4())
                            with execution_context(fd, token):
                                if needs_recovery:
                                    pipeline_jobs.recover_jobs(force=True, reason='API stopped before completing the job')
                                    needs_recovery = False
                                job_id = due_job(path)
                                if job_id is not None:
                                    logger.info('Executing update %s inside API process', job_id)
                                    pipeline_jobs.run_job(job_id, stop=self.stop_event)
                    self.stop_event.wait(self.poll_seconds)
        except Exception as exc:
            if not self.ready.is_set():
                self.startup_error = exc
            logger.exception('API pipeline runner stopped')
        finally:
            self.ready.set()


runner = ApiPipelineRunner()
