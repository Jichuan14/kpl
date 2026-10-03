"""Durable SQLite maintenance jobs, executed sequentially inside the API."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import League, PipelineJob
from app.services.pipeline_execution import (LOCK_FD_ENV, TOKEN_ENV, PipelineInterrupted,
    check_interrupted, execution_context, execution_token, lock_fd as current_lock_fd)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=2)
JOB_TIMEOUT_SECONDS = 3 * 60 * 60
LOCK_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline.lock"


def now() -> datetime:
    # SQLite stores naive UTC timestamps consistently.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def china_day_key(league_id: str | None, at: datetime | None = None) -> str:
    instant = at or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    day = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return f"scheduled:{league_id or 'auto'}:{day.isoformat()}"


def job_data(job: PipelineJob, *, include_result: bool = True) -> dict:
    def iso_utc(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc).isoformat()

    return {
        "id": job.id,
        "status_url": f"/api/jobs/{job.id}",
        "kind": job.kind,
        "league_id": job.league_id,
        "payload": json.loads(job.payload),
        "status": job.status,
        "stage": job.stage,
        "attempts": job.attempts,
        "created_at": iso_utc(job.created_at),
        "heartbeat_at": iso_utc(job.heartbeat_at),
        "finished_at": iso_utc(job.finished_at),
        "result": json.loads(job.result) if include_result and job.result else None,
        "error": job.error,
    }


def enqueue_job(db: Session, kind: str, league_id: str | None = None,
                payload: dict | None = None, idempotency_key: str | None = None) -> dict:
    if kind not in {"scheduled", "full_update", "sync_leagues", "sync_bp", "analysis", "publish"}:
        raise ValueError("Unknown job kind")
    if league_id is not None and (len(league_id) > 32 or not league_id or
                                  not all(c.isalnum() or c in "-_" for c in league_id)):
        raise ValueError("Invalid league_id")
    if idempotency_key:
        existing = db.scalar(select(PipelineJob).where(PipelineJob.idempotency_key == idempotency_key))
        if existing:
            return job_data(existing)
    job = PipelineJob(id=str(uuid4()), kind=kind, league_id=league_id,
                      payload=json.dumps(payload or {}), idempotency_key=idempotency_key,
                      status="pending", stage="queued")
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not idempotency_key:
            raise
        return job_data(db.scalar(select(PipelineJob).where(PipelineJob.idempotency_key == idempotency_key)))
    db.refresh(job)
    return job_data(job)


@contextmanager
def global_pipeline_lock(*, blocking: bool = True):
    """Hold the execution lock, or reuse the supervisor's inherited descriptor.

    Descendants inherit this descriptor so a surviving trainer keeps the lock
    even if its job process dies. Recovery cannot overlap that execution.
    """
    inherited = current_lock_fd()
    if inherited is not None:
        fd = int(inherited)
        stat = os.fstat(fd)
        expected = LOCK_PATH.stat()
        if (stat.st_dev, stat.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeError("Inherited pipeline lock does not match this workspace")
        yield fd
        return
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        try:
            yield handle.fileno()
        finally:
            # Closing the last inherited descriptor releases the lock. Never
            # explicitly unlock a description still held by a surviving trainer.
            pass


def execution_filter(job_id: str, token: str | None = None):
    conditions = [PipelineJob.id == job_id, PipelineJob.status == "running"]
    token = token or execution_token()
    if token:
        conditions.append(PipelineJob.execution_token == token)
    return conditions


def _update_stage(job_id: str, stage: str, token: str | None = None) -> None:
    check_interrupted()
    with SessionLocal() as db:
        db.execute(update(PipelineJob).where(*execution_filter(job_id, token))
                   .values(stage=stage, heartbeat_at=now()))
        db.commit()


def _heartbeat(job_id: str, stop: threading.Event, token: str) -> None:
    while not stop.wait(20):
        try:
            _update_stage(job_id, _current_stage(job_id), token)
        except Exception:
            logger.exception("Could not heartbeat pipeline job %s", job_id)


def _current_stage(job_id: str) -> str:
    with SessionLocal() as db:
        job = db.get(PipelineJob, job_id)
        return job.stage if job else "running"


def _automatic_model_update(job: PipelineJob, league_id: str) -> dict:
    if not get_settings().auto_model_training_enabled:
        logger.info("Job %s published season data; automatic model training is disabled", job.id)
        return {"status": "DEFERRED", "active_preserved": True,
                "reason": "AUTO_MODEL_TRAINING_ENABLED=false; existing model retained, no new model trained."}
    from app.services.analysis_pipeline import AnalysisPipeline
    _update_stage(job.id, "rolling_model")
    return AnalysisPipeline(league_id).run("rolling_model")


def _perform(job: PipelineJob) -> dict:
    from app.api.data import data_status
    from app.services.analysis_pipeline import AnalysisPipeline
    from app.services.static_publisher import publish_league
    from app.services.sync import SyncService

    payload = json.loads(job.payload)
    kind, league_id = job.kind, job.league_id
    with SessionLocal() as db:
        if kind == "sync_leagues":
            _update_stage(job.id, "sync_leagues")
            sync = SyncService(db)
            try:
                return sync.sync_leagues()
            finally:
                sync.close()
        if kind in {"scheduled", "full_update", "sync_bp"}:
            result = {}
            if kind == "scheduled":
                _update_stage(job.id, "sync_leagues")
                sync = SyncService(db)
                try:
                    result["catalog"] = sync.sync_leagues()
                    if league_id is None:
                        _update_stage(job.id, "select_league")
                        league_id = sync.select_started_league_id()
                    elif db.scalar(select(League).where(League.league_id == league_id)) is None:
                        raise ValueError(f"Pinned league {league_id} is absent from the official catalog")
                finally:
                    sync.close()
                # Persist the resolution before syncing any source rows. A
                # retry remains on the same league even if the catalog changes.
                db.execute(update(PipelineJob).where(*execution_filter(job.id))
                           .values(league_id=league_id))
                db.commit()
                job.league_id = league_id
                result["selected_league_id"] = league_id
            _update_stage(job.id, "sync_bp")
            sync = SyncService(db)
            try:
                downloaded = sync.sync_league_bp(
                    league_id=league_id, match_limit=payload.get("match_limit"),
                    recompute_stats=payload.get("recompute_stats", True),
                    incremental=payload.get("incremental", True),
                )
            finally:
                sync.close()
            result["download"] = downloaded
            league_id = downloaded["league_id"]
            if kind in {"scheduled", "full_update"} or payload.get("run_analysis"):
                # Readiness includes missing/stale model outputs. A successful
                # no-change sync still repairs missing artifacts and publication.
                status = data_status(league_id, db).data
                # A previous attempt may have crashed after committing the sync
                # and before recording that new data needs analysis.
                do_analysis = (kind == "full_update" or job.attempts > 1 or
                               downloaded["data_changed"] or not status["analysis_ready"])
                if do_analysis:
                    _update_stage(job.id, "analysis")
                    result["analysis"] = AnalysisPipeline(league_id).run("display")
                status = data_status(league_id, db).data
                if do_analysis or any(not asset["ready"] for asset in status["frontend_assets"]):
                    _update_stage(job.id, "publish")
                    result["published"] = publish_league(db, league_id)
                if do_analysis:
                    # Factual season publication is committed before global model
                    # training. A deferred/failed candidate keeps the incumbent.
                    result["model_update"] = _automatic_model_update(job, league_id)
            return result
        if kind == "analysis":
            _update_stage(job.id, "analysis")
            if payload["step"] == "all":
                result = {"analysis": AnalysisPipeline(league_id).run("display")}
                _update_stage(job.id,"publish")
                result["published"] = publish_league(db,league_id)
                result["model_update"] = _automatic_model_update(job, league_id)
                return result
            return AnalysisPipeline(league_id).run(payload["step"])
        if kind == "publish":
            _update_stage(job.id, "publish")
            return publish_league(db, league_id)
    raise ValueError("Unknown job kind")


def run_job(job_id: str, *, stop: threading.Event | None = None, timeout: float = JOB_TIMEOUT_SECONDS) -> None:
    init_db()
    with global_pipeline_lock() as lock_fd:
        token = execution_token() or str(uuid4())
        with SessionLocal() as db:
            claimed = db.execute(update(PipelineJob)
                .where(PipelineJob.id == job_id, PipelineJob.status == "pending",
                       PipelineJob.attempts < MAX_ATTEMPTS,
                       (PipelineJob.next_attempt_at.is_(None) | (PipelineJob.next_attempt_at <= now())))
                .values(status="running", stage="starting", heartbeat_at=now(),
                        attempts=PipelineJob.attempts + 1, execution_token=token))
            db.commit()
            if not claimed.rowcount:
                return
            job = db.get(PipelineJob, job_id)
            db.expunge(job)
        context = execution_context(lock_fd, token, stop=stop, timeout=timeout)
        context.__enter__()
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=_heartbeat, args=(job_id, heartbeat_stop, token), daemon=True)
        heartbeat.start()
        try:
            result = _perform(job)
        except Exception as exc:
            logger.exception("Pipeline job %s failed", job_id)
            with SessionLocal() as db:
                record = db.scalar(select(PipelineJob).where(*execution_filter(job_id, token)))
                if record is None:
                    return
                record.error = str(exc)[:8000]
                record.heartbeat_at = now()
                retryable = not isinstance(exc, ValueError) and (
                    record.stage in {"sync_bp", "sync_leagues", "select_league"} or
                    isinstance(exc, (OSError, TimeoutError, PipelineInterrupted)) or
                    "timed out" in str(exc).lower()
                )
                if retryable and record.attempts < MAX_ATTEMPTS:
                    record.status = "pending"
                    record.stage = "retry_wait"
                    record.next_attempt_at = now() + timedelta(seconds=30 * (2 ** (record.attempts - 1)))
                else:
                    record.status = "failed"
                    record.stage = "failed"
                    record.finished_at = now()
                db.commit()
        else:
            with SessionLocal() as db:
                record = db.scalar(select(PipelineJob).where(*execution_filter(job_id, token)))
                if record is None:
                    return
                record.status = "completed"
                record.stage = ("Data updated; model training deferred"
                                if result.get("model_update", {}).get("status") == "DEFERRED"
                                else "completed")
                record.result = json.dumps(result, default=str)
                record.error = None
                record.finished_at = now()
                record.heartbeat_at = now()
                db.commit()
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)
            # Fresh trainer commands normally reap their children. Cancellation
            # or failure also clears token-tagged descendants before releasing
            # the inherited lock and allowing another job.
            try:
                from app.services.pipeline_worker import terminate_token
                terminate_token(token)
                with SessionLocal() as db:
                    db.execute(update(PipelineJob).where(PipelineJob.id == job_id,
                        PipelineJob.execution_token == token).values(execution_token=None))
                    db.commit()
            finally:
                context.__exit__(None, None, None)



def recover_jobs(*, force: bool = False, reason: str = "Worker stopped before completing the job") -> bool:
    """Recover only while the execution lock proves no previous job can run.

    The supervisor terminates token-tagged orphan processes before calling this.
    A stale heartbeat alone never authorizes recovery of a live execution.
    """
    init_db()
    with global_pipeline_lock(blocking=False) as lock_fd:
        if lock_fd is None:
            return False
        current = now()
        with SessionLocal() as db:
            statement = select(PipelineJob).where(PipelineJob.status == "running")
            if not force:
                statement = statement.where((PipelineJob.heartbeat_at.is_(None)) |
                                            (PipelineJob.heartbeat_at < current - STALE_AFTER))
            for job in db.scalars(statement):
                job.error = reason
                job.execution_token = None
                if job.attempts >= MAX_ATTEMPTS:
                    job.status, job.stage, job.finished_at = "failed", "failed", current
                else:
                    job.status, job.stage = "pending", "retry_wait"
                    job.next_attempt_at = current + timedelta(seconds=30 * (2 ** max(0, job.attempts - 1)))
            exhausted = db.scalars(select(PipelineJob).where(PipelineJob.status == "pending",
                                                           PipelineJob.attempts >= MAX_ATTEMPTS))
            for job in exhausted:
                job.status, job.stage, job.finished_at = "failed", "failed", current
                job.error = job.error or "Maximum job attempts exhausted"
            # Normalize pending delivery labels from older ledgers.
            db.execute(update(PipelineJob).where(PipelineJob.status == "pending",
                PipelineJob.stage != "retry_wait")
                .values(stage="queued"))
            db.commit()
        return True
