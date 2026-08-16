"""Tests for sharing scout-report generations between matching requests."""

from __future__ import annotations

from threading import Barrier, Lock, Thread
from time import sleep
import unittest

from app.agent.scout_report_cache import ScoutReportCache


class ScoutReportCacheTests(unittest.TestCase):
    def test_reuses_a_completed_report_without_sharing_mutable_data(self) -> None:
        cache = ScoutReportCache()
        calls = 0

        def generate() -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"answer": "report", "tool_calls": [{"name": "team_profile"}]}

        first = cache.get_or_generate(("league", "blue", "red", "en"), generate)
        first["answer"] = "changed"
        second = cache.get_or_generate(("league", "blue", "red", "en"), generate)

        self.assertEqual(calls, 1)
        self.assertEqual(second["answer"], "report")

    def test_collapses_simultaneous_generations_for_the_same_matchup(self) -> None:
        cache = ScoutReportCache()
        key = ("league", "blue", "red", "zh-CN")
        barrier = Barrier(3)
        calls = 0
        calls_lock = Lock()
        results: list[dict[str, object]] = []

        def generate() -> dict[str, object]:
            nonlocal calls
            with calls_lock:
                calls += 1
            sleep(0.05)
            return {"answer": "shared report"}

        def request_report() -> None:
            barrier.wait()
            results.append(cache.get_or_generate(key, generate))

        threads = [Thread(target=request_report), Thread(target=request_report)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(calls, 1)
        self.assertEqual([result["answer"] for result in results], ["shared report", "shared report"])


if __name__ == "__main__":
    unittest.main()
