"""Evaluate deterministic patch retrieval and optional Coach tool routing.

Run without ``--live`` for the free, repeatable retrieval checks.  The live
mode additionally asks Kimi to route a small patch-research question catalog;
it is intentionally opt-in because it consumes provider tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.agent.eval_phase1 import Phase1EvalCase, load_cases, run_live
from app.agent.tool_registry import TOOLS
from app.agent.tools.patches import PatchSearchResponse, SearchPatchNotesArguments
from app.knowledge.patch_retrieval import PatchRetriever


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RETRIEVAL_CASES_PATH = (
    PROJECT_ROOT / "agent" / "evals" / "patch_research_retrieval_cases.jsonl"
)
DEFAULT_AGENT_CASES_PATH = (
    PROJECT_ROOT / "agent" / "evals" / "patch_research_agent_cases.jsonl"
)
DEFAULT_LIVE_REPORT_PATH = (
    PROJECT_ROOT / "agent" / "evals" / "patch_research_live_report.json"
)
PATCH_TOOL = "search_patch_notes"


class RetrievalEvalCase(BaseModel):
    """A source-grounded expectation for one direct patch-index search."""

    model_config = {"extra": "forbid"}

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    arguments: SearchPatchNotesArguments
    min_results: int = Field(ge=0, le=5)
    max_results: int = Field(ge=0, le=5)
    expected_announcement_ids: list[str] = Field(default_factory=list)
    expected_hero_name: str | None = Field(default=None, min_length=1, max_length=100)
    forbidden_heading_terms: list[str] = Field(default_factory=list)


class PatchSearchCallable(Protocol):
    def search(self, arguments: SearchPatchNotesArguments) -> PatchSearchResponse: ...


def load_retrieval_cases(
    path: Path = DEFAULT_RETRIEVAL_CASES_PATH,
) -> list[RetrievalEvalCase]:
    cases: list[RetrievalEvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(RetrievalEvalCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"Invalid retrieval evaluation case on line {line_number}") from exc
    return cases


def validate_retrieval_catalog(cases: list[RetrievalEvalCase]) -> dict[str, Any]:
    errors: list[str] = []
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        errors.append("duplicate case IDs: " + ", ".join(duplicates))
    if not cases:
        errors.append("at least one retrieval case is required")
    for case in cases:
        if case.min_results > case.max_results:
            errors.append(f"{case.id}: min_results cannot exceed max_results")
        if case.max_results > case.arguments.limit:
            errors.append(f"{case.id}: max_results exceeds the requested limit")
        if case.expected_announcement_ids and case.max_results == 0:
            errors.append(f"{case.id}: expected IDs require at least one result")
    return {
        "passed": not errors,
        "case_count": len(cases),
        "errors": errors,
    }


def assess_retrieval_result(
    case: RetrievalEvalCase,
    result: PatchSearchResponse | None,
    *,
    repeat_result: PatchSearchResponse | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    """Check source provenance, expected evidence, and deterministic output."""
    failures: list[str] = []
    if error_type is not None or result is None:
        return {
            "passed": False,
            "failures": [f"retrieval failed: {error_type or 'unknown error'}"],
            "result_count": 0,
            "announcement_ids": [],
        }

    cards = result.results
    if not case.min_results <= len(cards) <= case.max_results:
        failures.append(
            f"result count {len(cards)} is outside {case.min_results}..{case.max_results}"
        )
    actual_ids = [card.announcement_id for card in cards]
    expected_ids = set(case.expected_announcement_ids)
    if expected_ids and not expected_ids.intersection(actual_ids):
        failures.append("none of the expected announcement IDs were returned")
    for card in cards:
        if card.source_url.scheme != "https":
            failures.append(f"{card.announcement_id}: source URL is not HTTPS")
        if not card.source_hash:
            failures.append(f"{card.announcement_id}: source hash is missing")
        if not card.heading_path:
            failures.append(f"{card.announcement_id}: heading path is missing")
        if case.expected_hero_name and case.expected_hero_name not in card.hero_names:
            failures.append(
                f"{card.announcement_id}: expected hero {case.expected_hero_name} is missing"
            )
        heading = " > ".join(card.heading_path).casefold()
        for forbidden in case.forbidden_heading_terms:
            if forbidden.casefold() in heading:
                failures.append(
                    f"{card.announcement_id}: returned forbidden heading {forbidden}"
                )
    if repeat_result is not None and result.model_dump(mode="json") != repeat_result.model_dump(mode="json"):
        failures.append("same request did not return the same evidence ordering")
    return {
        "passed": not failures,
        "failures": failures,
        "result_count": len(cards),
        "announcement_ids": actual_ids,
    }


def run_retrieval(
    cases: list[RetrievalEvalCase],
    *,
    retriever: PatchSearchCallable | None = None,
) -> dict[str, Any]:
    """Run repeatable local retrieval checks without calling an LLM."""
    active_retriever = retriever or PatchRetriever()
    results: list[dict[str, Any]] = []
    for case in cases:
        first: PatchSearchResponse | None = None
        second: PatchSearchResponse | None = None
        error_type: str | None = None
        try:
            first = active_retriever.search(case.arguments)
            second = active_retriever.search(case.arguments)
        except Exception as exc:
            error_type = type(exc).__name__
        assessment = assess_retrieval_result(
            case,
            first,
            repeat_result=second,
            error_type=error_type,
        )
        results.append({"id": case.id, **assessment})
        status = "PASS" if assessment["passed"] else "FAIL"
        ids = ",".join(assessment["announcement_ids"]) or "none"
        print(f"{status} {case.id}: results={assessment['result_count']} ids={ids}")
    passed_count = sum(1 for result in results if result["passed"])
    report = {
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "passed": passed_count == len(results),
        "results": results,
    }
    print(f"Retrieval summary: {passed_count}/{len(results)} passed")
    return report


def validate_agent_catalog(cases: list[Phase1EvalCase]) -> dict[str, Any]:
    """Validate a small, paid-only catalog for Coach patch-tool routing."""
    errors: list[str] = []
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        errors.append("duplicate case IDs: " + ", ".join(duplicates))
    referenced = {
        tool
        for case in cases
        for tool in [*case.expected_tools, *case.allowed_tools]
    }
    unknown = sorted(referenced - set(TOOLS))
    if unknown:
        errors.append("unknown tools: " + ", ".join(unknown))
    if not any(PATCH_TOOL in case.expected_tools for case in cases):
        errors.append("at least one case must require search_patch_notes")
    if not any(
        case.category == "combined"
        and PATCH_TOOL in case.expected_tools
        and len(case.expected_tools) >= 2
        for case in cases
    ):
        errors.append("one combined case must require patch and KPL evidence")
    for case in cases:
        if case.category in {"supported", "combined"} and not case.expected_tools:
            errors.append(f"{case.id}: supported cases must expect tools")
        if case.category == "unsupported" and not case.expected_answer_terms:
            errors.append(f"{case.id}: unsupported cases need answer terms")
        if not set(case.expected_tools).issubset(case.allowed_tools):
            errors.append(f"{case.id}: expected tools must also be allowed")
    return {
        "passed": not errors,
        "case_count": len(cases),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-cases", type=Path, default=DEFAULT_RETRIEVAL_CASES_PATH)
    parser.add_argument("--agent-cases", type=Path, default=DEFAULT_AGENT_CASES_PATH)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--league-id", default="20260003")
    parser.add_argument("--report", type=Path, default=DEFAULT_LIVE_REPORT_PATH)
    parser.add_argument("--max-total-tokens", type=int, default=20_000)
    args = parser.parse_args()

    retrieval_cases = load_retrieval_cases(args.retrieval_cases)
    retrieval_catalog = validate_retrieval_catalog(retrieval_cases)
    agent_cases = load_cases(args.agent_cases)
    agent_catalog = validate_agent_catalog(agent_cases)
    if not retrieval_catalog["passed"] or not agent_catalog["passed"]:
        print("FAILED: patch research evaluation catalog is invalid")
        for error in [*retrieval_catalog["errors"], *agent_catalog["errors"]]:
            print(f"- {error}")
        return 1

    print(
        f"Catalog OK: {retrieval_catalog['case_count']} local retrieval cases; "
        f"{agent_catalog['case_count']} optional live agent cases."
    )
    retrieval_report = run_retrieval(retrieval_cases)
    if not retrieval_report["passed"]:
        return 1
    if not args.live:
        print("Offline retrieval evaluation only. Add --live for bounded Kimi routing checks.")
        return 0

    report = run_live(
        agent_cases,
        all_cases=agent_cases,
        league_id=args.league_id,
        report_path=args.report,
        max_total_tokens=args.max_total_tokens,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
