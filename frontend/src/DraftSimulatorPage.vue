<script setup>
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchDraftModel,
  fetchVisualizationSeasons,
  simulateDraft,
} from "./api";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { language, t } from "./i18n";

const leagueId = selectedLeagueId;
const seasons = ref([]);
const model = ref(null);
const result = ref(null);
const loading = ref(false);
const simulating = ref(false);
const error = ref("");
const search = ref("");
const rollouts = ref(100);
const bpOrder = ref(1);
const board = ref(emptyBoard());
const history = ref([]);
const globalMode = ref("single");
const seriesGame = ref(1);
const bestOf = ref(5);
const globalUsed = ref({ blue: [], red: [] });
const pickerTarget = ref("draft");

function emptyBoard() {
  return {
    blue_picks: [],
    red_picks: [],
    blue_bans: [],
    red_bans: [],
  };
}

const currentStep = computed(() =>
  model.value?.draft_sequence?.find(
    (step) => Number(step.bp_order) === Number(bpOrder.value)
  ) || null
);

const currentLabel = computed(() => {
  if (!currentStep.value) return t("Draft complete");
  const side = t(currentStep.value.side === "blue" ? "Blue" : "Red");
  const action = t(currentStep.value.action === "ban" ? "ban" : "pick");
  return `${side} ${action} · ${t("action")} ${currentStep.value.bp_order}`;
});

const usedHeroIds = computed(
  () =>
    new Set(
      Object.values(board.value).flatMap((heroIds) => heroIds.map(Number))
    )
);

const heroes = computed(() => model.value?.heroes || []);

const pickerTitle = computed(() => {
  if (pickerTarget.value === "global-blue") return t("Add Blue's earlier-game hero");
  if (pickerTarget.value === "global-red") return t("Add Red's earlier-game hero");
  return currentLabel.value;
});

const probabilityByHeroId = computed(
  () =>
    new Map(
      (result.value?.next_action_probabilities || []).map((row) => [
        Number(row.hero_id),
        Number(row.probability),
      ])
    )
);

const availableHeroes = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase();
  const targetSide = pickerTarget.value.replace("global-", "");
  const candidates =
    pickerTarget.value === "draft" && result.value
      ? result.value.next_action_probabilities
          .map((row) => heroes.value.find((hero) => Number(hero.hero_id) === Number(row.hero_id)))
          .filter(Boolean)
      : heroes.value;
  return candidates
    .filter((hero) => {
      const heroId = Number(hero.hero_id);
      const unavailableForTarget =
        pickerTarget.value === "draft"
          ? usedHeroIds.value.has(heroId)
          : globalUsed.value[targetSide].includes(heroId) || usedHeroIds.value.has(heroId);
      return !unavailableForTarget && (!needle || hero.hero_name.toLocaleLowerCase().includes(needle));
    })
    .sort(
      (a, b) =>
        (probabilityByHeroId.value.get(Number(b.hero_id)) || 0) -
          (probabilityByHeroId.value.get(Number(a.hero_id)) || 0) ||
        a.hero_name.localeCompare(b.hero_name)
    );
});

const selectedSeason = computed(() =>
  seasons.value.find((season) => season.league_id === leagueId.value)
);

const boardGroups = computed(() => [
  { key: "blue_bans", title: t("Blue bans"), tone: "blue" },
  { key: "blue_picks", title: t("Blue picks"), tone: "blue" },
  { key: "red_bans", title: t("Red bans"), tone: "red" },
  { key: "red_picks", title: t("Red picks"), tone: "red" },
]);

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function number(value) {
  return Number(value || 0).toLocaleString(language.value);
}

function heroName(heroId) {
  return (
    heroes.value.find((hero) => Number(hero.hero_id) === Number(heroId))
      ?.hero_name || String(heroId)
  );
}

function heroIcon(heroId) {
  return heroes.value.find((hero) => Number(hero.hero_id) === Number(heroId))?.hero_icon || "";
}

