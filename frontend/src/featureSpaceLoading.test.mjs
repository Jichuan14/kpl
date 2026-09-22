import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const pagePath = new URL("./HeroFeatureSpacePage.vue", import.meta.url);
const widgetPath = new URL("./LineupAnalyzerWidget.vue", import.meta.url);

test("feature board commits before optional response and history requests start", async () => {
  const source = await readFile(pagePath, "utf8");
  const payloadCommit = source.indexOf("payload.value = result;");
  const responseRequest = source.indexOf("void fetchHeroResponses(leagueId.value)");
  const historyRequest = source.indexOf("void fetchBattleLineups(leagueId.value)");
  assert.ok(payloadCommit >= 0);
  assert.ok(responseRequest > payloadCommit);
  assert.ok(historyRequest > payloadCommit);
  assert.equal(source.includes("Promise.all([\n      fetchLearnedFeatureSpace"), false);
});

test("closed history selector creates no battle option rows and an open page is bounded", async () => {
  const source = await readFile(widgetPath, "utf8");
  const closedGuard = source.indexOf('<div v-if="historyOpen" class="historical-lineup-options"');
  const battleRows = source.indexOf("v-for=\"battle in visibleHistoricalLineups\"");
  assert.ok(closedGuard >= 0);
  assert.ok(battleRows > closedGuard);
  assert.match(source, /const HISTORY_PAGE_SIZE = 24;/);
  assert.match(source, /slice\(start, start \+ HISTORY_PAGE_SIZE\)/);
});
