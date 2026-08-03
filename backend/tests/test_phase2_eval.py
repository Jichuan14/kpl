import unittest

from app.agent.eval_phase1 import load_cases
from app.agent.eval_phase2 import DEFAULT_CASES_PATH, validate_catalog


class Phase2EvaluationTest(unittest.TestCase):
    def test_catalog_covers_phase2_tools_and_categories(self) -> None:
        summary = validate_catalog(load_cases(DEFAULT_CASES_PATH))
        self.assertTrue(summary["passed"], summary["errors"])
        self.assertEqual(summary["phase2_tool_count"], 6)
        self.assertEqual(
            set(summary["categories"]),
            {"supported", "combined", "clarification", "unsupported"},
        )


if __name__ == "__main__":
    unittest.main()
