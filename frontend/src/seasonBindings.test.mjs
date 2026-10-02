import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse, compileScript } from "@vue/compiler-sfc";

test("public season control exists only in More; management keeps both explicit save controls", async () => {
  for (const page of ["HeroFeatureSpacePage", "DraftSimulatorPage", "TeamSynergyPage", "RankingsPage", "VisualizationPage"]) {
    const source = await readFile(new URL(`./${page}.vue`, import.meta.url), "utf8");
    const { descriptor } = parse(source);
    assert.doesNotMatch(descriptor.template.content, /v-model="leagueId"|simulator-season-select/);
    if (page === "DraftSimulatorPage") {
      const script = compileScript(descriptor, { id: page });
      assert.ok(script.bindings.seasons); // selectedSeason's catalog must exist at runtime.
      assert.match(descriptor.template.content, /settings-trigger/);
    }
  }
  const management = await readFile(new URL("./ManagementPage.vue", import.meta.url), "utf8");
  assert.match(management, /chooseManagementYear\(\$event.target.value\)/);
  assert.match(management, /chooseManagementLeague\(\$event.target.value\)/);
  assert.doesNotMatch(management, /v-model="leagueId"|v-model="selectedYear"/);
  const app = await readFile(new URL("./App.vue", import.meta.url), "utf8");
  assert.match(app, /v-model="utilityLeagueId"/);
});
