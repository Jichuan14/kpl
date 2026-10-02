"""Bound anonymous public work and issue server-authenticated visitor IDs."""
from contextlib import contextmanager
from hashlib import sha256
import hmac
import secrets
import os
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time

from fastapi import HTTPException, Request, Response

from app.config import get_settings
from app.services.coach_rate_limit import CoachRateLimiter
from app.services.request_identity import client_key

COOKIE = "draft_atlas_visitor"
SESSION_SECONDS = 365 * 86400


@lru_cache(maxsize=1)
def _persistent_secret(filename: str) -> bytes:
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Publish a complete 0600 key once, even if two processes initialize.
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(secrets.token_bytes(32))
            temporary.flush(); os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                pass
        finally:
            temporary_path.unlink()
    key = path.read_bytes()
    if len(key) < 32:
        raise RuntimeError("Public visitor signing key is invalid")
    return key


def _limiter(minute, daily, server_minute, server_daily):
    return CoachRateLimiter(
        per_ip_per_minute=minute, per_ip_per_day=daily,
        server_per_minute=server_minute, server_per_day=server_daily,
        max_active_per_ip=2, max_active_server=20,
    )


live_rate_limiter = _limiter(60, 10_000, 600, 100_000)
write_rate_limiter = _limiter(30, 500, 300, 20_000)


@contextmanager
def admission(limiter, request, *, trust_proxy_headers=None):
    trust = get_settings().public_trust_proxy_headers if trust_proxy_headers is None else trust_proxy_headers
    key = client_key(request, trust_proxy_headers=trust)
    decision = limiter.acquire(key)
    if not decision.allowed:
        raise HTTPException(429, detail="Too many requests. Try again shortly.",
                            headers={"Retry-After": str(decision.retry_after_seconds)})
    try:
        yield
    finally:
        limiter.release(key)


def limit_live_requests(request: Request):
    with admission(live_rate_limiter, request):
        yield


def limit_public_writes(request: Request):
    with admission(write_rate_limiter, request):
        yield


def public_visitor(request: Request, response: Response) -> str:
    settings = get_settings()
    configured = settings.public_session_secret
    key = configured.get_secret_value().encode() if configured else _persistent_secret(settings.public_session_key_path)
    supplied = request.cookies.get(COOKIE, "")
    token = None
    if len(supplied) <= 160:
        parts = supplied.split(".")
        if len(parts) == 3:
            identifier, issued, signature = parts
            payload = f"{identifier}.{issued}"
            expected = hmac.new(key, payload.encode(), sha256).hexdigest()
            try:
                valid_age = 0 <= time() - int(issued) <= SESSION_SECONDS
            except ValueError:
                valid_age = False
            if (len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier)
                    and len(signature) == 64 and all(c in "0123456789abcdef" for c in signature)
                    and valid_age and hmac.compare_digest(signature, expected)):
                token = identifier
    if token is None:
        token = secrets.token_hex(16)
        payload = f"{token}.{int(time())}"
        signature = hmac.new(key, payload.encode(), sha256).hexdigest()
        response.set_cookie(COOKIE, f"{payload}.{signature}", max_age=SESSION_SECONDS,
                            httponly=True, samesite="lax", path="/",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return sha256(token.encode()).hexdigest()
