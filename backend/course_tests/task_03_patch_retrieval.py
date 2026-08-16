"""Opt-in Task 3 tests; deterministic patch retrieval stays outside the Coach."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.agent.tools.patches import SearchPatchNotesArguments
from app.knowledge.patch_index import PatchIndexBuilder
from app.knowledge.patch_retrieval import PatchRetriever


class PatchRetrieverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.corpus = root / "knowledge"
        patches = self.corpus / "sources" / "official" / "patches"
        patches.mkdir(parents=True)
        (self.corpus / "metadata").mkdir()
        (self.corpus / "sources" / "official" / "herolist.json").write_text(
            json.dumps([{"cname": "刘备"}], ensure_ascii=False), encoding="utf-8"
        )
        (patches / "2026-08-12-807018-liu-bei.md").write_text(
            "---\nsource_url: https://example.test/807018\n---\n\n"
            "# 刘备平衡说明\n\n"
            "## Source boundary\n\n"
            "This is ingestion metadata, not patch evidence.\n\n"
            "# 英雄调整\n\n"
            "## 刘备\n\n"
            "被动：新增效果：弹丸可穿透非英雄单位。\n",
            encoding="utf-8",
        )
        (self.corpus / "metadata" / "tencent-patch-index.json").write_text(
            json.dumps(
                {
                    "documents": [
                        {
                            "announcement_id": "807018",
                            "title": "英雄平衡性调整 | 刘备平衡",
                            "published_at": "2026-08-12 15:20:55",
                            "source_url": "https://example.test/807018",
                            "raw_payload_sha256": "a" * 64,
                            "normalized_document": (
                                "sources/official/patches/"
                                "2026-08-12-807018-liu-bei.md"
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.index_path = root / "kpl_patch_index.db"
        PatchIndexBuilder(corpus_root=self.corpus, index_path=self.index_path).build()
        self.retriever = PatchRetriever(index_path=self.index_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_returns_citable_evidence_for_a_hero_query(self) -> None:
        response = self.retriever.search(
            SearchPatchNotesArguments(
                query="刘备最近有什么改动？",
                hero_name="刘备",
            )
        )

        self.assertEqual(response.source_type, "tencent_patch_notes")
        self.assertEqual(response.result_count, 1)
        card = response.results[0]
        self.assertEqual(card.announcement_id, "807018")
        self.assertEqual(card.hero_names, ["刘备"])
        self.assertIn("弹丸可穿透", card.excerpt)
        self.assertEqual(str(card.source_url), "https://example.test/807018")

    def test_applies_date_filters_before_returning_evidence(self) -> None:
        response = self.retriever.search(
            SearchPatchNotesArguments(
                query="刘备改动",
                hero_name="刘备",
                from_date="2026-08-13",
            )
        )

        self.assertEqual(response.result_count, 0)
        self.assertTrue(response.warnings)

    def test_results_are_bounded_and_deterministic(self) -> None:
        arguments = SearchPatchNotesArguments(query="刘备改动", hero_name="刘备", limit=1)
        first = self.retriever.search(arguments)
        second = self.retriever.search(arguments)

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertLessEqual(first.result_count, 1)


if __name__ == "__main__":
    unittest.main()
