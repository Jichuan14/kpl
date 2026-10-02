"""One process-wide admission budget for all public paid-model features."""
from app.config import get_settings
from app.services.coach_rate_limit import CoachRateLimiter
from fastapi import Request


def new_limiter() -> CoachRateLimiter:
    settings = get_settings()
    return CoachRateLimiter(
        per_ip_per_minute=settings.coach_ip_requests_per_minute,
        per_ip_per_day=settings.coach_ip_requests_per_day,
        server_per_minute=settings.coach_server_requests_per_minute,
        server_per_day=settings.coach_server_requests_per_day,
        max_active_per_ip=settings.coach_ip_max_active_requests,
        max_active_server=settings.coach_server_max_active_requests,
    )


rate_limiter = new_limiter()


def limit_provider_requests(request: Request):
    from app.services.public_requests import admission
    with admission(rate_limiter, request, trust_proxy_headers=get_settings().coach_trust_proxy_headers):
        yield
