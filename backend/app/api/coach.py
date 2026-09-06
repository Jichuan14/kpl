"""HTTP boundary for the evidence-backed KPL Draft Coach."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from app.agent.conversation import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    SqliteConversationStore,
    build_checkpointer,
    extract_entities,
    scoped_gate_reference,
    board_fingerprint,
)
from app.agent.errors import (
    CoachClassificationError,
    CoachConversationError,
    CoachUserInputError,
    localized_user_error,
)
from app.agent.evidence import ANSWER_VERSION, build_evidence_cards
from app.agent.scope import contains_chinese
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

PROGRESS_ZH = {
    "Checking the selected season": "正在检查所选赛季",
    "Planning the evidence needed": "正在规划所需证据",
    "Collecting supporting data": "正在收集支持数据",
    "Reviewing the evidence": "正在核对证据",
    "Checking the answer against the evidence": "正在用证据核对回答",
    "Repairing the answer from verified evidence": "正在根据已核实证据修正回答",
}

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
_coach_service: KimiCoachService | None = None


def get_coach_service() -> KimiCoachService:
    """Reuse one service so the compiled graph is not rebuilt per request."""
    global _coach_service
    if _coach_service is None:
        settings = get_settings()
        persistent = (
            settings.coach_enable_conversations
            and settings.coach_orchestration == "langgraph"
        )
        _coach_service = KimiCoachService(
            settings=settings,
            conversation_store=(
                SqliteConversationStore(settings.coach_conversation_path)
                if persistent
                else None
            ),
            checkpointer=(
                build_checkpointer(True, settings.coach_checkpoint_path)
                if persistent
                else None
            ),
        )
    return _coach_service


def reset_coach_service() -> None:
    global _coach_service
    if _coach_service is not None:
        _coach_service.close()
    _coach_service = None


def reset_coach_rate_limiter() -> None:
    """Reset process-local counters for isolated application tests."""
    global rate_limiter
    rate_limiter = _new_rate_limiter()


def _public_coach_data(result: dict) -> dict:
    evidence: list[dict[str, object]] = []
    warnings: list[str] = list(result.get("warnings") or [])
    seen_warnings = set(warnings)
    for call in result.get("tool_calls") or []:
        if call.get("success"):
            evidence.append({"tool": call["name"], "data": call["result"]})
        else:
            warning = f"{call['name']}: {call.get('error')}"
            if warning not in seen_warnings:
                warnings.append(warning)
                seen_warnings.add(warning)
    cards = result.get("evidence_cards")
    if cards is None:
        cards = build_evidence_cards(result.get("tool_calls") or [])
    return {
        "request_id": result["request_id"],
        "model": result["model"],
        "answer": result["answer"],
        "evidence": evidence,
        "warnings": warnings,
        "usage": result["usage"],
        "answer_version": result.get("answer_version", ANSWER_VERSION),
        "response_mode": result.get("response_mode") or "quick",
        "status": result.get("status") or "complete",
        "sections": result.get("sections") or [],
        "evidence_cards": cards,
        "coverage": result.get("coverage") or {},
        "follow_up_actions": result.get("follow_up_actions") or [],
        "conversation_id": result.get("conversation_id"),
    }


def _attach_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def _finish_conversation_turn(
    service: KimiCoachService,
    conversation_record,
    *,
    request_id: str,
    body: CoachInput,
    result: dict,
    payload: dict,
) -> None:
    if conversation_record is None:
        return
    payload["conversation_id"] = conversation_record.conversation_id
    coverage = result.get("coverage") or {}
    requested = list(coverage.get("requested") or []) if isinstance(coverage, dict) else []
    service.conversation_store.finish_turn(
        conversation_record,
        result=payload,
        turn={
            "request_id": request_id,
            "question": body.message,
            "intent": requested[0] if requested else None,
            "intents": requested,
            "entities": extract_entities(
                result.get("tool_calls") or [],
                body.draft_state.model_dump(mode="json") if body.draft_state else None,
            ),
            "league_id": body.league_id,
            "board_fingerprint": board_fingerprint(
                body.league_id,
                body.draft_state.model_dump(mode="json") if body.draft_state else None,
            ),
            "status": payload.get("status"),
            "pending_clarification": (
                None
                if payload.get("status") != "needs_clarification"
                else "clarification"
            ),
        },
    )


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
    response: Response,
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Answer one question with Kimi and approved local evidence tools."""
    request_id = uuid4().hex
    chinese = contains_chinese(body.message)
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
                    "message": localized_user_error(
                        "coach_rate_limited",
                        "The Draft Coach is busy. Try again shortly.",
                        chinese=chinese,
                    ),
                    "request_id": request_id,
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
    else:
        logger.info("coach_api_loopback_rate_limit_bypassed", extra={"request_id": request_id})
    settings = get_settings()
    service = None
    session_id = None
    conversation_record = None
    conversation_ref = None
    completed = False
    try:
        service = get_coach_service()
        session_id = service.conversation_store.ensure_session(
            request.cookies.get(SESSION_COOKIE)
        )
        _attach_session_cookie(response, session_id)
        if body.draft_state is not None:
            teams = validate_season_team_pair(
                db,
                body.league_id,
                body.draft_state.blue_team_id,
                body.draft_state.red_team_id,
            )
            body.draft_state.blue_team_name = str(teams["blue"]["team_name"])
            body.draft_state.red_team_name = str(teams["red"]["team_name"])
        if (
            settings.coach_enable_conversations
            and settings.coach_orchestration == "langgraph"
        ):
            conversation_record = service.conversation_store.get_or_create(
                body.conversation_id,
                session_id,
            )
            cached = service.conversation_store.begin_turn(
                conversation_record,
                client_request_id=body.client_request_id,
                request_id=request_id,
            )
            if cached is not None:
                completed = True
                return ApiResponse(message="coach response completed", data=cached)
            conversation_ref = scoped_gate_reference(
                conversation_record.to_public_ref(),
                current_league_id=body.league_id,
                current_board=board_fingerprint(
                    body.league_id,
                    body.draft_state.model_dump(mode="json") if body.draft_state else None,
                ),
            )
        result = service.ask(
            body,
            request_id=request_id,
            conversation_ref=conversation_ref,
            conversation_id=(
                conversation_record.conversation_id if conversation_record else None
            ),
        )
        completed = True
    except CoachUserInputError as exc:
        _http_error(
            status_code=422,
            code=exc.code,
            message=localized_user_error(exc.code, exc.message, chinese=chinese),
            request_id=request_id,
        )
    except CoachConversationError as exc:
        _http_error(
            status_code=404 if exc.code == "conversation_not_found" else 409,
            code=exc.code,
            message=localized_user_error(exc.code, exc.message, chinese=chinese),
            request_id=request_id,
        )
    except CoachClassificationError as exc:
        _http_error(
            status_code=502,
            code=exc.code,
            message=localized_user_error(exc.code, exc.message, chinese=chinese),
            request_id=request_id,
        )
    except ValueError as exc:
        _http_error(
            status_code=422,
            code="invalid_team_context",
            message=localized_user_error(
                "invalid_team_context",
                str(exc),
                chinese=chinese,
            ),
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
        if service is not None and conversation_record is not None and not completed:
            service.conversation_store.abort_turn(conversation_record)
        if not rate_limit_bypassed:
            rate_limiter.release(client_identity)

    payload = _public_coach_data(result)
    _finish_conversation_turn(
        service,
        conversation_record,
        request_id=request_id,
        body=body,
        result=result,
        payload=payload,
    )
    return ApiResponse(message="coach response completed", data=payload)


@router.post("/stream")
def stream_coach(
    body: CoachInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Stream allowlisted progress events, then the same validated JSON result."""
    settings = get_settings()
    if not settings.coach_enable_streaming:
        _http_error(
            status_code=404,
            code="streaming_disabled",
            message="Streaming is not enabled.",
            request_id=uuid4().hex,
        )
    request_id = uuid4().hex
    chinese = contains_chinese(body.message)
    rate_limit_bypassed = _is_direct_loopback_request(request)
    client_identity = _client_key(request)
    if not rate_limit_bypassed:
        decision = rate_limiter.acquire(client_identity)
        if not decision.allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "coach_rate_limited",
                    "message": localized_user_error(
                        "coach_rate_limited",
                        "The Draft Coach is busy. Try again shortly.",
                        chinese=chinese,
                    ),
                    "request_id": request_id,
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )

    service = None
    conversation_record = None
    conversation_ref = None
    cached = None
    try:
        service = get_coach_service()
        session_id = service.conversation_store.ensure_session(
            request.cookies.get(SESSION_COOKIE)
        )
        if body.draft_state is not None:
            teams = validate_season_team_pair(
                db,
                body.league_id,
                body.draft_state.blue_team_id,
                body.draft_state.red_team_id,
            )
            body.draft_state.blue_team_name = str(teams["blue"]["team_name"])
            body.draft_state.red_team_name = str(teams["red"]["team_name"])
        if (
            settings.coach_enable_conversations
            and settings.coach_orchestration == "langgraph"
        ):
            conversation_record = service.conversation_store.get_or_create(
                body.conversation_id,
                session_id,
            )
            cached = service.conversation_store.begin_turn(
                conversation_record,
                client_request_id=body.client_request_id,
                request_id=request_id,
            )
            conversation_ref = scoped_gate_reference(
                conversation_record.to_public_ref(),
                current_league_id=body.league_id,
                current_board=board_fingerprint(
                    body.league_id,
                    body.draft_state.model_dump(mode="json") if body.draft_state else None,
                ),
            )
    except CoachConversationError as exc:
        if not rate_limit_bypassed:
            rate_limiter.release(client_identity)
        _http_error(
            status_code=404 if exc.code == "conversation_not_found" else 409,
            code=exc.code,
            message=localized_user_error(exc.code, exc.message, chinese=chinese),
            request_id=request_id,
        )
    except (KimiConfigurationError, AuthenticationError):
        if not rate_limit_bypassed:
            rate_limiter.release(client_identity)
        _http_error(
            status_code=503,
            code="coach_unavailable",
            message="The Draft Coach provider is not configured or authenticated.",
            request_id=request_id,
        )
    except ValueError as exc:
        if not rate_limit_bypassed:
            rate_limiter.release(client_identity)
        _http_error(
            status_code=422,
            code="invalid_team_context",
            message=localized_user_error("invalid_team_context", str(exc), chinese=chinese),
            request_id=request_id,
        )

    def generate():
        import json
        from app.agent.graph import (
            PROGRESS_BY_NODE,
            graph_recursion_limit,
            initial_coach_state,
        )
        from app.agent.runtime import create_budget
        from app.agent.scope import normalize_gate_message
        from app.agent.service import reset_request_budget, set_request_budget

        seq = 0
        budget = create_budget(
            request_id,
            deadline_seconds=settings.coach_request_deadline_seconds,
            reserve_seconds=settings.coach_finalize_reserve_seconds,
        )

        def emit(event_type: str, payload: dict) -> str:
            nonlocal seq
            seq += 1
            event = {
                "v": 1,
                "seq": seq,
                "type": event_type,
                "request_id": request_id,
                **payload,
            }
            return json.dumps(event, ensure_ascii=False) + "\n"

        completed = False
        try:
            yield emit("accepted", {"status": "accepted"})
            if cached is not None:
                completed = True
                yield emit("result", {"data": cached})
                return
            graph = service.compiled_coach_graph()
            emitted_result = False
            graph_updates = iter(graph.stream(
                initial_coach_state(
                    body,
                    request_id=request_id,
                    normalized_message=normalize_gate_message(body.message),
                    usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    conversation_ref=conversation_ref,
                    conversation_id=(
                        conversation_record.conversation_id
                        if conversation_record is not None
                        else None
                    ),
                ),
                config={
                    "recursion_limit": graph_recursion_limit(
                        service.settings.kimi_max_tool_rounds
                    ),
                    **(
                        {
                            "configurable": {
                                "thread_id": conversation_record.conversation_id
                            }
                        }
                        if conversation_record is not None
                        else {}
                    ),
                },
                stream_mode="updates",
            ))
            while True:
                token = set_request_budget(budget)
                try:
                    update = next(graph_updates)
                except StopIteration:
                    break
                finally:
                    reset_request_budget(token)
                if not isinstance(update, dict):
                    continue
                for node_name, payload in update.items():
                    progress = PROGRESS_BY_NODE.get(node_name)
                    if progress:
                        if chinese:
                            progress = PROGRESS_ZH.get(progress, progress)
                        yield emit("progress", {"message": progress})
                    if node_name == "register_evidence":
                        cards = [
                            record.get("card")
                            for record in (payload or {}).get("evidence_records") or []
                            if record.get("card")
                        ]
                        if cards:
                            yield emit("evidence", {"evidence_cards": cards})
                    if node_name == "finalize" and (payload or {}).get("result"):
                        result = payload["result"]
                        public_payload = _public_coach_data(result)
                        _finish_conversation_turn(
                            service,
                            conversation_record,
                            request_id=request_id,
                            body=body,
                            result=result,
                            payload=public_payload,
                        )
                        yield emit(
                            "result",
                            {"data": public_payload},
                        )
                        emitted_result = True
                        completed = True
            if not emitted_result and not budget.cancelled():
                yield emit(
                    "error",
                    {
                        "code": "coach_incomplete",
                        "message": localized_user_error(
                            "coach_incomplete",
                            "The Draft Coach could not finish within its safety limits.",
                            chinese=chinese,
                        ),
                    },
                )
        except GeneratorExit:
            budget.cancel()
            raise
        except CoachUserInputError as exc:
            yield emit(
                "error",
                {
                    "code": exc.code,
                    "message": localized_user_error(exc.code, exc.message, chinese=chinese),
                },
            )
        except Exception:
            yield emit(
                "error",
                {
                    "code": "coach_provider_error",
                    "message": localized_user_error(
                        "coach_provider_error",
                        "The Draft Coach provider could not complete the request.",
                        chinese=chinese,
                    ),
                },
            )
        finally:
            if conversation_record is not None and not completed:
                service.conversation_store.abort_turn(conversation_record)
            if not rate_limit_bypassed:
                rate_limiter.release(client_identity)

    stream_response = StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
    _attach_session_cookie(stream_response, session_id)
    return stream_response


@router.post("/conversation/clear")
def clear_coach_conversation(request: Request, response: Response) -> ApiResponse:
    """Clear server-side conversation state for the current session."""
    service = get_coach_service()
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        service.clear_conversation_session(session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return ApiResponse(message="coach conversation cleared", data={"cleared": True})


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
