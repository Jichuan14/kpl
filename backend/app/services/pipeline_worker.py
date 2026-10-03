"""Process cleanup helpers and optional offline queue diagnostics.

Production updates are executed by pipeline_runner inside the API. This CLI
is only for diagnostics with the API stopped; both share the ownership lock.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[2]
LOCK_PATH = BACKEND / 'data' / 'pipeline.lock'
LOCK_ENV = 'KPL_PIPELINE_LOCK_FD'
TOKEN_ENV = 'KPL_JOB_EXECUTION_TOKEN'
STOP = False


def database_path() -> str:
    # Match Settings' local .env support without retaining the application.
    from dotenv import load_dotenv
    load_dotenv(BACKEND / '.env', override=False)
    url = os.environ.get('DATABASE_URL', f'sqlite:///{BACKEND / "data" / "kpl_bp.db"}')
    if not url.startswith('sqlite:///') or url.endswith(':memory:'):
        raise ValueError('The durable pipeline worker requires a file-backed SQLite database')
    return url[len('sqlite:///'):]


def due_job(path: str) -> str | None:
    with sqlite3.connect(path, timeout=10) as db:
        row = db.execute("""SELECT id FROM pipeline_jobs WHERE status='pending'
            AND attempts < 3 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY created_at, id LIMIT 1""", (time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),)).fetchone()
    return row[0] if row else None


def running_tokens(path: str) -> list[str]:
    with sqlite3.connect(path, timeout=10) as db:
        return [r[0] for r in db.execute("SELECT execution_token FROM pipeline_jobs WHERE execution_token IS NOT NULL")]


def token_processes(token: str) -> list[int]:
    """Find descendants even after reparenting or creating their own sessions."""
    tag = (TOKEN_ENV + '=' + token).encode()
    result = []
    if not Path('/proc').is_dir():
        # macOS local development: inspect environment internally; never log it.
        output = subprocess.check_output(['ps', 'eww', '-axo', 'pid=,command='], text=True)
        for line in output.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and tag.decode() in fields[1].split():
                if int(fields[0]) != os.getpid():
                    result.append(int(fields[0]))
        return result
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            if tag in (entry / 'environ').read_bytes().split(b'\0'):
                if int(entry.name) != os.getpid():
                    result.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return result


def terminate_token(token: str, root: subprocess.Popen | None = None, grace: float = 5) -> None:
    pids = token_processes(token)
    if root is not None and root.poll() is None:
        pids.append(root.pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in set(pids):
            try:
                # Pipeline subprocesses may have their own process groups.
                if os.getpgid(pid) == pid:
                    os.killpg(pid, sig)
                else:
                    os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + (grace if sig == signal.SIGTERM else 2)
        while time.monotonic() < deadline:
            if root is not None:
                root.poll()
            pids = token_processes(token)
            if root is not None and root.poll() is None:
                pids.append(root.pid)
            if not pids:
                return
            time.sleep(.1)
    if pids:
        raise RuntimeError('Previous job processes could not be stopped; refusing another execution')


def child(arguments: list[str], fd: int | None = None, token: str | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    if fd is not None:
        env[LOCK_ENV] = str(fd)
    if token is not None:
        env[TOKEN_ENV] = token
    return subprocess.Popen([sys.executable, '-m', 'app.services.pipeline_worker', *arguments],
                            env=env, pass_fds=(() if fd is None else (fd,)), start_new_session=True)


def request_stop(*_args):
    global STOP
    STOP = True


def supervise(path: str, poll: float = 2, timeout: float = 10800, once: bool = False) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with (LOCK_PATH.parent / 'pipeline-worker.lock').open('a+') as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A pipeline supervisor already owns this workspace')
        if child(['--initialize']).wait() != 0:
            raise RuntimeError('Could not initialize the job database')
        for token in running_tokens(path):
            terminate_token(token)
        needs_recovery = True
        while not STOP:
            # Close the descriptor without explicitly unlocking: descendants
            # retaining it keep the lock after an unexpected supervisor exit.
            with LOCK_PATH.open('a+') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if once:
                        return
                    time.sleep(poll)
                    continue
                if needs_recovery:
                    if child(['--recover'], lock.fileno()).wait() != 0:
                        raise RuntimeError('Could not recover interrupted jobs')
                    needs_recovery = False
                job = due_job(path)
                if job is not None:
                    token = str(uuid4())
                    print(f'Executing pipeline job {job}', flush=True)
                    process = child(['--job', job], lock.fileno(), token)
                    deadline = time.monotonic() + timeout
                    while process.poll() is None and not STOP and time.monotonic() < deadline:
                        time.sleep(.2)
                    reason = 'Job exceeded the three-hour execution deadline' if time.monotonic() >= deadline else 'Worker stopped before completing the job'
                    terminate_token(token, process)
                    process.wait()
                    print(f'Pipeline process exited for {job}: {process.returncode}', flush=True)
                    # Normal completion is untouched; only remaining running
                    # records are recovered with the original retry limit.
                    if child(['--recover', '--reason', reason], lock.fileno()).wait() != 0:
                        raise RuntimeError('Could not recover interrupted job')
                    # Reap orphaned children when running as container PID 1.
                    while True:
                        try:
                            pid, _ = os.waitpid(-1, os.WNOHANG)
                            if not pid:
                                break
                        except ChildProcessError:
                            break
            if once:
                return
            time.sleep(poll)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--reason', default='Worker stopped before completing the job')
    parser.add_argument('--job')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--poll', type=float, default=2)
    parser.add_argument('--timeout', type=float, default=10800)
    args = parser.parse_args()
    if args.initialize:
        from app.database import init_db
        init_db()
    elif args.recover:
        from app.services.pipeline_jobs import recover_jobs
        if not recover_jobs(force=True, reason=args.reason):
            raise RuntimeError('An execution is still holding the pipeline lock')
    elif args.job:
        from app.services.pipeline_jobs import run_job
        run_job(args.job)
    else:
        if args.poll <= 0 or args.timeout <= 0:
            parser.error('Polling interval and timeout must be positive')
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        supervise(database_path(), args.poll, args.timeout, args.once)


if __name__ == '__main__':
    main()
