"""Durable SQLite job ledger with RabbitMQ delivery for private maintenance work."""
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

from celery import Celery
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import League, PipelineJob

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=2)
DISPATCH_AFTER = timedelta(minutes=5)
JOB_TIMEOUT_SECONDS = 3 * 60 * 60
LOCK_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline.lock"

celery_app = Celery("kpl_pipeline", broker=get_settings().rabbitmq_url)
celery_app.conf.update(
    task_default_queue="pipeline",
    task_default_queue_type="quorum",
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Job status comes from SQLite. Celery's optional remote-control queues
    # are transient/non-exclusive, which RabbitMQ 4.3 rejects by default.
    worker_enable_remote_control=False,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={"confirm_publish": True, "max_retries": 1},
    beat_schedule={"recover-pipeline-jobs": {"task": "pipeline.recover", "schedule": 30.0}},
)


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
    dispatch_job(db, job)
    return job_data(job)


def dispatch_job(db: Session, job: PipelineJob) -> bool:
    """Best effort. The committed row is the outbox if RabbitMQ is unavailable."""
    try:
        celery_app.send_task("pipeline.run", args=[job.id], task_id=str(uuid4()),
                             retry=False, delivery_mode=2)
    except Exception:
        logger.exception("RabbitMQ dispatch failed for job %s; recovery will retry", job.id)
        job.stage = "waiting_for_broker"
        db.commit()
        return False
    job.dispatched_at = now()
    if job.status == "pending":
        job.stage = "queued"
    db.commit()
    return True


@contextmanager
def global_pipeline_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _update_stage(job_id: str, stage: str) -> None:
    with SessionLocal() as db:
        db.execute(update(PipelineJob).where(PipelineJob.id == job_id, PipelineJob.status == "running")
                   .values(stage=stage, heartbeat_at=now()))
        db.commit()


def _heartbeat(job_id: str, stop: threading.Event) -> None:
    while not stop.wait(20):
        try:
            _update_stage(job_id, _current_stage(job_id))
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
                db.execute(update(PipelineJob).where(PipelineJob.id == job.id)
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


@celery_app.task(name="pipeline.run", ignore_result=True)
def run_job(job_id: str) -> None:
    init_db()
    with global_pipeline_lock():
        with SessionLocal() as db:
            claimed = db.execute(update(PipelineJob)
                .where(PipelineJob.id == job_id, PipelineJob.status == "pending",
                       PipelineJob.attempts < MAX_ATTEMPTS,
                       (PipelineJob.next_attempt_at.is_(None) | (PipelineJob.next_attempt_at <= now())))
                .values(status="running", stage="starting", heartbeat_at=now(),
                        attempts=PipelineJob.attempts + 1))
            db.commit()
            if not claimed.rowcount:
                return
            job = db.get(PipelineJob, job_id)
            db.expunge(job)
        # The solo worker has no enforceable Celery hard task limit. Exiting
        # PID 1 on timeout stops its subprocesses with the container; RabbitMQ
        # redelivers and the stale-job sweep retries within the attempt budget.
        watchdog = threading.Timer(JOB_TIMEOUT_SECONDS, lambda: os._exit(124))
        watchdog.daemon = True
        watchdog.start()
        stop = threading.Event()
        heartbeat = threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True)
        heartbeat.start()
        try:
            result = _perform(job)
        except Exception as exc:
            logger.exception("Pipeline job %s failed", job_id)
            with SessionLocal() as db:
                record = db.get(PipelineJob, job_id)
                record.error = str(exc)[:8000]
                record.heartbeat_at = now()
                retryable = not isinstance(exc, ValueError) and (
                    record.stage in {"sync_bp", "sync_leagues", "select_league"} or
                    isinstance(exc, (OSError, TimeoutError)) or
                    "timed out" in str(exc).lower()
                )
                if retryable and record.attempts < MAX_ATTEMPTS:
                    record.status = "pending"
                    record.stage = "retry_wait"
                    record.next_attempt_at = now() + timedelta(seconds=30 * (2 ** (record.attempts - 1)))
                    record.dispatched_at = None
                else:
                    record.status = "failed"
                    record.stage = "failed"
                    record.finished_at = now()
                db.commit()
        else:
            with SessionLocal() as db:
                record = db.get(PipelineJob, job_id)
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
            watchdog.cancel()
            stop.set()
            heartbeat.join(timeout=2)


@celery_app.task(name="pipeline.recover", ignore_result=True)
def recover_jobs() -> None:
    init_db()
    current = now()
    with SessionLocal() as db:
        stale = list(db.scalars(select(PipelineJob).where(
            PipelineJob.status == "running", PipelineJob.heartbeat_at < current - STALE_AFTER)))
        for job in stale:
            job.error = "Worker stopped before completing the job"
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.stage, job.finished_at = "failed", "failed", current
            else:
                job.status, job.stage, job.dispatched_at = "pending", "retry_wait", None
                job.next_attempt_at = current
        db.commit()
        pending = list(db.scalars(select(PipelineJob).where(
            PipelineJob.status == "pending",
            (PipelineJob.next_attempt_at.is_(None) | (PipelineJob.next_attempt_at <= current)),
            (PipelineJob.dispatched_at.is_(None) | (PipelineJob.dispatched_at < current - DISPATCH_AFTER))
        ).order_by(PipelineJob.created_at).limit(20)))
        for job in pending:
            dispatch_job(db, job)
