"""Offline quality contract for the LangGraph coach reliability phase.

This module validates evaluation case quality and scores saved or deterministic
fixture results. It never creates a provider client or performs a paid call.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.answer_validation import validate_answer
from app.agent.evidence import build_evidence_records
from app.agent.tool_registry import TOOLS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = PROJECT_ROOT / "agent" / "evals" / "phase_3_cases.jsonl"
QUALITY_DIMENSIONS = frozenset(
    {"policy_routing", "numeric_grounding", "context", "coverage", "usefulness"}
)


class Phase3Turn(BaseModel):
    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1, max_length=4000)
    response_mode: Literal["quick", "analysis"] = "quick"


class Phase3EvalCase(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    language: Literal["en", "zh"]
    category: Literal[
        "routing",
        "grounding",
        "context",
        "coverage",
        "degraded",
        "contract",
    ]
    turns: list[Phase3Turn] = Field(min_length=1, max_length=4)
    fixture_context: dict[str, Any] = Field(default_factory=dict)
    expected_intents: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)
    expected_status: list[
        Literal["complete", "partial", "needs_clarification", "refused", "error"]
    ] = Field(min_length=1)
    budget_class: Literal["quick", "analysis", "degraded"]
    dimensions: list[
        Literal[
            "policy_routing",
            "numeric_grounding",
            "context",
            "coverage",
            "usefulness",
        ]
    ] = Field(min_length=1)


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[Phase3EvalCase]:
    cases: list[Phase3EvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(Phase3EvalCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"Invalid Phase 3 evaluation case on line {line_number}") from exc
    return cases


def validate_catalog(cases: list[Phase3EvalCase]) -> dict[str, Any]:
    errors: list[str] = []
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        errors.append(f"duplicate case IDs: {', '.join(duplicates)}")
    referenced_tools = {
        tool for case in cases for tool in [*case.required_evidence, *case.allowed_tools]
    }
    unknown = sorted(referenced_tools - set(TOOLS))
    if unknown:
        errors.append(f"unknown tools: {', '.join(unknown)}")
    dimensions = {dimension for case in cases for dimension in case.dimensions}
    missing_dimensions = sorted(QUALITY_DIMENSIONS - dimensions)
    if missing_dimensions:
        errors.append(f"missing quality dimensions: {', '.join(missing_dimensions)}")
    if {case.language for case in cases} != {"en", "zh"}:
        errors.append("catalog must contain English and Chinese cases")
    if not any(len(case.turns) > 1 for case in cases):
        errors.append("catalog must contain a multi-turn case")
    if not any(case.budget_class == "degraded" for case in cases):
        errors.append("catalog must contain a degraded-budget case")
    for case in cases:
        if not set(case.required_evidence).issubset(set(case.allowed_tools)):
            errors.append(f"{case.id}: required evidence must also be allowed")
        if case.category == "grounding" and not case.prohibited_claims:
            errors.append(f"{case.id}: grounding cases need prohibited claims")
        if "context" in case.dimensions and not case.fixture_context:
            errors.append(f"{case.id}: context dimension needs fixture context")
    return {
        "passed": not errors,
        "case_count": len(cases),
        "dimensions": sorted(dimensions),
        "errors": errors,
    }


def assess_result(
    case: Phase3EvalCase,
    result: dict[str, Any],
    *,
    observed_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score independent dimensions without an LLM judge."""
    answer = str(result.get("answer") or "").strip()
    calls = list(result.get("tool_calls") or [])
    successful_tools = {
        str(call.get("name") or "") for call in calls if call.get("success")
    }
    evidence_records = build_evidence_records(calls)
    grounding = validate_answer(
        answer,
        intents=case.expected_intents,
        evidence_records=evidence_records,
        truncated=bool(result.get("truncated")),
    )
    policy_ok = (
        str(result.get("status") or "error") in case.expected_status
        and successful_tools <= set(case.allowed_tools)
    )
    coverage_ok = set(case.required_evidence) <= successful_tools
    observed = observed_context or {}
    expected_context = case.fixture_context.get("expected_resolved") or {}
    context_ok = all(observed.get(key) == value for key, value in expected_context.items())
    prohibited = [
        claim for claim in case.prohibited_claims if claim.casefold() in answer.casefold()
    ]
    usefulness_ok = bool(answer) and not prohibited
    scores = {
        "policy_routing": policy_ok,
        "numeric_grounding": bool(grounding["valid"]),
        "context": context_ok,
        "coverage": coverage_ok,
        "usefulness": usefulness_ok,
    }
    selected = {dimension: scores[dimension] for dimension in case.dimensions}
    return {
        "passed": all(selected.values()),
        "scores": selected,
        "grounding_issues": grounding["issues"],
        "prohibited_claims_found": prohibited,
        "actual_tools": sorted(successful_tools),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args()
    report = validate_catalog(load_cases(args.cases))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"]:
        print("Offline Phase 3 catalog only; live release gate remains pending authorization.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
