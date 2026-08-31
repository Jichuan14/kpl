"""LangGraph orchestration for the KPL Draft Coach.

This module replaces only the request-scoped tool loop. The Kimi client, tool
registry, scope policy, prompts, and HTTP contract stay outside the graph.
Runtime objects (client, settings, tool functions) are closed over by nodes and
are never stored in graph state.
"""

from __future__ import annotations

import logging
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.agent.scope import ScopeDecision, denial_answer, missing_live_board
from app.agent.service import (
    CoachInput,
    CoachLoopLimitError,
    KimiCoachService,
)

logger = logging.getLogger(__name__)

GRAPH_NODE_NAMES = (
    "scope_gate",
    "plan_tools",
    "prepare_context",
    "call_model",
    "enforce_limits",
    "execute_tools",
    "sanitize_answer",
    "finalize",
)


class CoachState(TypedDict, total=False):
    """Serializable, request-scoped coach state.

    Limits, allowed tools, league_id, and draft visibility are set by Python
    nodes. The model cannot write these fields.
    """

    request: dict[str, Any]
    request_id: str
    normalized_message: str
    scope_decision: dict[str, Any]
    allowed_tools: list[str]
    messages: list[dict[str, Any]]
    executed_tools: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_rounds: int
    total_tool_calls: int
    usage: dict[str, int]
    answer: str
    error: str | None
    allow_draft_context: bool
    dropped_unrelated: bool
    missing_live_board: bool
    result: dict[str, Any]


def initial_coach_state(
    request: CoachInput,
    *,
    request_id: str,
    normalized_message: str,
    usage: dict[str, int],
) -> CoachState:
    """Build the request-scoped graph input without runtime dependencies."""
    return {
        "request": request.model_dump(mode="json"),
        "request_id": request_id,
        "normalized_message": normalized_message,
        "scope_decision": {},
        "allowed_tools": [],
        "messages": [],
        "executed_tools": [],
        "pending_tool_calls": [],
        "tool_rounds": 0,
        "total_tool_calls": 0,
        "usage": dict(usage),
        "answer": "",
        "error": None,
        "allow_draft_context": False,
        "dropped_unrelated": False,
        "missing_live_board": False,
    }


def _as_tool_call(record: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=record["id"],
        function=SimpleNamespace(
            name=record["name"],
            arguments=record["arguments"],
        ),
    )


def _coach_result(
    state: CoachState,
    *,
    model: str,
) -> dict[str, Any]:
    return {
        "request_id": state["request_id"],
        "model": model,
        "answer": state.get("answer") or "",
        "tool_calls": list(state.get("executed_tools") or []),
        "usage": dict(state.get("usage") or {}),
    }


def _is_allowed(state: CoachState) -> bool:
    decision = state.get("scope_decision") or {}
    if not decision:
        return False
    return ScopeDecision.model_validate(decision).is_allowed()