async function loadSeasons() {
  seasons.value = (await fetchVisualizationSeasons()) || [];
  selectAvailableLeague(seasons.value);
}

async function loadModel() {
  if (!leagueId.value) return;
  loading.value = true;
  error.value = "";
  result.value = null;
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  globalMode.value = "single";
  seriesGame.value = 1;
  bestOf.value = 5;
  globalUsed.value = { blue: [], red: [] };
  pickerTarget.value = "draft";
  try {
    model.value = await fetchDraftModel(leagueId.value);
    await forecast();
  } catch (err) {
    model.value = null;
    error.value = err.message || "Could not load this season's draft model.";
  } finally {
    loading.value = false;
  }
}

async function forecast() {
  if (!model.value || !currentStep.value || simulating.value) return;
  simulating.value = true;
  error.value = "";
  try {
    result.value = await simulateDraft({
      league_id: leagueId.value,
      bp_order: bpOrder.value,
      ...board.value,
      blue_used_previous_battles: globalUsed.value.blue,
      red_used_previous_battles: globalUsed.value.red,
      rollouts: rollouts.value,
    });
  } catch (err) {
    result.value = null;
    error.value = err.message || "Could not simulate this draft state.";
  } finally {
    simulating.value = false;
  }
}

async function chooseHero(heroId) {
  if (pickerTarget.value !== "draft") {
    const side = pickerTarget.value.replace("global-", "");
    if (globalUsed.value[side].includes(Number(heroId))) return;
    globalUsed.value[side].push(Number(heroId));
    search.value = "";
    await forecast();
    return;
  }
  if (!currentStep.value || usedHeroIds.value.has(Number(heroId))) return;
  const field = `${currentStep.value.side}_${
    currentStep.value.action === "pick" ? "picks" : "bans"
  }`;
  board.value[field].push(Number(heroId));
  history.value.push({ field, heroId: Number(heroId), bpOrder: bpOrder.value });
  bpOrder.value += 1;
  search.value = "";
  await forecast();
}

async function undo() {
  const event = history.value.pop();
  if (!event) return;
  const index = board.value[event.field].lastIndexOf(event.heroId);
  if (index >= 0) board.value[event.field].splice(index, 1);
  bpOrder.value = event.bpOrder;
  await forecast();
}

async function reset() {
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  search.value = "";
  await forecast();
}

async function startGlobalBp() {
  globalMode.value = "match";
  seriesGame.value = 1;
  globalUsed.value = { blue: [], red: [] };
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "draft";
  await forecast();
}

async function customizeGlobalBp() {
  globalMode.value = "custom";
  seriesGame.value = 2;
  globalUsed.value = { blue: [], red: [] };
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "global-blue";
  await forecast();
}

async function clearGlobalBp() {
  globalMode.value = "single";
  seriesGame.value = 1;
  globalUsed.value = { blue: [], red: [] };
  pickerTarget.value = "draft";
  await forecast();
}

async function startNextBattle() {
  if (currentStep.value || seriesGame.value >= bestOf.value) return;
  for (const side of ["blue", "red"]) {
    globalUsed.value[side] = [...new Set([...globalUsed.value[side], ...board.value[`${side}_picks`]])];
  }
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  seriesGame.value += 1;
  await forecast();
}

async function removeGlobalHero(side, heroId) {
  globalUsed.value[side] = globalUsed.value[side].filter((id) => id !== heroId);
  await forecast();
}

function removeHero(field, heroId) {
  const eventIndex = history.value.findLastIndex(
    (event) => event.field === field && event.heroId === heroId
  );
  if (eventIndex === history.value.length - 1) undo();
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadModel();
  } catch (err) {
    error.value = err.message || "Could not load the draft simulator.";
  }
});

watch(leagueId, loadModel);
</script>

