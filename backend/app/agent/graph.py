"""LangGraph orchestration for the KPL Draft Coach.

This module replaces only the request-scoped tool loop. The Kimi client, tool
registry, scope policy, prompts, and HTTP contract stay outside the graph.
Runtime objects (client, settings, tool functions, clocks) are closed over by
nodes or held in a request-local ContextVar and are never stored in graph state.
"""

from __future__ import annotations

import logging
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.agent.answer_validation import (
    clarification_answer,
    coverage_from_plan,
    limitation_answer,
    missing_context_kind,
    validate_answer,
)
from app.agent.conversation import (
    board_fingerprint,
    extract_entities,
    resolve_follow_up,
    scoped_gate_reference,
)
from app.agent.errors import CoachClassificationError, CoachUserInputError
from app.agent.evidence import ANSWER_VERSION, build_evidence_records, collect_warnings
from app.agent.runtime import create_budget
from app.agent.scope import (
    MAX_GATE_MESSAGE_LENGTH,
    ScopeDecision,
    contains_chinese,
    denial_answer,
    missing_live_board,
    normalize_gate_message,
)
from app.agent.service import (
    CoachInput,
    CoachLoopLimitError,
    KimiCoachService,
    current_request_budget,
    reset_request_budget,
    set_request_budget,
)
from app.agent.workflows import follow_up_actions, plan_evidence

logger = logging.getLogger(__name__)

GRAPH_NODE_NAMES = (
    "prepare_turn",
    "scope_gate",
    "clarify",
    "plan_evidence",
    "prepare_context",
    "call_model",
    "enforce_limits",
    "execute_tools",
    "register_evidence",
    "finalize_budget_partial",
    "sanitize_answer",
    "validate_answer",
    "bounded_repair",
    "finalize",
    "record_completed_turn",
)

PROGRESS_BY_NODE = {
    "prepare_turn": "Checking the selected season",
    "scope_gate": "Checking the selected season",
    "plan_evidence": "Planning the evidence needed",
    "execute_tools": "Collecting supporting data",
    "register_evidence": "Reviewing the evidence",
    "validate_answer": "Checking the answer against the evidence",
    "bounded_repair": "Repairing the answer from verified evidence",
}


class CoachState(TypedDict, total=False):
    """Serializable, request-scoped coach state.

    Limits, allowed tools, league_id, and draft visibility are set by Python
    nodes. The model cannot write these fields.
    """

    request: dict[str, Any]
    request_id: str
    normalized_message: str
    response_mode: str
    conversation_id: str | None
    client_request_id: str | None
    conversation_ref: dict[str, Any]
    scope_decision: dict[str, Any]
    allowed_tools: list[str]
    evidence_plan: dict[str, Any]
    messages: list[dict[str, Any]]
    executed_tools: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    evidence_records: list[dict[str, Any]]
    coverage: dict[str, Any]
    tool_rounds: int
    total_tool_calls: int
    repair_count: int
    usage: dict[str, int]
    answer: str
    sections: list[dict[str, str]]
    follow_up_actions: list[dict[str, str]]
    status: str
    error: str | None
    allow_draft_context: bool
    dropped_unrelated: bool
    missing_live_board: bool
    truncated: bool
    result: dict[str, Any]


def initial_coach_state(
    request: CoachInput,
    *,
    request_id: str,
    normalized_message: str,
    usage: dict[str, int],
    conversation_ref: dict[str, Any] | None = None,
    conversation_id: str | None = None,
) -> CoachState:
    """Build the request-scoped graph input without runtime dependencies."""
    return {
        "request": request.model_dump(mode="json"),
        "request_id": request_id,
        "normalized_message": normalized_message,
        "response_mode": request.response_mode,
        "conversation_id": conversation_id,
        "client_request_id": request.client_request_id,
        "conversation_ref": dict(conversation_ref or {}),
        "scope_decision": {},
        "allowed_tools": [],
        "evidence_plan": {},
        "messages": [],
        "executed_tools": [],
        "pending_tool_calls": [],
        "evidence_records": [],
        "coverage": {},
        "tool_rounds": 0,
        "total_tool_calls": 0,
        "repair_count": 0,
        "usage": dict(usage),
        "answer": "",
        "sections": [],
        "follow_up_actions": [],
        "status": "",
        "error": None,
        "allow_draft_context": False,
        "dropped_unrelated": False,
        "missing_live_board": False,
        "truncated": False,
    }


