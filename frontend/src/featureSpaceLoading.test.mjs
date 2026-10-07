import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse } from "@vue/compiler-sfc";
import { computed, ref, shallowRef, watch } from "vue";
import { createModelSession } from "./modelSession.js";
import { createSeasonStartup } from "./seasonStartup.js";
import { createRequestScope } from "./requestScope.js";
import { heroCatalog } from "./heroCatalog.js";
import { supplementalHeroes } from "./supplementalHeroes.js";

const pagePath = new URL("./HeroFeatureSpacePage.vue", import.meta.url);
const widgetPath = new URL("./LineupAnalyzerWidget.vue", import.meta.url);

async function mountPage(overrides = {}) {
  const source = await readFile(pagePath, "utf8");
  const script = parse(source).descriptor.scriptSetup.content.replace(/^import[\s\S]*?from ["'][^"']+["'];\n/gm, "");
  const mounted = [], cleanup = [];
  const context = {
    ref, shallowRef, computed, watch, createModelSession, createSeasonStartup, createRequestScope,
    heroCatalog, supplementalHeroes, selectedLeagueId: ref(""), language: ref("en"),
    onMounted: (fn) => mounted.push(fn), onBeforeUnmount: (fn) => cleanup.push(fn),
    useSeasonCatalog: () => ({ seasons: ref([]), loadSeasons: async () => {} }),
    useLatestRequest: () => (fn) => fn(undefined, () => true),
    getStored: () => null, setStored() {}, t: (key) => key, finishStartupLoading() {},
    heroAsset: () => "", mechanicLabel: (key) => key, heroSearchAliases: {},
    window: { location: { search: "" } },
    fetchActiveModel: async () => ({ model_version: "pinned" }),
    fetchHeroCatalog: async () => ({ rows: heroCatalog }),
    fetchHeroResponses: async () => ({ rows: [] }), fetchBattleLineups: async () => ({ battles: [] }),
    fetchLearnedFeatureSpace: async () => ({ rows: [] }), fetchHeroMatchupRecommendations: async () => ({}),
    ...overrides,
  };
  const returned = "pickerHeroes, toolsReady, leagueId, catalog, responses, historicalLineups, historicalState, payload, toolError, favoriteHeroIds, opponentHeroIds, addFavorite, addOpponent, loadToolSeason, loadHistory, toggleDeepDive";
  const page = new Function(...Object.keys(context), `${script}\nreturn { ${returned} };`)(...Object.values(context));
  mounted.forEach((fn) => fn());
  return { ...page, dispose() { cleanup.forEach((fn) => fn()); } };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("hero choices work while season and model startup are pending; Explore and history stay idle", async () => {
  const model = deferred(), seasons = deferred();
  let maps = 0, history = 0;
  const page = await mountPage({
    fetchActiveModel: () => model.promise,
    useSeasonCatalog: () => ({ seasons: ref([]), loadSeasons: () => seasons.promise }),
    fetchLearnedFeatureSpace: async () => { maps++; return {}; },
    fetchBattleLineups: async () => { history++; return {}; },
  });
  assert.ok(page.pickerHeroes.value.length > 120);
  page.addFavorite(105); page.addOpponent(106);
  assert.deepEqual(page.favoriteHeroIds.value, [105]);
  assert.deepEqual(page.opponentHeroIds.value, [106]);
  assert.equal(page.toolsReady.value, false);
  page.leagueId.value = "20260004";
  seasons.resolve([]); model.resolve({ model_version: "pinned" });
  await flush();
  assert.equal(page.toolsReady.value, true);
  assert.equal(maps, 0); assert.equal(history, 0);
  assert.deepEqual(page.opponentHeroIds.value, [106]);
  page.dispose();
});

test("season evidence loads independently; late catalog and history cannot restore the old season", async () => {
  const oldCatalog = deferred(), oldHistory = deferred();
  let historyCalls = 0;
  const page = await mountPage({
    selectedLeagueId: ref("old"),
    fetchHeroCatalog: (season) => season === "old" ? oldCatalog.promise : Promise.resolve({ rows: [{ hero_id: 105, hero_name: "new" }] }),
    fetchHeroResponses: async (season) => ({ rows: [{ season }] }),
    fetchBattleLineups: () => { historyCalls++; return oldHistory.promise; },
  });
  await flush();
  assert.deepEqual(page.responses.value.rows, [{ season: "old" }]);
  assert.equal(page.toolsReady.value, false);
  const historyLoad = page.loadHistory();
  page.loadHistory(); assert.equal(historyCalls, 1);
  page.leagueId.value = "new";
  await flush();
  oldCatalog.resolve({ rows: [{ hero_id: 106, hero_name: "old" }] });
  oldHistory.resolve({ battles: [{ old: true }] });
  await historyLoad; await flush();
  assert.equal(page.catalog.value.rows[0].hero_name, "new");
  assert.deepEqual(page.responses.value.rows, [{ season: "new" }]);
  assert.deepEqual(page.historicalLineups.value, []);
  assert.equal(page.historicalState.value, "idle");
  page.dispose();
});

test("missing model leaves both selectors usable without legacy recommendations", async () => {
  const page = await mountPage({ fetchActiveModel: async () => { throw new Error("missing"); } });
  await flush();
  assert.ok(page.toolError.value);
  page.addFavorite(105); page.addOpponent(106);
  assert.deepEqual(page.favoriteHeroIds.value, [105]);
  assert.equal(page.toolsReady.value, false);
  page.dispose();
});

test("opening Explore loads the pinned map once without changing tool choices", async () => {
  let maps = 0;
  const page = await mountPage({
    selectedLeagueId: ref("S4"),
    fetchLearnedFeatureSpace: async (season, version) => {
      maps++; assert.equal(season, "S4"); assert.equal(version, "pinned");
      return { rows: [{ hero_id: 105, hero_name: "廉颇", x: 0, y: 0 }] };
    },
  });
  await flush();
  page.addFavorite(105); page.addOpponent(106);
  assert.equal(maps, 0);
  page.toggleDeepDive({ target: { open: true } });
  await flush();
  assert.equal(maps, 1);
  assert.ok(page.payload.value);
  page.toggleDeepDive({ target: { open: false } });
  page.toggleDeepDive({ target: { open: true } });
  await flush();
  assert.equal(maps, 1);
  assert.deepEqual(page.favoriteHeroIds.value, [105]);
  assert.deepEqual(page.opponentHeroIds.value, [106]);
  page.dispose();
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
