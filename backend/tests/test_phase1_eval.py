import unittest
import json
import tempfile
from pathlib import Path

from app.agent.eval_phase1 import (
    Phase1EvalCase,
    assess_result,
    load_cases,
    reassess_failed_results,
    validate_catalog,
)


def case(**overrides) -> Phase1EvalCase:
    values = {
        "id": "next-action-test",
        "category": "supported",
        "question": "What is next?",
        "expected_tools": ["predict_next_draft_action"],
        "allowed_tools": ["predict_next_draft_action"],
        "requires_board": True,
        "model_type": "learnable",
        "max_answer_chars": 500,
    }
    values.update(overrides)
    return Phase1EvalCase.model_validate(values)


def result(*, answer="Hero A is most likely.", tools=None, tokens=20):
    return {
        "answer": answer,
        "tool_calls": tools or [],
        "usage": {"total_tokens": tokens},
    }


class Phase1EvaluationTest(unittest.TestCase):
    def test_committed_catalog_covers_every_tool_and_category(self) -> None:
        summary = validate_catalog(load_cases())

        self.assertTrue(summary["passed"], summary["errors"])
        self.assertEqual(summary["registered_tool_count"], 7)
        self.assertEqual(
            set(summary["categories"]),
            {"supported", "combined", "clarification", "unsupported"},
        )

    def test_supported_case_passes_with_expected_selected_model(self) -> None:
        assessment = assess_result(
            case(),
            result(
                tools=[
                    {
                        "name": "predict_next_draft_action",
                        "success": True,
                        "result": {"model_type": "learnable"},
                    }
                ]
            ),
        )

        self.assertTrue(assessment["passed"], assessment["failures"])

    def test_wrong_tool_and_model_fail_routing_gate(self) -> None:
        assessment = assess_result(
            case(),
            result(
                tools=[
                    {
                        "name": "simulate_future_draft",
                        "success": True,
                        "result": {"model_type": "stats"},
                    }
                ]
            ),
        )

        self.assertFalse(assessment["passed"])
        self.assertTrue(any("missing tools" in item for item in assessment["failures"]))
        self.assertTrue(any("unexpected tools" in item for item in assessment["failures"]))

    def test_markdown_table_and_verbose_answer_fail_format_gate(self) -> None:
        assessment = assess_result(
            case(max_answer_chars=100),
            result(
                answer="| Hero | Rate |\n|---|---|\n| A | 70% |" + " x" * 60,
                tools=[
                    {
                        "name": "predict_next_draft_action",
                        "success": True,
                        "result": {"model_type": "learnable"},
                    }
                ],
            ),
        )

        self.assertFalse(assessment["passed"])
        self.assertTrue(any("Markdown table" in item for item in assessment["failures"]))
        self.assertTrue(any("exceeds" in item for item in assessment["failures"]))

    def test_unsupported_case_requires_limitation_language(self) -> None:
        unsupported = case(
            id="unsupported-test",
            category="unsupported",
            expected_tools=[],
            allowed_tools=[],
            expect_no_tools=True,
            requires_board=False,
            expected_answer_terms=[["cannot", "not supported"]],
        )

        failed = assess_result(unsupported, result(answer="Wolves will pick Hero A."))
        passed = assess_result(
            unsupported,
            result(answer="I cannot answer that team-specific question in Phase 1."),
        )

        self.assertFalse(failed["passed"])
        self.assertTrue(passed["passed"], passed["failures"])

    def test_failed_report_can_be_reassessed_without_api_calls(self) -> None:
        unsupported = case(
            id="unsupported-test",
            category="unsupported",
            expected_tools=[],
            allowed_tools=[],
            expect_no_tools=True,
            requires_board=False,
            expected_answer_terms=[["cannot", "not supported"]],
        )
        report = {
            "total_tokens": 25,
            "results": [
                {
                    "id": "unsupported-test",
                    "category": "unsupported",
                    "passed": False,
                    "failures": ["old wording gate"],
                    "actual_tools": [],
                    "tokens": 25,
                    "answer": "I cannot answer that in Phase 1.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")

            updated = reassess_failed_results([unsupported], path)

        self.assertTrue(updated["passed"])
        self.assertEqual(updated["total_tokens"], 25)


if __name__ == "__main__":
    unittest.main()
