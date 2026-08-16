"""Opt-in Task 2 tests; keep this file unchanged while implementing the index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.knowledge.patch_index import PatchDocumentRecord, PatchIndexBuilder


class PatchIndexBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.corpus = self.root / "knowledge"
        patches = self.corpus / "sources" / "official" / "patches"
        patches.mkdir(parents=True)
        (self.corpus / "metadata").mkdir()
        self.index_path = self.root / "kpl_patch_index.db"

        document = patches / "2026-08-12-807018-liu-bei.md"
        document.write_text(
            "---\n"
            'document_id: "tencent-patch-807018"\n'
            'announcement_id: "807018"\n'
            'source_url: "https://example.test/807018"\n'
            'raw_payload_sha256: "' + "a" * 64 + '"\n'
            "---\n\n"
            "# 英雄平衡性调整 | 刘备平衡\n\n"
            "## 英雄调整\n\n"
            "### 刘备\n\n"
            "被动：弹丸可穿透非英雄单位。\n\n"
            "### 其他说明\n\n"
            "该公告只记录游戏改动。\n",
            encoding="utf-8",
        )
        metadata = {
            "schema_version": 1,
            "documents": [
                {
                    "announcement_id": "807018",
                    "title": "英雄平衡性调整 | 刘备平衡",
                    "published_at": "2026-08-12 15:20:55",
                    "game_version": None,
                    "source_url": "https://example.test/807018",
                    "raw_payload": "raw/tencent-announcements/807018.json",
                    "raw_payload_sha256": "a" * 64,
                    "normalized_document": (
                        "sources/official/patches/2026-08-12-807018-liu-bei.md"
                    ),
                }
            ],
        }
        (self.corpus / "metadata" / "tencent-patch-index.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def builder(self) -> PatchIndexBuilder:
        return PatchIndexBuilder(corpus_root=self.corpus, index_path=self.index_path)

    def test_build_creates_a_rebuildable_index(self) -> None:
        result = self.builder().build()

        self.assertEqual(result.document_count, 1)
        self.assertGreaterEqual(result.chunk_count, 2)
        self.assertEqual(result.index_path, self.index_path)
        self.assertTrue(result.index_version)
        self.assertTrue(self.index_path.is_file())

    def test_chunks_preserve_source_provenance_and_heading_context(self) -> None:
        documents = self.builder()._load_documents()
        chunks = self.builder()._chunk_document(documents[0])

        liu_bei = next(chunk for chunk in chunks if "刘备" in chunk.heading_path)
        self.assertEqual(liu_bei.announcement_id, "807018")
        self.assertEqual(liu_bei.source_url, "https://example.test/807018")
        self.assertEqual(liu_bei.source_hash, "a" * 64)
        self.assertIn("弹丸可穿透", liu_bei.text)

    def test_fts_table_can_find_a_hero_section(self) -> None:
        self.builder().build()

        with sqlite3.connect(self.index_path) as db:
            rows = db.execute(
                "SELECT chunk_id, text FROM patch_chunks_fts WHERE patch_chunks_fts MATCH ?",
                ("刘备",),
            ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertIn("弹丸可穿透", rows[0][1])

    def test_rejects_a_document_path_outside_the_corpus(self) -> None:
        index_file = self.corpus / "metadata" / "tencent-patch-index.json"
        payload = json.loads(index_file.read_text(encoding="utf-8"))
        payload["documents"][0]["normalized_document"] = "../../outside.md"
        index_file.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "outside the corpus"):
            self.builder()._load_documents()

    def test_equipment_heading_creates_an_item_tag_without_a_short_hero_match(self) -> None:
        document_path = self.corpus / "sources" / "official" / "patches" / "equipment.md"
        document_path.write_text(
            "# 装备调整\n\n"
            "## 移动\n\n"
            "### 攻击\n\n"
            "#### 暗影战斧\n\n"
            "冷却缩减调整。\n",
            encoding="utf-8",
        )
        document = PatchDocumentRecord(
            announcement_id="equipment-1",
            title="装备调整",
            published_at="2026-08-12 15:20:55",
            source_url="https://example.test/equipment-1",
            source_hash="b" * 64,
            document_path=document_path,
        )

        chunk = self.builder()._chunk_document(document)[0]

        self.assertEqual(chunk.equipment_names, ("暗影战斧",))
        self.assertEqual(chunk.hero_names, ())


if __name__ == "__main__":
    unittest.main()
