"""HTTP boundary for the evidence-backed KPL Draft Coach."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from app.agent.service import (
    CoachInput,
    CoachLoopLimitError,
    KimiCoachService,
    KimiConfigurationError,
    provider_retry_after_seconds,
)
from app.agent.scout_report import ScoutReportInput, ScoutReportService
from app.agent.scout_report_cache import scout_report_cache, scout_report_cache_key
from app.database import get_db
from app.config import get_settings
from app.schemas import ApiResponse, CoachLimitsUpdate
from app.services.coach_rate_limit import CoachRateLimiter
from app.services.request_identity import client_key
from app.services.season_teams import validate_season_team_pair

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coach", tags=["coach"])


def _new_rate_limiter() -> CoachRateLimiter:
    settings = get_settings()
    return CoachRateLimiter(
        per_ip_per_minute=settings.coach_ip_requests_per_minute,
        per_ip_per_day=settings.coach_ip_requests_per_day,
        server_per_minute=settings.coach_server_requests_per_minute,
        server_per_day=settings.coach_server_requests_per_day,
        max_active_per_ip=settings.coach_ip_max_active_requests,
        max_active_server=settings.coach_server_max_active_requests,
    )


rate_limiter = _new_rate_limiter()


def _client_key(request: Request) -> str:
    """Use proxy-supplied IPs only when deployment explicitly opts in."""
    return client_key(
        request,
        trust_proxy_headers=get_settings().coach_trust_proxy_headers,
    )


def _is_direct_loopback_request(request: Request) -> bool:
    """Bypass development limits only for a direct localhost request.

    A public deployment can legitimately have a loopback reverse proxy, so a
    trusted-proxy deployment never receives this bypass. Requiring both a
    loopback peer and loopback Host also avoids treating proxied public traffic
    as local testing traffic.
    """
    if get_settings().coach_trust_proxy_headers:
        return False
    peer = request.client.host if request.client else ""
    host = request.url.hostname or ""
    try:
        peer_is_loopback = ip_address(peer).is_loopback
    except ValueError:
        peer_is_loopback = False
    try:
        host_is_loopback = ip_address(host).is_loopback
    except ValueError:
        host_is_loopback = host.casefold() == "localhost"
    return peer_is_loopback and host_is_loopback


def _http_error(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "request_id": request_id,
        },
    )


@router.post("")
def ask_coach(
    body: CoachInput,
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Answer one question with Kimi and approved local evidence tools."""
    request_id = uuid4().hex
    rate_limit_bypassed = _is_direct_loopback_request(request)
    client_identity = _client_key(request)
    if not rate_limit_bypassed:
        decision = rate_limiter.acquire(client_identity)
        if not decision.allowed:
            logger.warning(
                "coach_api_rate_limited",
                extra={"request_id": request_id, "limit": decision.code},
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "coach_rate_limited",
                    "message": "The Draft Coach is busy. Try again shortly.",
                    "request_id": request_id,
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
    else:
        logger.info("coach_api_loopback_rate_limit_bypassed", extra={"request_id": request_id})
    try:
        if body.draft_state is not None:
            teams = validate_season_team_pair(
                db,
                body.league_id,
                body.draft_state.blue_team_id,
                body.draft_state.red_team_id,
            )
            body.draft_state.blue_team_name = str(teams["blue"]["team_name"])
            body.draft_state.red_team_name = str(teams["red"]["team_name"])
        result = KimiCoachService().ask(body, request_id=request_id)
    except ValueError as exc:
        _http_error(
            status_code=422,
            code="invalid_team_context",
            message=str(exc),
            request_id=request_id,
        )
    except (KimiConfigurationError, AuthenticationError) as exc:
        logger.error(
            "coach_api_unavailable",
            extra={
                "request_id": request_id,
                "error_type": type(exc).__name__,
            },
        )
        _http_error(
            status_code=503,
            code="coach_unavailable",
            message="The Draft Coach provider is not configured or authenticated.",
            request_id=request_id,
        )
    except RateLimitError as exc:
        retry_after = provider_retry_after_seconds(exc)
        logger.warning(
            "coach_api_rate_limited",
            extra={
                "request_id": request_id,
                "error_type": type(exc).__name__,
                "retry_after_seconds": retry_after,
            },
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "coach_rate_limited",
                "message": "The Draft Coach is temporarily rate limited. Try again later.",
                "request_id": request_id,
            },
            headers={"Retry-After": str(retry_after)},
        )
    except APITimeoutError as exc:
        logger.warning(
            "coach_api_timeout",
            extra={"request_id": request_id, "error_type": type(exc).__name__},
        )
        _http_error(
            status_code=504,
            code="coach_timeout",
            message="The Draft Coach provider timed out. Try again.",
            request_id=request_id,
        )
    except (APIConnectionError, APIStatusError) as exc:
        logger.warning(
            "coach_api_provider_failure",
            extra={
                "request_id": request_id,
                "error_type": type(exc).__name__,
                "provider_status": getattr(exc, "status_code", None),
            },
        )
        _http_error(
            status_code=502,
            code="coach_provider_error",
            message="The Draft Coach provider could not complete the request.",
            request_id=request_id,
        )
    except (CoachLoopLimitError, RuntimeError) as exc:
        logger.warning(
            "coach_api_incomplete",
            extra={"request_id": request_id, "error_type": type(exc).__name__},
        )
        _http_error(
            status_code=502,
            code="coach_incomplete",
            message="The Draft Coach could not finish within its safety limits.",
            request_id=request_id,
        )
    except Exception as exc:
        # Do not log the exception string: provider errors can contain request
        # details. The type and request ID are enough for safe correlation.
        logger.error(
            "coach_api_internal_failure",
            extra={"request_id": request_id, "error_type": type(exc).__name__},
        )
        _http_error(
            status_code=500,
            code="coach_internal_error",
            message="The Draft Coach encountered an internal error.",
            request_id=request_id,
        )
    finally:
        if not rate_limit_bypassed:
            rate_limiter.release(client_identity)

    evidence: list[dict[str, object]] = []
    warnings: list[str] = []
    for call in result["tool_calls"]:
        if call["success"]:
            evidence.append(
                {
                    "tool": call["name"],
                    "data": call["result"],
                }
            )
        else:
            warnings.append(f"{call['name']}: {call['error']}")

    return ApiResponse(
        message="coach response completed",
        data={
            "request_id": result["request_id"],
            "model": result["model"],
            "answer": result["answer"],
            "evidence": evidence,
            "warnings": warnings,
            "usage": result["usage"],
        },
    )