<template>
  <main class="simulator-page">
    <header class="simulator-hero">
      <div>
        <p class="simulator-eyebrow">Interactive model</p>
        <h1>BP Draft Simulator</h1>
        <p>
          Build a Blue-versus-Red draft action by action. The model updates its
          forecast after every pick or ban.
        </p>
      </div>
      <label class="simulator-season">
        <span>Competition</span>
        <select v-model="leagueId" :disabled="loading">
          <option v-for="season in seasons" :key="season.league_id" :value="season.league_id">
            {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
          </option>
        </select>
        <small v-if="model">{{ number(model.training_decisions) }} historic draft actions</small>
      </label>
    </header>

    <p v-if="error" class="simulator-message error">{{ error }}</p>
    <p v-else-if="loading" class="simulator-message">Loading draft model…</p>

    <template v-else-if="model">
      <section class="simulator-status">
        <div>
          <span>Next action</span>
          <strong data-i18n-ignore>{{ currentLabel }}</strong>
          <small>{{ selectedSeason?.league_name || leagueId }}</small>
        </div>
        <div class="simulator-actions">
          <label>
            <span>Rollouts</span>
            <select v-model.number="rollouts" @change="forecast">
              <option :value="100">100 · fast</option>
              <option :value="500">500</option>
              <option :value="1000">1,000</option>
              <option :value="2500">2,500</option>
            </select>
          </label>
          <button type="button" :disabled="!history.length || simulating" @click="undo">Undo</button>
          <button type="button" :disabled="simulating" @click="reset">Reset</button>
        </div>
      </section>

      <section class="global-bp-panel">
        <div>
          <p class="simulator-eyebrow">Match format</p>
          <h2>Global BP</h2>
          <p>
            Earlier-game picks are unavailable to the same team in later games,
            while remaining available to its opponent.
          </p>
        </div>
        <div class="global-actions">
          <button type="button" :class="{ active: globalMode === 'single' }" @click="clearGlobalBp">Single game</button>
          <button type="button" :class="{ active: globalMode === 'match' }" @click="startGlobalBp">Start Global BP</button>
          <button type="button" :class="{ active: globalMode === 'custom' }" @click="customizeGlobalBp">Customize used heroes</button>
          <label class="series-format">
            <span>Series</span>
            <select v-model.number="bestOf" :disabled="globalMode === 'single'">
              <option :value="5">BO5</option>
              <option :value="7">BO7</option>
            </select>
          </label>
        </div>
        <div v-if="globalMode !== 'single'" class="global-used">
          <div v-for="side in ['blue', 'red']" :key="side" :class="side">
            <span>{{ side === 'blue' ? 'Blue used earlier' : 'Red used earlier' }}</span>
            <button
              v-for="heroId in globalUsed[side]"
              :key="`${side}-${heroId}`"
              type="button"
              :title="`Remove ${heroName(heroId)}`"
              @click="removeGlobalHero(side, heroId)"
            >
              <img :src="heroIcon(heroId)" :alt="heroName(heroId)" />
            </button>
            <small v-if="!globalUsed[side].length">None selected</small>
          </div>
          <button
            v-if="globalMode === 'match'"
            class="next-battle"
            type="button"
            :disabled="Boolean(currentStep) || seriesGame >= bestOf"
            @click="startNextBattle"
          >
            {{
              seriesGame >= bestOf
                ? `BO${bestOf} complete`
                : currentStep
                  ? 'Finish this draft to continue'
                  : `Start game ${seriesGame + 1}`
            }}
          </button>
          <small v-else>BO{{ bestOf }} · Game {{ seriesGame }} · {{ globalMode === 'custom' ? 'custom prior usage' : 'tracked from earlier games' }}</small>
        </div>
      </section>

      <section class="simulator-layout">
        <div class="draft-board">
          <section
            v-for="group in boardGroups"
            :key="group.key"
            class="draft-group"
            :class="group.tone"
          >
            <p data-i18n-ignore>{{ group.title }}</p>
            <div class="draft-slots">
              <button
                v-for="heroId in board[group.key]"
                :key="`${group.key}-${heroId}`"
                type="button"
                :title="history.at(-1)?.heroId === heroId ? 'Remove latest action' : ''"
                :disabled="history.at(-1)?.heroId !== heroId"
                @click="removeHero(group.key, heroId)"
              >
                <img v-if="heroIcon(heroId)" :src="heroIcon(heroId)" :alt="heroName(heroId)" />
                <span v-else>{{ heroName(heroId).slice(0, 1) }}</span>
              </button>
              <span v-for="slot in Math.max(0, 5 - board[group.key].length)" :key="slot">—</span>
            </div>
          </section>
        </div>

        <aside class="forecast-panel">
          <div class="forecast-heading">
            <div>
              <p class="simulator-eyebrow">Model forecast</p>
              <h2>{{ result?.next_step?.side === "blue" ? "Blue" : "Red" }} {{ result?.next_step?.action }}</h2>
            </div>
            <span v-if="simulating">Updating…</span>
          </div>
          <div class="probability-list">
            <div v-for="row in result?.next_action_probabilities?.slice(0, 10)" :key="row.hero_id">
              <img :src="heroIcon(row.hero_id)" :alt="row.hero_name" />
              <span class="probability-track"><i :style="{ width: percent(row.probability) }"></i></span>
              <em>{{ percent(row.probability) }}</em>
            </div>
          </div>
          <div v-if="result?.simulation?.banned_by_end?.length" class="end-ban-list">
            <p>Most likely to be banned before draft end</p>
            <span v-for="row in result.simulation.banned_by_end.slice(0, 3)" :key="row.hero_id">
              <img :src="heroIcon(row.hero_id)" :alt="row.hero_name" />
              {{ percent(row.probability) }}
            </span>
          </div>
        </aside>
      </section>

      <section class="hero-picker">
        <div class="picker-heading">
          <div>
            <p class="simulator-eyebrow">{{ pickerTarget === 'draft' ? 'Add the next action' : 'Global BP setup' }}</p>
            <h2 data-i18n-ignore>{{ pickerTitle }}</h2>
          </div>
          <input v-model="search" type="search" placeholder="Find a hero…" :disabled="pickerTarget === 'draft' && !currentStep" />
        </div>
        <div v-if="globalMode !== 'single'" class="picker-targets">
          <button type="button" :class="{ active: pickerTarget === 'draft' }" @click="pickerTarget = 'draft'">Current draft</button>
          <button type="button" :class="{ active: pickerTarget === 'global-blue' }" @click="pickerTarget = 'global-blue'">Blue earlier games</button>
          <button type="button" :class="{ active: pickerTarget === 'global-red' }" @click="pickerTarget = 'global-red'">Red earlier games</button>
        </div>
        <div class="hero-options">
          <button
            v-for="hero in availableHeroes"
            :key="hero.hero_id"
            type="button"
            :disabled="(pickerTarget === 'draft' && !currentStep) || simulating"
            :title="`${hero.hero_name} · ${percent(probabilityByHeroId.get(Number(hero.hero_id)) || 0)}`"
            @click="chooseHero(hero.hero_id)"
          >
            <img v-if="hero.hero_icon" :src="hero.hero_icon" :alt="hero.hero_name" />
            <span v-else>{{ hero.hero_name.slice(0, 1) }}</span>
            <small>{{ percent(probabilityByHeroId.get(Number(hero.hero_id)) || 0) }}</small>
          </button>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.simulator-page { width: min(1440px, calc(100% - 2rem)); margin: 0 auto; padding: 2.25rem 0 5rem; }
.simulator-hero, .simulator-status, .simulator-layout { display: flex; gap: 1.5rem; justify-content: space-between; }
.simulator-hero { align-items: flex-end; }
.simulator-eyebrow { margin: 0 0 .45rem; color: var(--accent-deep); font-size: .66rem; letter-spacing: .13em; text-transform: uppercase; }
.simulator-hero h1, .picker-heading h2, .forecast-heading h2 { margin: 0; font-family: var(--display); letter-spacing: -.045em; }
.simulator-hero h1 { font-size: clamp(2.4rem, 5vw, 4rem); line-height: .95; }
.simulator-hero > div > p:last-child { max-width: 40rem; margin: .8rem 0 0; color: var(--ink-soft); font-size: .8rem; }
.simulator-season { display: grid; min-width: 310px; gap: .4rem; }
.simulator-season span, .simulator-actions label span { color: var(--ink-soft); font-size: .64rem; letter-spacing: .1em; text-transform: uppercase; }
.simulator-season select, .simulator-actions select, .picker-heading input { min-height: 42px; padding: .55rem .7rem; border: 1px solid var(--line); background: rgba(255,255,255,.85); color: var(--ink); font: inherit; }
.simulator-season small { color: var(--ink-soft); font-size: .66rem; }
.simulator-message { margin: 1.5rem 0; color: var(--ink-soft); }.simulator-message.error { color: var(--warn); }
.simulator-status { align-items: center; margin-top: 1.5rem; padding: 1rem 1.15rem; border: 1px solid var(--line); background: rgba(255,255,255,.72); }
.simulator-status > div:first-child span, .simulator-status small { display: block; color: var(--ink-soft); font-size: .65rem; letter-spacing: .08em; text-transform: uppercase; }
.simulator-status strong { display: block; margin: .18rem 0; font: 700 1.25rem var(--display); }
.simulator-actions { display: flex; align-items: end; gap: .5rem; }.simulator-actions label { display: grid; gap: .3rem; }
.simulator-actions button, .hero-options button, .draft-slots button { border: 1px solid var(--line); background: rgba(255,255,255,.86); color: var(--ink); font: inherit; cursor: pointer; }
.simulator-actions button { min-height: 42px; padding: .55rem .75rem; }.simulator-actions button:disabled, .hero-options button:disabled, .draft-slots button:disabled { cursor: default; opacity: .45; }
.global-bp-panel { display:grid; grid-template-columns:minmax(14rem, 1fr) auto; gap:1rem 1.5rem; margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.72); }.global-bp-panel h2 { margin:0; font:700 1.35rem var(--display); letter-spacing:-.04em; }.global-bp-panel > div:first-child > p:last-child { max-width:38rem; margin:.4rem 0 0; color:var(--ink-soft); font-size:.72rem; }.global-actions, .picker-targets { display:flex; flex-wrap:wrap; gap:.45rem; align-items:center; }.global-actions button, .picker-targets button, .next-battle { min-height:36px; padding:.45rem .6rem; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink-soft); font:inherit; font-size:.67rem; cursor:pointer; }.global-actions button.active, .picker-targets button.active { border-color:var(--accent-deep); background:var(--ink); color:#fff; }.series-format { display:grid; gap:.12rem; color:var(--ink-soft); font-size:.58rem; letter-spacing:.08em; text-transform:uppercase; }.series-format select { min-height:30px; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink); font:inherit; font-size:.67rem; }.global-used { display:grid; grid-template-columns:1fr 1fr auto; gap:.8rem; grid-column:1 / -1; padding-top:.8rem; border-top:1px solid var(--line); }.global-used > div { display:flex; align-items:center; flex-wrap:wrap; gap:.35rem; }.global-used > div > span { width:100%; color:var(--ink-soft); font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; }.global-used > div button { width:2rem; height:2rem; padding:0; border:1px solid var(--line); background:#fff; cursor:pointer; }.global-used img { width:100%; height:100%; object-fit:cover; }.global-used small { align-self:center; color:var(--ink-soft); font-size:.66rem; }.global-used > .next-battle { align-self:end; min-height:36px; width:auto; height:auto; padding:.45rem .6rem; border-color:var(--accent-deep); background:var(--accent); color:#fff; white-space:nowrap; }.global-used > .next-battle:disabled { cursor:not-allowed; opacity:.5; }
.simulator-layout { align-items: stretch; margin-top: .75rem; }.draft-board { display: grid; flex: 1; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .75rem; }
.draft-group, .forecast-panel, .hero-picker { border: 1px solid var(--line); background: rgba(255,255,255,.76); }.draft-group { min-height: 160px; padding: 1rem; }.draft-group > p { margin: 0 0 .8rem; font-size: .67rem; letter-spacing: .1em; text-transform: uppercase; }.draft-group.blue > p { color: #286999; }.draft-group.red > p { color: #a84b4b; }
.draft-slots { display: flex; flex-wrap: wrap; gap: .45rem; }.draft-slots button, .draft-slots span { display:grid; place-items:center; width:4rem; height:4rem; padding:0; font-size:.7rem; text-align:left; }.draft-slots button img { width:100%; height:100%; object-fit:cover; }.draft-slots span { border: 1px dashed var(--line); color: var(--ink-soft); }
.forecast-panel { width: min(100%, 390px); padding: 1rem; }.forecast-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }.forecast-heading h2 { font-size: 1.5rem; }.forecast-heading > span { color: var(--ink-soft); font-size: .68rem; }
.probability-list { margin-top: 1rem; }.probability-list > div { display: grid; grid-template-columns:2rem minmax(4rem,1.8fr) 3rem; gap: .55rem; align-items: center; margin-top: .55rem; font-size: .7rem; }.probability-list img { width:2rem; height:2rem; object-fit:cover; }.probability-list em { color: var(--ink-soft); font-style: normal; text-align: right; }.probability-track { height: .42rem; overflow: hidden; background: rgba(16,42,46,.1); }.probability-track i { display:block; height:100%; background: var(--accent); }
.end-ban-list { margin-top: 1.2rem; padding-top: .85rem; border-top: 1px solid var(--line); }.end-ban-list p { margin:0 0 .5rem; color: var(--ink-soft); font-size:.65rem; }.end-ban-list span { display:inline-flex; align-items:center; gap:.25rem; margin:.25rem .6rem 0 0; font-size:.7rem; }.end-ban-list img { width:1.6rem; height:1.6rem; object-fit:cover; }
.hero-picker { margin-top: .75rem; padding: 1rem; }.picker-heading { display:flex; align-items:end; justify-content:space-between; gap:1rem; }.picker-heading h2 { font-size:1.4rem; }.picker-heading input { width:min(100%, 260px); }.picker-targets { margin-top:.85rem; }.hero-options { display:grid; grid-template-columns:repeat(auto-fill, minmax(3.6rem, 1fr)); gap:.45rem; margin-top:1rem; max-height:360px; overflow:auto; }.hero-options button { position:relative; display:grid; place-items:center; aspect-ratio:1; padding:0; overflow:hidden; }.hero-options button img { width:100%; height:100%; object-fit:cover; }.hero-options button small { position:absolute; right:0; bottom:0; padding:.14rem .2rem; background:rgba(16,42,46,.84); color:#fff; font-size:.56rem; }.hero-options button:hover:not(:disabled), .draft-slots button:not(:disabled):hover { border-color: var(--accent); color: var(--accent-deep); }
@media (max-width: 860px) { .simulator-hero, .simulator-status, .simulator-layout { flex-direction:column; align-items:stretch; }.simulator-season, .forecast-panel { width:100%; }.simulator-actions { justify-content:space-between; }.draft-board { grid-template-columns:1fr; }.global-bp-panel { grid-template-columns:1fr; }.global-used { grid-template-columns:1fr; }.next-battle { justify-self:start; } }
@media (max-width: 620px) { .simulator-page { width:calc(100% - 1rem); padding-top:1.25rem; }.simulator-status { gap:1rem; }.simulator-actions { flex-wrap:wrap; }.picker-heading { align-items:stretch; flex-direction:column; }.picker-heading input { width:100%; }.hero-options { grid-template-columns:repeat(auto-fill, minmax(3.25rem, 1fr)); } }
</style>
