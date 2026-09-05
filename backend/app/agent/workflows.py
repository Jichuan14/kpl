"""Deterministic evidence-planning templates for the LangGraph coach."""

from __future__ import annotations

import re
from typing import Any

from app.agent.scope import INTENT_TOOL_ALLOWLIST, plan_allowed_tools

MAX_PLANNED_ASKS = 3

WORKFLOW_TEMPLATES = {
    "ordinary_lookup": {
        "label": "ordinary lookup",
        "required_from_intents": True,
    },
    "explain_recommendation": {
        "label": "explain current recommendation",
        "preferred_intents": ("lineup_recommendation", "draft_prediction"),
    },
    "compare_alternatives": {
        "label": "compare available alternatives",
        "preferred_intents": ("lineup_recommendation", "draft_prediction"),
    },
    "team_tendency": {
        "label": "team tendency analysis",
        "preferred_intents": (
            "team_draft_tendencies",
            "team_opening_sequences",
            "team_combo_performance",
            "recent_team_trends",
        ),
    },
    "patch_trend": {
        "label": "patch or trend comparison",
        "preferred_intents": ("patch_notes", "game_reference", "recent_team_trends", "meta_heroes"),
    },
}

ASK_SPLIT = re.compile(r"(?:以及|,|，|and then|and also|并且|还有|再问)", re.IGNORECASE)
COMPARE_HINT = re.compile(r"compare|vs\.?|versus|还是|对比|哪个更|difference|区别", re.IGNORECASE)
EXPLAIN_HINT = re.compile(r"why (?:that|this) one|为什么|为啥|解释", re.IGNORECASE)
PATCH_HINT = re.compile(r"patch|版本|调整|改动", re.IGNORECASE)


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for item in values:
        if item and item not in seen:
            seen.append(item)
    return seen


def detect_uncovered_asks(message: str) -> list[str]:
    """Return extra asks beyond the supported planning bound."""
    parts = [part.strip() for part in ASK_SPLIT.split(message) if part.strip()]
    if len(parts) <= MAX_PLANNED_ASKS:
        return []
    return parts[MAX_PLANNED_ASKS:]


def select_workflow(intents: list[str], message: str) -> str:
    if COMPARE_HINT.search(message) and any(
        intent in {"lineup_recommendation", "draft_prediction", "hero_relationships"}
        for intent in intents
    ):
        return "compare_alternatives"
    if EXPLAIN_HINT.search(message) and any(
        intent in {"lineup_recommendation", "draft_prediction"} for intent in intents
    ):
        return "explain_recommendation"
    if any(intent in WORKFLOW_TEMPLATES["team_tendency"]["preferred_intents"] for intent in intents):
        return "team_tendency"
    if any(intent in WORKFLOW_TEMPLATES["patch_trend"]["preferred_intents"] for intent in intents) or PATCH_HINT.search(message):
        if "patch_notes" in intents or "game_reference" in intents:
            return "patch_trend"
    return "ordinary_lookup"


def plan_evidence(
    *,
    intents: list[str],
    query_scope: str,
    message: str,
    has_draft_state: bool,
) -> dict[str, Any]:
    """Build a validated evidence plan from intents and a workflow template."""
    workflow = select_workflow(intents, message)
    allowed = plan_allowed_tools(intents, query_scope, has_draft_state=has_draft_state)
    required_groups: list[dict[str, Any]] = []
    for intent in intents:
        tools = sorted(INTENT_TOOL_ALLOWLIST.get(intent, frozenset()) & allowed)
        if not tools and intent == "coach_capabilities":
            continue
        required_groups.append(
            {
                "label": intent,
                "intent": intent,
                "tools": tools,
                "optional": False,
            }
        )
    uncovered = detect_uncovered_asks(message)
    subquestions = _unique([*intents, *([f"uncovered:{ask}" for ask in uncovered])])
    return {
        "workflow": workflow,
        "subquestions": subquestions[:MAX_PLANNED_ASKS],
        "required_groups": required_groups,
        "optional_enrichment": [],
        "uncovered_asks": uncovered,
        "allowed_tools": sorted(allowed),
        "exclusions": [],
    }


def follow_up_actions(
    *,
    intents: list[str],
    evidence_records: list[dict[str, Any]],
    conversation_ref: dict[str, Any] | None,
    chinese: bool,
) -> list[dict[str, str]]:
    """Bounded contextual suggestions that require a user click."""
    actions: list[dict[str, str]] = []
    entities = (conversation_ref or {}).get("entities") or {}
    team = entities.get("team_name") or entities.get("blue_team_name")
    hero = entities.get("hero_name")
    if any(record.get("family") == "draft_recommendation" for record in evidence_records):
        actions.append(
            {
                "id": "explain_difference",
                "label": "解释差异" if chinese else "Explain the difference",
            }
        )
        actions.append(
            {
                "id": "compare_alternatives",
                "label": "比较备选" if chinese else "Compare alternatives",
            }
        )
    if any(record.get("sample_size") for record in evidence_records):
        actions.append(
            {
                "id": "show_sample",
                "label": "查看样本" if chinese else "Show the sample",
            }
        )
    if team and "team_draft_tendencies" not in intents:
        actions.append(
            {
                "id": "team_tendency",
                "label": f"{team} 的倾向" if chinese else f"{team} tendencies",
            }
        )
    if hero and "hero_bp_stats" not in intents:
        actions.append(
            {
                "id": "hero_sample",
                "label": f"{hero} 的样本" if chinese else f"{hero} sample",
            }
        )
    return actions[:3]