def build_coach_graph(service: KimiCoachService):
    """Compile the coach StateGraph for one service instance.

    Persistence is intentionally omitted: each HTTP request remains isolated,
    matching the current client-supplied history contract.
    """

    def scope_gate(state: CoachState) -> dict[str, Any]:
        usage = dict(state.get("usage") or {})
        decision, gate_usage = service._classify_scope(
            state["normalized_message"],
            request_id=state["request_id"],
        )
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
            return update
        return update

    def plan_tools(state: CoachState) -> dict[str, Any]:
        request = CoachInput.model_validate(state["request"])
        decision = ScopeDecision.model_validate(state["scope_decision"])
        has_draft_state = request.draft_state is not None
        intents = decision.resolved_intents()
        return {
            "allowed_tools": sorted(
                decision.allowed_tools(has_draft_state=has_draft_state)
            ),
            "allow_draft_context": (
                decision.query_scope == "current_draft" and has_draft_state
            ),
            "dropped_unrelated": decision.dropped_unrelated,
            "missing_live_board": missing_live_board(
                intents, has_draft_state=has_draft_state
            ),
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
        )
        service._add_usage(usage, getattr(response, "usage", None))
        message = response.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        messages = list(state.get("messages") or [])
        messages.append(message.model_dump(exclude_none=True))
        if not tool_calls:
            answer = str(getattr(message, "content", "") or "").strip()
            if not answer:
                raise RuntimeError("Kimi returned no answer")
            return {
                "messages": messages,
                "usage": usage,
                "answer": answer,
                "pending_tool_calls": [],
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
        }

    def enforce_limits(state: CoachState) -> dict[str, Any]:
        pending = list(state.get("pending_tool_calls") or [])
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
        for record in state.get("pending_tool_calls") or []:
            tool_record, tool_message = service._execute_tool_call(
                _as_tool_call(record),
                request=request,
                request_id=state["request_id"],
                allowed_tools=allowed_tools,
                allow_draft_context=allow_draft_context,
            )
            executed.append(tool_record)
            messages.append(tool_message)
        return {
            "messages": messages,
            "executed_tools": executed,
            "pending_tool_calls": [],
            "total_tool_calls": len(executed),
            "tool_rounds": int(state.get("tool_rounds") or 0) + 1,
        }

    def sanitize_answer(state: CoachState) -> dict[str, Any]:
        answer = state.get("answer") or ""
        usage = dict(state.get("usage") or {})
        if service._contains_planning_leak(answer):
            answer, rewrite_usage = service._rewrite_answer(
                answer,
                request_id=state["request_id"],
            )
            service._add_usage(usage, rewrite_usage)
        return {"answer": answer, "usage": usage}

    def finalize(state: CoachState) -> dict[str, Any]:
        return {
            "result": _coach_result(state, model=service.settings.kimi_model),
        }

    def after_scope(state: CoachState) -> Literal["plan_tools", "finalize"]:
        if _is_allowed(state):
            return "plan_tools"
        return "finalize"

    def after_model(state: CoachState) -> Literal["enforce_limits", "sanitize_answer"]:
        if state.get("pending_tool_calls"):
            return "enforce_limits"
        return "sanitize_answer"

    builder = StateGraph(CoachState)
    builder.add_node("scope_gate", scope_gate)
    builder.add_node("plan_tools", plan_tools)
    builder.add_node("prepare_context", prepare_context)
    builder.add_node("call_model", call_model)
    builder.add_node("enforce_limits", enforce_limits)
    builder.add_node("execute_tools", execute_tools)
    builder.add_node("sanitize_answer", sanitize_answer)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "scope_gate")
    builder.add_conditional_edges(
        "scope_gate",
        after_scope,
        {"plan_tools": "plan_tools", "finalize": "finalize"},
    )
    builder.add_edge("plan_tools", "prepare_context")
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
    builder.add_edge("execute_tools", "call_model")
    builder.add_edge("sanitize_answer", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(name="kpl_draft_coach")


def graph_recursion_limit(max_tool_rounds: int) -> int:
    """Bound graph super-steps to the configured tool-round budget."""
    return (max_tool_rounds + 1) * 4 + 16


def run_coach_graph(
    service: KimiCoachService,
    request: CoachInput,
    *,
    request_id: str,
    normalized_message: str,
    usage: dict[str, int],
    started: float,
) -> dict[str, Any]:
    """Invoke the compiled coach graph and return the existing service payload."""
    graph = service.compiled_coach_graph()
    try:
        final_state: CoachState = graph.invoke(
            initial_coach_state(
                request,
                request_id=request_id,
                normalized_message=normalized_message,
                usage=usage,
            ),
            config={"recursion_limit": graph_recursion_limit(
                service.settings.kimi_max_tool_rounds
            )},
        )
    except GraphRecursionError as exc:
        raise CoachLoopLimitError(
            "Kimi did not finish within the tool-round limit"
        ) from exc

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
                **result["usage"],
            },
        )
    return result
