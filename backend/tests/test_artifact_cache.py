import os
import tempfile
import unittest
from pathlib import Path

from app.agent.artifact_cache import ArtifactFormatError, JsonlArtifactCache


class JsonlArtifactCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.league_id = "league-1"
        self.directory = self.root / self.league_id
        self.directory.mkdir()
        self.path = self.directory / "stats.jsonl"
        self.path.write_text(
            '{"team_id":"a","value":1}\n'
            '{"team_id":"b","value":2}\n',
            encoding="utf-8",
        )
        self.cache = JsonlArtifactCache(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_rows_once_for_unchanged_version(self) -> None:
        with self.assertLogs("app.agent.artifact_cache", level="INFO"):
            first = self.cache.load(self.league_id, "stats.jsonl")
        second = self.cache.load(self.league_id, "stats.jsonl")

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertIs(first.rows, second.rows)
        self.assertEqual(len(second.rows), 2)
        self.assertEqual(second.rows[0]["team_id"], "a")

    def test_reloads_rows_and_indexes_after_version_change(self) -> None:
        calls = 0

        def build_index(rows):
            nonlocal calls
            calls += 1
            return {row["team_id"]: row for row in rows}

        first_index, first = self.cache.get_index(
            self.league_id,
            "stats.jsonl",
            "by-team",
            build_index,
        )
        repeated_index, repeated = self.cache.get_index(
            self.league_id,
            "stats.jsonl",
            "by-team",
            build_index,
        )

        self.assertIs(first_index, repeated_index)
        self.assertTrue(repeated.cache_hit)
        self.assertEqual(calls, 1)

        self.path.write_text(
            '{"team_id":"a","value":3}\n'
            '{"team_id":"c","value":4}\n'
            '{"team_id":"d","value":5}\n',
            encoding="utf-8",
        )
        current = self.path.stat().st_mtime_ns
        os.utime(self.path, ns=(current + 1_000_000, current + 1_000_000))

        changed_index, changed = self.cache.get_index(
            self.league_id,
            "stats.jsonl",
            "by-team",
            build_index,
        )

        self.assertFalse(changed.cache_hit)
        self.assertNotEqual(first.version, changed.version)
        self.assertEqual(set(changed_index), {"a", "c", "d"})
        self.assertEqual(calls, 2)

    def test_rejects_unsafe_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid league_id"):
            self.cache.load("../league", "stats.jsonl")
        with self.assertRaisesRegex(ValueError, "Invalid JSONL artifact filename"):
            self.cache.load(self.league_id, "../stats.jsonl")
        with self.assertRaisesRegex(ValueError, "Invalid JSONL artifact filename"):
            self.cache.load(self.league_id, "stats.json")

    def test_rejects_malformed_jsonl(self) -> None:
        self.path.write_text('{"valid":true}\nnot-json\n', encoding="utf-8")

        with self.assertRaisesRegex(ArtifactFormatError, "line 2"):
            self.cache.load(self.league_id, "stats.jsonl")

    def test_rejects_non_object_jsonl_rows(self) -> None:
        self.path.write_text('[1,2,3]\n', encoding="utf-8")

        with self.assertRaisesRegex(ArtifactFormatError, "Expected an object"):
            self.cache.load(self.league_id, "stats.jsonl")


if __name__ == "__main__":
    unittest.main()
