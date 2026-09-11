import test from "node:test";
import assert from "node:assert/strict";
import { fetchPowerRankings, invalidatePublishedData, resetPublishedDataCacheForTests } from "./api.js";

test("published artifact cache shares work without sharing a caller abort signal", async () => {
  resetPublishedDataCacheForTests();
  let calls = 0;
  const previous = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ value: ++calls }) });
  try {
    const controller = new AbortController();
    const [first, second] = await Promise.all([
      fetchPowerRankings("season", { signal: controller.signal }),
      fetchPowerRankings("season"),
    ]);
    controller.abort();
    assert.equal(calls, 1);
    assert.deepEqual(first, second);
  } finally { globalThis.fetch = previous; }
});

test("published artifact invalidation forces a fresh request", async () => {
  resetPublishedDataCacheForTests();
  let calls = 0;
  const previous = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ value: ++calls }) });
  try {
    await fetchPowerRankings("season");
    invalidatePublishedData("season");
    await fetchPowerRankings("season");
    assert.equal(calls, 2);
  } finally { globalThis.fetch = previous; }
});
