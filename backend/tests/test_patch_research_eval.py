import unittest

from app.agent.eval_patch_research import (
    DEFAULT_AGENT_CASES_PATH,
    RetrievalEvalCase,
    assess_retrieval_result,
    load_retrieval_cases,
    validate_agent_catalog,
    validate_retrieval_catalog,
)
from app.agent.eval_phase1 import load_cases
from app.agent.tools.patches import PatchSearchResponse


def response(*, announcement_id: str = "807018") -> PatchSearchResponse:
    return PatchSearchResponse(
        index_version="index-v1",
        result_count=1,
        results=[
            {
                "announcement_id": announcement_id,
                "title": "刘备平衡调整",
                "published_at": "2026-08-12",
                "hero_names": ["刘备"],
                "heading_path": ["英雄调整", "刘备"],
                "excerpt": "被动调整。",
                "source_url": "https://example.test/807018",
                "source_hash": "a" * 64,
            }
        ],
    )


class PatchResearchEvaluationTest(unittest.TestCase):
    def test_committed_catalogs_are_valid(self) -> None:
        retrieval_summary = validate_retrieval_catalog(load_retrieval_cases())
        agent_summary = validate_agent_catalog(load_cases(DEFAULT_AGENT_CASES_PATH))

        self.assertTrue(retrieval_summary["passed"], retrieval_summary["errors"])
        self.assertTrue(agent_summary["passed"], agent_summary["errors"])

    def test_retrieval_assessment_requires_provenance_and_stable_order(self) -> None:
        case = RetrievalEvalCase(
            id="liu-bei-test",
            arguments={"query": "刘备改动", "hero_name": "刘备", "limit": 2},
            min_results=1,
            max_results=2,
            expected_announcement_ids=["807018"],
            expected_hero_name="刘备",
            forbidden_heading_terms=["Source boundary"],
        )

        assessment = assess_retrieval_result(
            case,
            response(),
            repeat_result=response(),
        )

        self.assertTrue(assessment["passed"], assessment["failures"])

    def test_retrieval_assessment_rejects_unexpected_evidence(self) -> None:
        case = RetrievalEvalCase(
            id="liu-bei-test",
            arguments={"query": "刘备改动", "hero_name": "刘备", "limit": 2},
            min_results=1,
            max_results=2,
            expected_announcement_ids=["807018"],
            expected_hero_name="刘备",
        )

        assessment = assess_retrieval_result(case, response(announcement_id="wrong-id"))

        self.assertFalse(assessment["passed"])
        self.assertIn("expected announcement", assessment["failures"][0])


if __name__ == "__main__":
    unittest.main()