def _as_tool_call(record: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=record["id"],
        function=SimpleNamespace(
            name=record["name"],
            arguments=record["arguments"],
        ),
    )


def _sections_from_answer(answer: str, response_mode: str) -> list[dict[str, str]]:
    if response_mode != "analysis" or not answer:
        return []
    sections: list[dict[str, str]] = []
    labels = (
        ("conclusion", ("结论", "conclusion", "直接结论")),
        ("reasons", ("原因", "reasons", "理由")),
        ("comparison", ("对比", "comparison", "比较")),
        ("limitations", ("局限", "uncertainty", "限制", "caveat")),
    )
    current = "conclusion"
    buckets: dict[str, list[str]] = {key: [] for key, _ in labels}
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.casefold()
        matched = None
        for key, markers in labels:
            if any(marker in lowered for marker in markers) and len(line) < 24:
                matched = key
                break
        if matched:
            current = matched
            continue
        buckets[current].append(line)
    for key, _ in labels:
        if buckets[key]:
            sections.append({"id": key, "title": key, "body": " ".join(buckets[key])})
    if not sections:
        sections.append({"id": "conclusion", "title": "conclusion", "body": answer})
    return sections


def _coach_result(state: CoachState, *, model: str) -> dict[str, Any]:
    executed = list(state.get("executed_tools") or [])
    records = list(state.get("evidence_records") or [])
    if not records and executed:
        records = build_evidence_records(executed)
    cards = [record.get("card") or {} for record in records]
    status = state.get("status") or ("complete" if state.get("answer") else "partial")
    return {
        "request_id": state["request_id"],
        "model": model,
        "answer": state.get("answer") or "",
        "tool_calls": executed,
        "usage": dict(state.get("usage") or {}),
        "answer_version": ANSWER_VERSION,
        "response_mode": state.get("response_mode") or "quick",
        "status": status,
        "sections": list(state.get("sections") or []),
        "evidence_cards": cards,
        "coverage": dict(state.get("coverage") or {}),
        "follow_up_actions": list(state.get("follow_up_actions") or []),
        "conversation_id": state.get("conversation_id"),
        "warnings": collect_warnings(executed),
    }


def _is_allowed(state: CoachState) -> bool:
    decision = state.get("scope_decision") or {}
    if not decision:
        return False
    return ScopeDecision.model_validate(decision).is_allowed()


def _finish_reason(response: Any) -> str:
    choice = response.choices[0]
    return str(getattr(choice, "finish_reason", "") or "")


