"""Per-execution state shared by API job code, never by unrelated requests."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import threading
import time

LOCK_FD_ENV = 'KPL_PIPELINE_LOCK_FD'
TOKEN_ENV = 'KPL_JOB_EXECUTION_TOKEN'


class PipelineInterrupted(RuntimeError):
    pass


@dataclass
class Execution:
    fd: int
    token: str
    stop: threading.Event | None = None
    deadline: float | None = None


CURRENT: ContextVar[Execution | None] = ContextVar('pipeline_execution', default=None)


def lock_fd() -> int | None:
    execution = CURRENT.get()
    if execution is not None:
        return execution.fd
    value = os.environ.get(LOCK_FD_ENV)
    return int(value) if value is not None else None


def execution_token() -> str | None:
    execution = CURRENT.get()
    return execution.token if execution is not None else os.environ.get(TOKEN_ENV)


def check_interrupted() -> None:
    execution = CURRENT.get()
    if execution is not None:
        if execution.stop is not None and execution.stop.is_set():
            raise PipelineInterrupted('API stopped before completing the update')
        if execution.deadline is not None and time.monotonic() >= execution.deadline:
            raise PipelineInterrupted('Job exceeded the three-hour execution deadline')


@contextmanager
def execution_context(fd: int, token: str, *, stop=None, timeout=None):
    state = CURRENT.set(Execution(fd, token, stop, None if timeout is None else time.monotonic() + timeout))
    try:
        yield
    finally:
        CURRENT.reset(state)


def subprocess_options() -> dict:
    """Give only this job's subprocesses their lock and execution identity."""
    check_interrupted()
    env = {**os.environ, 'MALLOC_ARENA_MAX': '2'}
    fd, token = lock_fd(), execution_token()
    if fd is not None:
        env[LOCK_FD_ENV] = str(fd)
    if token is not None:
        env[TOKEN_ENV] = token
    return {'env': env, 'pass_fds': () if fd is None else (fd,)}
