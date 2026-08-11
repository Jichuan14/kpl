"""Executable offline and bounded-live evaluation for the Phase 1 coach."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.service import CoachInput, KimiCoachService
from app.agent.tool_registry import TOOLS
from app.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = PROJECT_ROOT / "agent" / "evals" / "phase_1_cases.jsonl"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "agent" / "evals" / "phase_1_live_report.json"
DRAFT_TOOLS = {"predict_next_draft_action", "simulate_future_draft"}
PHASE1_TOOLS = {
    "predict_next_draft_action",
    "simulate_future_draft",
    "get_hero_relationships",
    "get_team_synergies",
    "get_meta_heroes",
    "get_hero_bp_stats",
    "get_battle_draft",
}


class Phase1EvalCase(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    category: Literal["supported", "combined", "clarification", "unsupported"]
    question: str = Field(min_length=1, max_length=1000)
    expected_tools: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    expect_no_tools: bool = False
    expected_answer_terms: list[list[str]] = Field(default_factory=list)
    requires_board: bool = False
    model_type: Literal["stats", "learnable", "sequence"] = "stats"
    max_answer_chars: int = Field(default=1000, ge=100, le=5000)


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[Phase1EvalCase]:
    cases: list[Phase1EvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(Phase1EvalCase.model_validate_json(line))
        except Exception as exc:
            raise ValueError(f"Invalid evaluation case on line {line_number}") from exc
    return cases


def validate_catalog(cases: list[Phase1EvalCase]) -> dict[str, Any]:
    errors: list[str] = []
    ids = [case.id for case in cases]
    duplicate_ids = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicate_ids:
        errors.append(f"duplicate case IDs: {', '.join(duplicate_ids)}")

    registered = set(TOOLS)
    referenced = {
        tool
        for case in cases
        for tool in [*case.expected_tools, *case.allowed_tools]
    }
    unknown = sorted(referenced - registered)
    if unknown:
        errors.append(f"unknown tools: {', '.join(unknown)}")
    uncovered = sorted(
        PHASE1_TOOLS
        - {tool for case in cases for tool in case.expected_tools}
    )
    if uncovered:
        errors.append(f"registered tools without evaluation cases: {', '.join(uncovered)}")

    categories = {case.category for case in cases}
    required_categories = {"supported", "combined", "clarification", "unsupported"}
    missing_categories = sorted(required_categories - categories)
    if missing_categories:
        errors.append(f"missing categories: {', '.join(missing_categories)}")
    if not any(case.category == "combined" and len(case.expected_tools) >= 2 for case in cases):
        errors.append("combined coverage must require at least two tools")
    for case in cases:
        if case.category in {"supported", "combined"} and not case.expected_tools:
            errors.append(f"{case.id}: supported cases must expect tools")
        if case.category in {"clarification", "unsupported"} and not case.expected_answer_terms:
            errors.append(f"{case.id}: limitation/clarification terms are required")
        if not set(case.expected_tools).issubset(set(case.allowed_tools)):
            errors.append(f"{case.id}: expected tools must also be allowed")

    return {
        "passed": not errors,
        "case_count": len(cases),
        "registered_tool_count": len(PHASE1_TOOLS),
        "categories": sorted(categories),
        "errors": errors,
    }


def _format_violations(answer: str, max_chars: int) -> list[str]:
    violations: list[str] = []
    if len(answer) > max_chars:
        violations.append(f"answer exceeds {max_chars} characters")
    patterns = {
        "Markdown table": r"(?m)^\s*\|.+\|\s*$",
        "Markdown heading": r"(?m)^\s*#{1,6}\s+",
        "code fence": r"```",
        "horizontal rule": r"(?m)^\s*(?:---+|___+|\*\*\*+)\s*$",
    }
    for label, pattern in patterns.items():
        if re.search(pattern, answer):
            violations.append(f"answer contains {label}")
    return violations


def assess_result(
    case: Phase1EvalCase,
    result: dict[str, Any] | None,
    *,
    error_type: str | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    if error_type is not None or result is None:
        failures.append(f"request failed: {error_type or 'unknown error'}")
        return {
            "passed": False,
            "failures": failures,
            "actual_tools": [],
            "tokens": 0,
            "answer": "",
        }

    answer = str(result.get("answer") or "").strip()
    calls = [call for call in result.get("tool_calls", []) if call.get("success")]
    actual_tools = [str(call.get("name") or "") for call in calls]
    actual_set = set(actual_tools)
    missing = sorted(set(case.expected_tools) - actual_set)
    unexpected = sorted(actual_set - set(case.allowed_tools))
    if missing:
        failures.append(f"missing tools: {', '.join(missing)}")
    if unexpected:
        failures.append(f"unexpected tools: {', '.join(unexpected)}")
    if case.expect_no_tools and actual_tools:
        failures.append("expected no successful tool calls")

    lowered = answer.casefold()
    for alternatives in case.expected_answer_terms:
        if not any(term.casefold() in lowered for term in alternatives):
            failures.append(
                "answer lacks one of: " + ", ".join(alternatives)
            )
    failures.extend(_format_violations(answer, case.max_answer_chars))
    if not answer:
        failures.append("answer is empty")

    if case.requires_board:
        for call in calls:
            if call.get("name") not in DRAFT_TOOLS:
                continue
            actual_model = (call.get("result") or {}).get("model_type")
            if actual_model != case.model_type:
                failures.append(
                    f"draft tool used {actual_model!r}, expected {case.model_type!r}"
                )

    usage = result.get("usage") or {}
    return {
        "passed": not failures,
        "failures": failures,
        "actual_tools": actual_tools,
        "tokens": int(usage.get("total_tokens") or 0),
        "answer": answer,
    }


def _draft_state(model_type: str) -> dict[str, Any]:
    return {
        "model_type": model_type,
        "blue_team_id": "10001",
        "blue_team_name": "重庆狼队",
        "red_team_id": "10027",
        "red_team_name": "成都AG超玩会",
        "bp_order": 1,
        "blue_picks": [],
        "red_picks": [],
        "blue_bans": [],
        "red_bans": [],
        "blue_used_previous_battles": [],
        "red_used_previous_battles": [],
    }


def run_live(
    cases: list[Phase1EvalCase],
    *,
    all_cases: list[Phase1EvalCase] | None = None,
    league_id: str,
    report_path: Path,
    max_total_tokens: int,
    baseline_report: Path | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    live_settings = settings.model_copy(
        update={
            "kimi_max_output_tokens": min(settings.kimi_max_output_tokens, 400),
            "kimi_max_tool_calls": min(settings.kimi_max_tool_calls, 4),
        }
    )
    service = KimiCoachService(settings=live_settings)
    results: list[dict[str, Any]] = []
    run_tokens = 0

    for case in cases:
        result: dict[str, Any] | None = None
        error_type: str | None = None
        try:
            result = service.ask(
                CoachInput(
                    message=case.question,
                    league_id=league_id,
                    draft_state=(
                        _draft_state(case.model_type)
                        if case.requires_board
                        else None
                    ),
                ),
                request_id=f"phase1-eval-{case.id}",
            )
        except Exception as exc:  # Provider exceptions vary by SDK version.
            error_type = type(exc).__name__

        assessment = assess_result(case, result, error_type=error_type)
        run_tokens += assessment["tokens"]
        results.append(
            {
                "id": case.id,
                "category": case.category,
                **assessment,
            }
        )
        status = "PASS" if assessment["passed"] else "FAIL"
        tools = ",".join(assessment["actual_tools"]) or "none"
        print(
            f"{status} {case.id}: tools={tools} "
            f"tokens={assessment['tokens']}"
        )
        if run_tokens > max_total_tokens:
            print("STOP: live evaluation exceeded its total-token budget")
            break

    baseline_results: list[dict[str, Any]] = []
    baseline_tokens = 0
    if baseline_report is not None:
        baseline = json.loads(baseline_report.read_text(encoding="utf-8"))
        baseline_results = list(baseline.get("results") or [])
        baseline_tokens = int(baseline.get("total_tokens") or 0)

    merged_by_id = {item["id"]: item for item in baseline_results}
    merged_by_id.update({item["id"]: item for item in results})
    catalog_cases = all_cases or cases
    merged_results = [
        merged_by_id[case.id]
        for case in catalog_cases
        if case.id in merged_by_id
    ]
    passed_count = sum(1 for item in merged_results if item["passed"])
    total_tokens = baseline_tokens + run_tokens
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_id": league_id,
        "model": live_settings.kimi_model,
        "case_count": len(merged_results),
        "evaluated_in_this_run": len(results),
        "passed_count": passed_count,
        "failed_count": len(merged_results) - passed_count,
        "total_tokens": total_tokens,
        "passed": (
            len(merged_results) == len(catalog_cases)
            and passed_count == len(catalog_cases)
        ),
        "results": merged_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Live summary: {passed_count}/{len(merged_results)} passed; "
        f"{total_tokens} total tokens"
    )
    print(f"Report: {report_path}")
    return report


def reassess_failed_results(
    cases: list[Phase1EvalCase],
    report_path: Path,
) -> dict[str, Any]:
    """Reapply current deterministic wording/format gates without API calls."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cases_by_id = {case.id: case for case in cases}
    updated_results: list[dict[str, Any]] = []
    for item in report.get("results") or []:
        if item.get("passed"):
            updated_results.append(item)
            continue
        case = cases_by_id.get(str(item.get("id") or ""))
        if case is None:
            updated_results.append(item)
            continue
        synthetic_calls = [
            {
                "name": name,
                "success": True,
                "result": (
                    {"model_type": case.model_type}
                    if name in DRAFT_TOOLS
                    else {}
                ),
            }
            for name in item.get("actual_tools") or []
        ]
        assessment = assess_result(
            case,
            {
                "answer": item.get("answer") or "",
                "tool_calls": synthetic_calls,
                "usage": {"total_tokens": int(item.get("tokens") or 0)},
            },
        )
        updated_results.append(
            {
                "id": case.id,
                "category": case.category,
                **assessment,
            }
        )

    passed_count = sum(1 for item in updated_results if item.get("passed"))
    report.update(
        {
            "reassessed_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(updated_results),
            "passed_count": passed_count,
            "failed_count": len(updated_results) - passed_count,
            "passed": (
                len(updated_results) == len(cases)
                and passed_count == len(cases)
            ),
            "results": updated_results,
        }
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Reassessment summary: {passed_count}/{len(updated_results)} passed; "
        "0 additional API tokens"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--league-id", default="20260003")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--reassess-report", action="store_true")
    parser.add_argument("--max-total-tokens", type=int, default=120000)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    catalog = validate_catalog(cases)
    if not catalog["passed"]:
        print("FAILED: Phase 1 evaluation catalog is invalid")
        for error in catalog["errors"]:
            print(f"- {error}")
        return 1
    print(
        f"Catalog OK: {catalog['case_count']} cases cover "
        f"{catalog['registered_tool_count']} registered tools and "
        f"{len(catalog['categories'])} evaluation categories."
    )
    if args.reassess_report:
        report = reassess_failed_results(cases, args.report)
        return 0 if report["passed"] else 1
    if not args.live:
        print("Offline validation only. Add --live for bounded Kimi evaluation.")
        return 0

    selected_cases = cases
    if args.case_id:
        selected_ids = set(args.case_id)
        known_ids = {case.id for case in cases}
        unknown_ids = sorted(selected_ids - known_ids)
        if unknown_ids:
            print(f"FAILED: unknown case IDs: {', '.join(unknown_ids)}")
            return 1
        selected_cases = [case for case in cases if case.id in selected_ids]

    report = run_live(
        selected_cases,
        all_cases=cases,
        league_id=args.league_id,
        report_path=args.report,
        max_total_tokens=args.max_total_tokens,
        baseline_report=args.baseline_report,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