def build_coach_graph(service: KimiCoachService, *, checkpointer: Any | None = None):
    """Compile the coach StateGraph for one service instance."""

    def instrument_node(
        name: str,
        node: Callable[[CoachState], dict[str, Any]],
    ) -> Callable[[CoachState], dict[str, Any]]:
        def instrumented(state: CoachState) -> dict[str, Any]:
            started_at = perf_counter()
            try:
                update = node(state)
            except Exception as exc:
                logger.warning(
                    "coach_graph_node_failed",
                    extra={
                        "request_id": state.get("request_id"),
                        "node": name,
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                        "error_type": type(exc).__name__,
                    },
                )
                raise
            logger.info(
                "coach_graph_node_completed",
                extra={
                    "request_id": state.get("request_id"),
                    "node": name,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    "status": update.get("status"),
                    "tool_call_count": len(update.get("executed_tools") or []),
                    "evidence_count": len(update.get("evidence_records") or []),
                },
            )
            return update

        return instrumented

    def prepare_turn(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        effective_response_mode = (
            request.response_mode
            if request.response_mode != "analysis" or service.settings.coach_enable_analysis
            else "quick"
        )
        normalized = state.get("normalized_message") or normalize_gate_message(
            request.message
        )
        if not normalized:
            raise CoachUserInputError("empty_input", "Please enter a question.")
        if len(normalized) > MAX_GATE_MESSAGE_LENGTH:
            raise CoachUserInputError(
                "input_too_long",
                "The question is too long. Please keep it within 4,000 characters.",
            )
        current_board = board_fingerprint(
            request.league_id,
            request.draft_state.model_dump(mode="json") if request.draft_state else None,
        )
        reference = scoped_gate_reference(
            state.get("conversation_ref") or {},
            current_league_id=request.league_id,
            current_board=current_board,
        )
        follow_up = resolve_follow_up(normalized, reference)
        merged_ref = {
            **reference,
            "entities": follow_up.get("entities") or reference.get("entities") or {},
            "needs_clarification": follow_up.get("needs_clarification"),
            "board_fingerprint": current_board,
            "current_league_id": request.league_id,
        }
        return {
            "normalized_message": normalized,
            "response_mode": effective_response_mode,
            "conversation_ref": merged_ref,
            "messages": [],
            "executed_tools": [],
            "pending_tool_calls": [],
            "evidence_records": [],
            "coverage": {},
            "tool_rounds": 0,
            "total_tool_calls": 0,
            "repair_count": 0,
            "answer": "",
            "sections": [],
            "follow_up_actions": [],
            "status": "",
            "error": None,
            "truncated": False,
        }

    def scope_gate(state: CoachState) -> dict[str, Any]:
        usage = dict(state.get("usage") or {})
        try:
            decision, gate_usage = service._classify_scope(
                state["normalized_message"],
                request_id=state["request_id"],
                reference=state.get("conversation_ref") or None,
            )
        except CoachClassificationError:
            raise
        service._add_usage(usage, gate_usage)
        update: dict[str, Any] = {
            "scope_decision": decision.model_dump(mode="json"),
            "usage": usage,
            "allowed_tools": [],
            "allow_draft_context": False,
        }
        if not decision.is_allowed():
            logger.info(
                "coach_request_scope_denied",
                extra={
                    "request_id": state["request_id"],
                    "intent": decision.intent,
                    "intents": decision.resolved_intents(),
                    "reason_code": decision.reason_code,
                },
            )
            update["answer"] = denial_answer(state["normalized_message"])
            update["status"] = "refused"
            return update
        return update

    def clarify(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        decision = ScopeDecision.model_validate(state["scope_decision"])
        kind = missing_context_kind(
            decision.resolved_intents(),
            has_draft_state=request.draft_state is not None,
            conversation_ref=state.get("conversation_ref"),
        ) or "missing_board"
        chinese = contains_chinese(state["normalized_message"])
        return {
            "answer": clarification_answer(kind, chinese=chinese),
            "status": "needs_clarification",
            "allowed_tools": [],
        }

    def plan_evidence_node(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        decision = ScopeDecision.model_validate(state["scope_decision"])
        has_draft_state = request.draft_state is not None
        intents = decision.resolved_intents()
        plan = plan_evidence(
            intents=intents,
            query_scope=decision.query_scope,
            message=state["normalized_message"],
            has_draft_state=has_draft_state,
        )
        return {
            "allowed_tools": plan["allowed_tools"],
            "allow_draft_context": (
                decision.query_scope == "current_draft" and has_draft_state
            ),
            "dropped_unrelated": decision.dropped_unrelated,
            "missing_live_board": missing_live_board(
                intents, has_draft_state=has_draft_state
            ),
            "evidence_plan": plan,
        }

    def prepare_context(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        decision = ScopeDecision.model_validate(state["scope_decision"])
        usage = dict(state.get("usage") or {})
        messages = service._build_initial_messages(
            request,
            request_id=state["request_id"],
            decision=decision,
            normalized_message=state["normalized_message"],
            usage=usage,
            conversation_reference=state.get("conversation_ref") or None,
        )
        plan = state.get("evidence_plan") or {}
        if plan.get("uncovered_asks"):
            messages.append(
                {
                    "role": "user",
                    "content": json_safe(
                        {
                            "planning_note": (
                                "This request includes more parts than one analysis "
                                "can cover. Answer the supported parts and state "
                                "which remaining asks were not covered."
                            ),
                            "uncovered_asks": plan["uncovered_asks"],
                        }
                    ),
                }
            )
        return {
            "messages": messages,
            "usage": usage,
            "executed_tools": [],
            "pending_tool_calls": [],
            "tool_rounds": 0,
            "total_tool_calls": 0,
        }

    def call_model(state: CoachState) -> dict[str, Any]:
        usage = dict(state.get("usage") or {})
        round_index = int(state.get("tool_rounds") or 0)
        response = service._completion(
            list(state.get("messages") or []),
            state["request_id"],
            round_index,
            allowed_tools=frozenset(state.get("allowed_tools") or []),
            response_mode=state.get("response_mode") or "quick",
        )
        service._add_usage(usage, getattr(response, "usage", None))
        message = response.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        messages = list(state.get("messages") or [])
        messages.append(message.model_dump(exclude_none=True))
        truncated = _finish_reason(response) == "length"
        if not tool_calls:
            answer = str(getattr(message, "content", "") or "").strip()
            if not answer:
                raise RuntimeError("Kimi returned no answer")
            return {
                "messages": messages,
                "usage": usage,
                "answer": answer,
                "pending_tool_calls": [],
                "truncated": truncated,
            }
        pending = [
            {
                "id": str(tool_call.id),
                "name": str(tool_call.function.name),
                "arguments": tool_call.function.arguments or "{}",
            }
            for tool_call in tool_calls
        ]
        return {
            "messages": messages,
            "usage": usage,
            "pending_tool_calls": pending,
            "answer": "",
            "truncated": False,
        }

    def enforce_limits(state: CoachState) -> dict[str, Any]:
        pending = list(state.get("pending_tool_calls") or [])
        budget = current_request_budget()
        if budget is not None and (budget.cancelled() or not budget.allow_provider_call()):
            raise CoachLoopLimitError("Kimi ran out of request budget")
        if int(state.get("tool_rounds") or 0) >= service.settings.kimi_max_tool_rounds:
            raise CoachLoopLimitError("Kimi exceeded the tool-round limit")
        executed = list(state.get("executed_tools") or [])
        if len(executed) + len(pending) > service.settings.kimi_max_tool_calls:
            raise CoachLoopLimitError("Kimi exceeded the total tool-call limit")
        return {}

    def execute_tools(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        allowed_tools = frozenset(state.get("allowed_tools") or [])
        executed = list(state.get("executed_tools") or [])
        messages = list(state.get("messages") or [])
        allow_draft_context = bool(state.get("allow_draft_context"))
        budget = current_request_budget()
        for record in state.get("pending_tool_calls") or []:
            if budget is not None and (
                budget.cancelled() or not budget.allow_provider_call()
            ):
                break
            tool_record, tool_message = service._execute_tool_call(
                _as_tool_call(record),
                request=request,
                request_id=state["request_id"],
                allowed_tools=allowed_tools,
                allow_draft_context=allow_draft_context,
            )
            executed.append(tool_record)
            messages.append(tool_message)
            if budget is not None:
                budget.record_tool_call()
        if budget is not None:
            budget.record_tool_round()
        return {
            "messages": messages,
            "executed_tools": executed,
            "pending_tool_calls": [],
            "total_tool_calls": len(executed),
            "tool_rounds": int(state.get("tool_rounds") or 0) + 1,
        }

    def register_evidence(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        records = build_evidence_records(
            state.get("executed_tools") or [],
            context={
                "league_id": request.league_id,
                "board_fingerprint": (state.get("conversation_ref") or {}).get(
                    "board_fingerprint"
                ),
            },
        )
        return {
            "evidence_records": records,
            "coverage": coverage_from_plan(state.get("evidence_plan") or {}, records),
        }

    def finalize_budget_partial(state: CoachState) -> dict[str, Any]:
        records = list(state.get("evidence_records") or [])
        usable = any(
            record.get("success") and record.get("status") in {"ok", "sparse"}
            for record in records
        )
        if not usable:
            raise CoachLoopLimitError(
                "Kimi ran out of request budget before usable evidence was available"
            )
        answer = limitation_answer(
            chinese=contains_chinese(state["normalized_message"]),
            issues=["budget_exhausted"],
            evidence_records=records,
            uncovered_asks=list(
                (state.get("evidence_plan") or {}).get("uncovered_asks") or []
            ),
        )
        return {
            "answer": answer,
            "status": "partial",
            "sections": _sections_from_answer(
                answer,
                state.get("response_mode") or "quick",
            ),
        }

    def sanitize_answer(state: CoachState) -> dict[str, Any]:
        answer = state.get("answer") or ""
        usage = dict(state.get("usage") or {})
        if service._contains_planning_leak(answer):
            answer, rewrite_usage = service._rewrite_answer(
                answer,
                request_id=state["request_id"],
                response_mode=state.get("response_mode") or "quick",
            )
            service._add_usage(usage, rewrite_usage)
        return {"answer": answer, "usage": usage}

    def validate_answer_node(state: CoachState) -> dict[str, Any]:
        decision = ScopeDecision.model_validate(state.get("scope_decision") or {})
        validation = validate_answer(
            state.get("answer") or "",
            intents=decision.resolved_intents() if decision else [],
            evidence_records=list(state.get("evidence_records") or []),
            truncated=bool(state.get("truncated")),
        )
        request = CoachInput.model_validate(state["request"])
        chinese = contains_chinese(state["normalized_message"])
        actions = follow_up_actions(
            intents=decision.resolved_intents() if decision else [],
            evidence_records=list(state.get("evidence_records") or []),
            conversation_ref=state.get("conversation_ref"),
            chinese=chinese,
        )
        coverage = coverage_from_plan(
            state.get("evidence_plan") or {},
            list(state.get("evidence_records") or []),
            validation=validation,
        )
        update: dict[str, Any] = {
            "coverage": coverage,
            "follow_up_actions": actions,
            "sections": _sections_from_answer(
                state.get("answer") or "",
                state.get("response_mode") or "quick",
            ),
        }
        if validation["valid"]:
            update["status"] = (
                "partial" if coverage.get("missing") else validation["status"]
            )
            return update
        budget = current_request_budget()
        can_repair = (
            validation["repairable"]
            and int(state.get("repair_count") or 0) < 1
            and any(
                record.get("success") and record.get("status") in {"ok", "sparse"}
                for record in state.get("evidence_records") or []
            )
            and not any(
                issue in {
                    "impossible_percent",
                    "unsupported_percent",
                    "percent_subject_mismatch",
                    "percent_metric_mismatch",
                    "advantage_described_as_win_probability",
                }
                for issue in validation["issues"]
            )
            and (budget is None or budget.can_repair())
        )
        if can_repair:
            update["status"] = "repairable"
            return update
        update["answer"] = limitation_answer(
            chinese=chinese,
            issues=validation["issues"],
            evidence_records=list(state.get("evidence_records") or []),
            uncovered_asks=list((state.get("evidence_plan") or {}).get("uncovered_asks") or []),
        )
        update["status"] = "partial"
        update["sections"] = _sections_from_answer(
            update["answer"],
            state.get("response_mode") or "quick",
        )
        return update

    def bounded_repair(state: CoachState) -> dict[str, Any]:
        budget = current_request_budget()
        if budget is not None:
            budget.record_repair()
        decision = ScopeDecision.model_validate(state.get("scope_decision") or {})
        issues = coverage_from_plan(
            state.get("evidence_plan") or {},
            list(state.get("evidence_records") or []),
        )
        messages = list(state.get("messages") or [])
        messages.append(
            {
                "role": "user",
                "content": json_safe(
                    {
                        "repair": True,
                        "instruction": (
                            "The previous answer is not grounded. Rewrite using only "
                            "verified tool evidence. If a number or comparison is not "
                            "supported, say so. Do not invent statistics. Do not "
                            "describe relative advantage as win probability."
                        ),
                        "coverage": issues,
                        "intents": decision.resolved_intents(),
                    }
                ),
            }
        )
        return {
            "messages": messages,
            "repair_count": int(state.get("repair_count") or 0) + 1,
            "answer": "",
            "pending_tool_calls": [],
            "allowed_tools": [],
        }

    def finalize(state: CoachState) -> dict[str, Any]:
        status = state.get("status")
        if not status:
            status = "refused" if not _is_allowed(state) else "complete"
        return {
            "status": status,
            "result": _coach_result(
                {**state, "status": status},
                model=service.settings.kimi_model,
            ),
        }

    def record_completed_turn(state: CoachState) -> dict[str, Any]:
        return {}

    def after_scope(state: CoachState) -> Literal["clarify", "plan_evidence", "finalize"]:
        if not _is_allowed(state):
            return "finalize"
        request = CoachInput.model_validate(state["request"])
        decision = ScopeDecision.model_validate(state["scope_decision"])
        kind = missing_context_kind(
            decision.resolved_intents(),
            has_draft_state=request.draft_state is not None,
            conversation_ref=state.get("conversation_ref"),
        )
        if kind == "other_team":
            return "clarify"
        if kind == "missing_board" and not any(
            intent
            not in {
                "draft_prediction",
                "draft_simulation",
                "lineup_recommendation",
                "lineup_score",
            }
            for intent in decision.resolved_intents()
        ):
            return "clarify"
        return "plan_evidence"

    def after_model(state: CoachState) -> Literal["enforce_limits", "sanitize_answer"]:
        if state.get("pending_tool_calls"):
            return "enforce_limits"
        return "sanitize_answer"

    def after_register(state: CoachState) -> Literal["call_model", "finalize_budget_partial"]:
        budget = current_request_budget()
        if budget is not None and not budget.allow_provider_call():
            return "finalize_budget_partial"
        return "call_model"

    def after_validate(
        state: CoachState,
    ) -> Literal["bounded_repair", "finalize"]:
        if state.get("status") == "repairable":
            return "bounded_repair"
        return "finalize"

    builder = StateGraph(CoachState)
    builder.add_node("prepare_turn", instrument_node("prepare_turn", prepare_turn))
    builder.add_node("scope_gate", instrument_node("scope_gate", scope_gate))
    builder.add_node("clarify", instrument_node("clarify", clarify))
    builder.add_node("plan_evidence", instrument_node("plan_evidence", plan_evidence_node))
    builder.add_node("prepare_context", instrument_node("prepare_context", prepare_context))
    builder.add_node("call_model", instrument_node("call_model", call_model))
    builder.add_node("enforce_limits", instrument_node("enforce_limits", enforce_limits))
    builder.add_node("execute_tools", instrument_node("execute_tools", execute_tools))
    builder.add_node("register_evidence", instrument_node("register_evidence", register_evidence))
    builder.add_node(
        "finalize_budget_partial",
        instrument_node("finalize_budget_partial", finalize_budget_partial),
    )
    builder.add_node("sanitize_answer", instrument_node("sanitize_answer", sanitize_answer))
    builder.add_node("validate_answer", instrument_node("validate_answer", validate_answer_node))
    builder.add_node("bounded_repair", instrument_node("bounded_repair", bounded_repair))
    builder.add_node("finalize", instrument_node("finalize", finalize))
    builder.add_node(
        "record_completed_turn",
        instrument_node("record_completed_turn", record_completed_turn),
    )
    builder.add_edge(START, "prepare_turn")
    builder.add_edge("prepare_turn", "scope_gate")
    builder.add_conditional_edges(
        "scope_gate",
        after_scope,
        {
            "plan_evidence": "plan_evidence",
            "clarify": "clarify",
            "finalize": "finalize",
        },
    )
    builder.add_edge("clarify", "finalize")
    builder.add_edge("plan_evidence", "prepare_context")
    builder.add_edge("prepare_context", "call_model")
    builder.add_conditional_edges(
        "call_model",
        after_model,
        {
            "enforce_limits": "enforce_limits",
            "sanitize_answer": "sanitize_answer",
        },
    )
    builder.add_edge("enforce_limits", "execute_tools")
    builder.add_edge("execute_tools", "register_evidence")
    builder.add_conditional_edges(
        "register_evidence",
        after_register,
        {
            "call_model": "call_model",
            "finalize_budget_partial": "finalize_budget_partial",
        },
    )
    builder.add_edge("finalize_budget_partial", "finalize")
    builder.add_edge("sanitize_answer", "validate_answer")
    builder.add_conditional_edges(
        "validate_answer",
        after_validate,
        {
            "bounded_repair": "bounded_repair",
            "finalize": "finalize",
        },
    )
    builder.add_edge("bounded_repair", "call_model")
    builder.add_edge("finalize", "record_completed_turn")
    builder.add_edge("record_completed_turn", END)
    compile_kwargs: dict[str, Any] = {"name": "kpl_draft_coach"}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    return builder.compile(**compile_kwargs)


def json_safe(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def graph_recursion_limit(max_tool_rounds: int) -> int:
    """Bound graph super-steps to the configured tool-round budget plus repair."""
    return (max_tool_rounds + 2) * 6 + 20


def run_coach_graph(
    service: KimiCoachService,
    request: CoachInput,
    *,
    request_id: str,
    normalized_message: str,
    usage: dict[str, int],
    started: float,
    conversation_ref: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    checkpointer: Any | None = None,
    budget: Any | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke the compiled coach graph and return the existing service payload."""
    graph = service.compiled_coach_graph()
    active_budget = budget or create_budget(
            request_id,
            deadline_seconds=service.settings.coach_request_deadline_seconds,
            reserve_seconds=service.settings.coach_finalize_reserve_seconds,
            clock=getattr(service, "clock", None),
        )
    token = set_request_budget(active_budget)
    invoke_config = {
        "recursion_limit": graph_recursion_limit(service.settings.kimi_max_tool_rounds),
        **(config or {}),
    }
    if conversation_id is not None:
        configurable = dict(invoke_config.get("configurable") or {})
        configurable["thread_id"] = conversation_id
        invoke_config["configurable"] = configurable
    try:
        final_state: CoachState = graph.invoke(
            initial_coach_state(
                request,
                request_id=request_id,
                normalized_message=normalized_message,
                usage=usage,
                conversation_ref=conversation_ref,
                conversation_id=conversation_id,
            ),
            config=invoke_config,
        )
    except GraphRecursionError as exc:
        raise CoachLoopLimitError(
            "Kimi did not finish within the tool-round limit"
        ) from exc
    except CoachClassificationError:
        raise
    except CoachUserInputError:
        raise
    finally:
        reset_request_budget(token)

    result = final_state.get("result")
    if result is None:
        raise RuntimeError("Kimi returned no answer")
    if _is_allowed(final_state):
        logger.info(
            "coach_request_completed",
            extra={
                "request_id": request_id,
                "model": service.settings.kimi_model,
                "tool_call_count": len(result.get("tool_calls") or []),
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "answer_version": result.get("answer_version"),
                "response_mode": result.get("response_mode"),
                "completion_status": result.get("status"),
                "coverage_answered_count": len(
                    (result.get("coverage") or {}).get("answered") or []
                ),
                "coverage_missing_count": len(
                    (result.get("coverage") or {}).get("missing") or []
                ),
                **active_budget.snapshot(),
                **result["usage"],
            },
        )
    return result