@router.post("/scout-report")
def prepare_scout_report(
    body: ScoutReportInput,
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Build a fixed, evidence-backed preparation report for the selected matchup."""
    request_id = uuid4().hex
    rate_limit_bypassed = _is_direct_loopback_request(request)
    client_identity = _client_key(request)
    if not rate_limit_bypassed:
        decision = rate_limiter.acquire(client_identity)
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "coach_rate_limited",
                    "message": "The Draft Coach is busy. Try again shortly.",
                    "request_id": request_id,
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
    try:
        teams = validate_season_team_pair(
            db,
            body.league_id,
            body.blue_team_id,
            body.red_team_id,
        )
        # Names always come from the selected season, never from the browser.
        trusted_body = body.model_copy(
            update={
                "blue_team_name": str(teams["blue"]["team_name"]),
                "red_team_name": str(teams["red"]["team_name"]),
            }
        )
        cache_key = scout_report_cache_key(
            league_id=trusted_body.league_id,
            blue_team_id=trusted_body.blue_team_id,
            red_team_id=trusted_body.red_team_id,
            language=trusted_body.language,
        )
        result = scout_report_cache.get_or_generate(
            cache_key,
            lambda: ScoutReportService().generate(trusted_body, request_id=request_id),
        )
        # A cached report is still a distinct HTTP request for observability.
        result["request_id"] = request_id
    except ValueError as exc:
        _http_error(
            status_code=422,
            code="invalid_team_context",
            message=str(exc),
            request_id=request_id,
        )
    except (KimiConfigurationError, AuthenticationError) as exc:
        logger.error("scout_report_unavailable", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _http_error(
            status_code=503,
            code="coach_unavailable",
            message="The Draft Coach provider is not configured or authenticated.",
            request_id=request_id,
        )
    except RateLimitError as exc:
        retry_after = provider_retry_after_seconds(exc)
        logger.warning(
            "scout_report_rate_limited",
            extra={
                "request_id": request_id,
                "error_type": type(exc).__name__,
                "retry_after_seconds": retry_after,
            },
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "coach_rate_limited",
                "message": "The Draft Coach provider is temporarily rate limited. Try again later.",
                "request_id": request_id,
            },
            headers={"Retry-After": str(retry_after)},
        )
    except APITimeoutError as exc:
        logger.warning("scout_report_timeout", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _http_error(status_code=504, code="coach_timeout", message="The Draft Coach provider timed out. Try again.", request_id=request_id)
    except (APIConnectionError, APIStatusError) as exc:
        logger.warning("scout_report_provider_failure", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _http_error(status_code=502, code="coach_provider_error", message="The Draft Coach provider could not complete the request.", request_id=request_id)
    except RuntimeError as exc:
        logger.warning("scout_report_incomplete", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _http_error(status_code=502, code="coach_incomplete", message="The Draft Coach could not finish the report.", request_id=request_id)
    except Exception as exc:
        logger.error("scout_report_internal_failure", extra={"request_id": request_id, "error_type": type(exc).__name__})
        _http_error(status_code=500, code="coach_internal_error", message="The Draft Coach encountered an internal error.", request_id=request_id)
    finally:
        if not rate_limit_bypassed:
            rate_limiter.release(client_identity)

    evidence = [
        {"tool": call["name"], "subject": call["subject"], "data": call["result"]}
        for call in result["tool_calls"]
        if call["success"]
    ]
    warnings = [*result["warnings"], *[
        f"{call['subject']}: {call['error']}"
        for call in result["tool_calls"]
        if not call["success"]
    ]]
    return ApiResponse(
        message="scout report completed",
        data={
            "request_id": result["request_id"],
            "model": result["model"],
            "answer": result["answer"],
            "evidence": evidence,
            "warnings": warnings,
            "usage": result["usage"],
            "priority_heroes": result["priority_heroes"],
        },
    )


@router.get("/usage")
def coach_usage() -> ApiResponse:
    """Return privacy-safe, process-local Draft Coach capacity metrics."""
    return ApiResponse(message="coach usage retrieved", data=rate_limiter.usage())


@router.put("/limits")
def update_coach_limits(body: CoachLimitsUpdate) -> ApiResponse:
    """Update process-local limits from the private management interface."""
    rate_limiter.update_limits(
        per_ip_per_minute=body.ip_requests_per_minute,
        per_ip_per_day=body.ip_requests_per_day,
        server_per_minute=body.server_requests_per_minute,
        server_per_day=body.server_requests_per_day,
        max_active_per_ip=body.ip_max_active_requests,
        max_active_server=body.server_max_active_requests,
    )
    return ApiResponse(message="coach limits updated", data=rate_limiter.usage())
