"""Opt-in course tests for Task 1; do not modify as part of the exercise."""

from datetime import date
import unittest

from pydantic import ValidationError

from app.agent.tools.patches import (
    PatchEvidenceCard,
    PatchSearchResponse,
    SearchPatchNotesArguments,
)


VALID_CARD = {
    "announcement_id": "807018",
    "title": "英雄平衡性调整 | 刘备平衡",
    "published_at": "2026-08-12",
    "hero_names": ["刘备"],
    "heading_path": ["英雄调整", "刘备"],
    "excerpt": "被动：新增效果：弹丸可穿透非英雄单位。",
    "source_url": (
        "https://apps.game.qq.com/wmp/v3.1/public/"
        "searchNews.php?p0=18&source=web_pc&id=807018"
    ),
    "source_hash": "a" * 64,
}


class PatchToolContractTest(unittest.TestCase):
    def test_accepts_a_bounded_request(self) -> None:
        arguments = SearchPatchNotesArguments(
            query="刘备最近有什么改动？",
            hero_name="刘备",
            from_date="2026-07-01",
            to_date="2026-08-14",
        )

        self.assertEqual(arguments.query, "刘备最近有什么改动？")
        self.assertEqual(arguments.hero_name, "刘备")
        self.assertEqual(arguments.limit, 3)
        self.assertEqual(arguments.from_date, date(2026, 7, 1))

    def test_rejects_unknown_or_unbounded_request_fields(self) -> None:
        with self.assertRaises(ValidationError):
            SearchPatchNotesArguments(query="刘备改动", sql="SELECT * FROM patches")
        with self.assertRaises(ValidationError):
            SearchPatchNotesArguments(query="刘备改动", limit=6)

    def test_rejects_invalid_request_dates_and_blank_query(self) -> None:
        with self.assertRaises(ValidationError):
            SearchPatchNotesArguments(
                query="刘备改动",
                from_date="2026-08-14",
                to_date="2026-07-01",
            )
        with self.assertRaises(ValidationError):
            SearchPatchNotesArguments(query="   ")

    def test_evidence_card_requires_citable_provenance(self) -> None:
        card = PatchEvidenceCard(**VALID_CARD)
        self.assertEqual(card.announcement_id, "807018")
        with self.assertRaises(ValidationError):
            PatchEvidenceCard(**{**VALID_CARD, "source_url": "http://example.test"})
        with self.assertRaises(ValidationError):
            PatchEvidenceCard(**{**VALID_CARD, "source_hash": "short"})

    def test_equipment_evidence_does_not_require_a_hero_tag(self) -> None:
        card = PatchEvidenceCard(
            **{
                **VALID_CARD,
                "entity_type": "equipment",
                "hero_names": [],
                "equipment_names": ["暗影战斧"],
            }
        )

        self.assertEqual(card.entity_type, "equipment")
        self.assertEqual(card.equipment_names, ["暗影战斧"])

    def test_response_count_must_match_evidence(self) -> None:
        response = PatchSearchResponse(
            source_type="tencent_patch_notes",
            index_version="task-01-example",
            result_count=1,
            results=[VALID_CARD],
        )
        self.assertEqual(response.result_count, 1)
        with self.assertRaises(ValidationError):
            PatchSearchResponse(
                source_type="tencent_patch_notes",
                index_version="task-01-example",
                result_count=2,
                results=[VALID_CARD],
            )


if __name__ == "__main__":
    unittest.main()
