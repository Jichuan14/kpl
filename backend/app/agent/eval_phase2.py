"""Offline catalog gate and optional bounded-live evaluation for Phase 2."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from app.agent.eval_phase1 import Phase1EvalCase, load_cases, run_live
from app.agent.tool_registry import TOOLS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = PROJECT_ROOT / "agent" / "evals" / "phase_2_cases.jsonl"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "agent" / "evals" / "phase_2_live_report.json"
PHASE2_TOOLS = {
    "predict_next_draft_action",
    "get_team_draft_tendencies",
    "get_team_opening_sequences",
    "get_team_combo_performance",
    "get_player_hero_pool",
    "get_recent_team_trends",
    "recommend_value_draft_action",
    "score_current_lineup",
}


def validate_catalog(cases: list[Phase1EvalCase]) -> dict[str, Any]:
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
    covered = {tool for case in cases for tool in case.expected_tools}
    uncovered = sorted(PHASE2_TOOLS - covered)
    if uncovered:
        errors.append("Phase 2 tools without evaluation cases: " + ", ".join(uncovered))
    required_categories = {"supported", "combined", "clarification", "unsupported"}
    categories = {case.category for case in cases}
    missing = sorted(required_categories - categories)
    if missing:
        errors.append("missing categories: " + ", ".join(missing))
    if not any(case.category == "combined" and len(case.expected_tools) >= 2 for case in cases):
        errors.append("combined coverage must require at least two tools")
    for case in cases:
        if not set(case.expected_tools).issubset(set(case.allowed_tools)):
            errors.append(f"{case.id}: expected tools must also be allowed")
        if case.category in {"clarification", "unsupported"} and not case.expected_answer_terms:
            errors.append(f"{case.id}: answer terms are required")
    return {
        "passed": not errors,
        "case_count": len(cases),
        "phase2_tool_count": len(PHASE2_TOOLS),
        "categories": sorted(categories),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--league-id", default="20260003")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--max-total-tokens", type=int, default=80000)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    catalog = validate_catalog(cases)
    if not catalog["passed"]:
        print("FAILED: Phase 2 evaluation catalog is invalid")
        for error in catalog["errors"]:
            print(f"- {error}")
        return 1
    print(
        f"Catalog OK: {catalog['case_count']} cases cover "
        f"{catalog['phase2_tool_count']} Phase 2 tools and "
        f"{len(catalog['categories'])} categories."
    )
    if not args.live:
        print("Offline validation only. Add --live for bounded Kimi evaluation.")
        return 0
    selected = cases
    if args.case_id:
        wanted = set(args.case_id)
        unknown = sorted(wanted - {case.id for case in cases})
        if unknown:
            print("FAILED: unknown case IDs: " + ", ".join(unknown))
            return 1
        selected = [case for case in cases if case.id in wanted]
    report = run_live(
        selected,
        all_cases=cases,
        league_id=args.league_id,
        report_path=args.report,
        max_total_tokens=args.max_total_tokens,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
