<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { simulateDraft } from "./api";
import { t } from "./i18n";

const props = defineProps({
  base: Object,
  heroes: Array,
  draftSequence: Array,
  heroAsset: Function,
  simulationContext: Object,
  initialForecast: Object,
});
const emit = defineEmits(["close", "apply", "versions-change", "pin-snapshot"]);
function scenarioFromBase(id) {
  return {
    id,
    board: {
      blue_picks: [...props.base.board.blue_picks],
      red_picks: [...props.base.board.red_picks],
      blue_bans: [...props.base.board.blue_bans],
      red_bans: [...props.base.board.red_bans],
    },
    bpOrder: Number(props.base.bpOrder),
    history: props.base.history.map((entry) => ({ ...entry })),
    startHistoryLength: Number(props.base.startHistoryLength),
    blueUsed: [...props.base.blueUsed],
    redUsed: [...props.base.redUsed],
    search: "",
    pinnedSignature: "",
    forecast: null,
    probabilityByHeroId: new Map(),
    forecastLoading: false,
    forecastError: "",
    forecastRequest: 0,
  };
}
const scenarios = ref([scenarioFromBase(1)]);
let nextId = 2;
const forecastCache = new Map();
const forecastTimers = new Map();
const columns = computed(() => `columns-${Math.min(4, scenarios.value.length)}`);
const stepFor = (scenario) => props.draftSequence.find((step) => Number(step.bp_order) === Number(scenario.bpOrder));
const actionLabel = (step) => !step ? t("Draft complete") : `${t(step.side === "blue" ? "Blue" : "Red")} · ${t(step.action === "pick" ? "Pick" : "Ban")}`;
function used(scenario) { return new Set([...scenario.board.blue_picks, ...scenario.board.red_picks, ...scenario.board.blue_bans, ...scenario.board.red_bans, ...scenario.blueUsed, ...scenario.redUsed].map(Number)); }
function probabilityFor(scenario, heroId) {
  return Number(scenario.probabilityByHeroId.get(Number(heroId)) || 0);
}
function applyForecast(scenario, response) {
  scenario.forecast = response;
  scenario.probabilityByHeroId = new Map(
    (response?.next_action_probabilities || []).map((row) => [Number(row.hero_id), Number(row.probability)])
  );
}
function availableHeroes(scenario) {
  const needle = scenario.search.trim().toLocaleLowerCase();
  const unavailable = used(scenario);
  const source = (scenario.forecast?.next_action_probabilities || [])
    .map((row) => props.heroes.find((hero) => Number(hero.hero_id) === Number(row.hero_id)))
    .filter(Boolean);
  return source
    .filter((hero) => !unavailable.has(Number(hero.hero_id)) && (!needle || hero.hero_name.toLocaleLowerCase().includes(needle)))
    .sort((left, right) => probabilityFor(scenario, right.hero_id) - probabilityFor(scenario, left.hero_id) || left.hero_name.localeCompare(right.hero_name));
}
function forecastPayload(scenario) {
  if (!props.simulationContext || !stepFor(scenario)) return null;
  const { leagueId, blueTeam, redTeam, modelType } = props.simulationContext;
  return {
    league_id: leagueId,
    model_type: modelType,
    blue_team_id: String(blueTeam.team_id),
    blue_team_name: blueTeam.team_name,
    red_team_id: String(redTeam.team_id),
    red_team_name: redTeam.team_name,
    bp_order: scenario.bpOrder,
    blue_picks: [...scenario.board.blue_picks],
    red_picks: [...scenario.board.red_picks],
    blue_bans: [...scenario.board.blue_bans],
    red_bans: [...scenario.board.red_bans],
    blue_used_previous_battles: [...scenario.blueUsed],
    red_used_previous_battles: [...scenario.redUsed],
  };
}
function forecastKey(payload) {
  return JSON.stringify(payload);
}
async function loadForecast(scenario) {
  const payload = forecastPayload(scenario);
  if (!payload) {
    scenario.forecast = null;
    scenario.probabilityByHeroId = new Map();
    scenario.forecastLoading = false;
    return;
  }
  const request = ++scenario.forecastRequest;
  const key = forecastKey(payload);
  scenario.forecastLoading = true;
  scenario.forecastError = "";
  const cached = forecastCache.get(key);
  if (cached?.data) {
    applyForecast(scenario, cached.data);
    scenario.forecastLoading = false;
    return;
  }
  let pending = cached?.pending;
  if (!pending) {
    pending = simulateDraft(payload).then((response) => {
      forecastCache.set(key, { data: response });
      return response;
    }).catch((error) => {
      forecastCache.delete(key);
      throw error;
    });
    forecastCache.set(key, { pending });
  }
  try {
    const response = await pending;
    if (request === scenario.forecastRequest) applyForecast(scenario, response);
  } catch (error) {
    if (request === scenario.forecastRequest) {
      scenario.forecast = null;
      scenario.probabilityByHeroId = new Map();
      scenario.forecastError = error.message || t("Could not update hero probabilities.");
    }
  } finally {
    if (request === scenario.forecastRequest) scenario.forecastLoading = false;
  }
}
function queueForecast(scenario, immediate = false) {
  const existing = forecastTimers.get(scenario.id);
  if (existing) window.clearTimeout(existing);
  scenario.forecast = null;
  scenario.probabilityByHeroId = new Map();
  scenario.forecastError = "";
  scenario.forecastLoading = Boolean(stepFor(scenario));
  if (immediate) {
    loadForecast(scenario);
    return;
  }
  forecastTimers.set(scenario.id, window.setTimeout(() => {
    forecastTimers.delete(scenario.id);
    loadForecast(scenario);
  }, 120));
}
function choose(scenario, heroId) {
  const step = stepFor(scenario);
  if (!step || used(scenario).has(Number(heroId)) || scenario.forecastLoading) return;
  const legalIds = new Set(
    (scenario.forecast?.next_action_probabilities || []).map((row) => Number(row.hero_id))
  );
  if (!legalIds.has(Number(heroId))) return;
  const field = `${step.side}_${step.action === "pick" ? "picks" : "bans"}`;
  scenario.board[field].push(Number(heroId));
  scenario.history.push({ field, heroId: Number(heroId), bpOrder: scenario.bpOrder });
  scenario.bpOrder += 1;
  queueForecast(scenario);
}
function scenarioSignature(scenario) {
  return JSON.stringify(
    scenario.history.slice(scenario.startHistoryLength).map((entry) => [entry.field, entry.heroId, entry.bpOrder])
  );
}
function canPin(scenario) {
  return scenario.history.length > scenario.startHistoryLength;
}
function isPinned(scenario) {
  return canPin(scenario) && scenario.pinnedSignature === scenarioSignature(scenario);
}
function pinSnapshot(scenario, selectedBranchId = null) {
  if (!canPin(scenario)) return;
  emit("pin-snapshot", {
    sessionId: props.base.versionSessionId,
    selectedBranchId,
    branch: branchSnapshot(scenario),
  });
  scenario.pinnedSignature = scenarioSignature(scenario);
}
function branchSnapshot(scenario) {
  return {
    id: scenario.id,
    actions: scenario.history
      .slice(scenario.startHistoryLength)
      .map((entry) => ({ ...entry })),
    state: {
      board: {
        blue_picks: [...scenario.board.blue_picks],
        red_picks: [...scenario.board.red_picks],
        blue_bans: [...scenario.board.blue_bans],
        red_bans: [...scenario.board.red_bans],
      },
      bpOrder: scenario.bpOrder,
      history: scenario.history.map((entry) => ({ ...entry })),
      blueUsed: [...scenario.blueUsed],
      redUsed: [...scenario.redUsed],
    },
  };
}

function undo(scenario) { if (scenario.history.length <= scenario.startHistoryLength) return; const last = scenario.history.pop(); scenario.board[last.field].pop(); scenario.bpOrder = last.bpOrder; queueForecast(scenario, true); }
function add() { if (scenarios.value.length < 4) { const scenario = scenarioFromBase(nextId++); scenarios.value.push(scenario); queueForecast(scenario, true); } }
function remove(id) { if (scenarios.value.length > 1) { const timer = forecastTimers.get(id); if (timer) window.clearTimeout(timer); forecastTimers.delete(id); scenarios.value = scenarios.value.filter((scenario) => scenario.id !== id); } }
function applyScenario(scenario) {
  pinSnapshot(scenario, scenario.id);
  emit("apply", scenario);
}
onMounted(() => {
  const scenario = scenarios.value[0];
  const payload = forecastPayload(scenario);
  if (payload && props.initialForecast) {
    forecastCache.set(forecastKey(payload), { data: props.initialForecast });
    applyForecast(scenario, props.initialForecast);
  } else {
    queueForecast(scenario, true);
  }
});
onBeforeUnmount(() => {
  forecastTimers.forEach((timer) => window.clearTimeout(timer));
  scenarios.value.forEach((scenario) => { scenario.forecastRequest += 1; });
});
</script>

<template>
  <div class="whatif-backdrop" @click.self="emit('close')">
    <section class="whatif-modal" role="dialog" aria-modal="true" :aria-label="t('What-if workspace')">
      <header class="modal-header"><div><h2>{{ t('What-if workspace') }}</h2><p>{{ t('Each board starts from this exact draft snapshot. Continue a different BP line in every scenario.') }}</p></div><div><span>{{ scenarios.length }}/4</span><button type="button" :disabled="scenarios.length >= 4" @click="add">{{ t('Add another what-if') }}</button><button type="button" aria-label="Close" @click="emit('close')">×</button></div></header>
      <div class="scenario-grid" :class="columns">
        <article v-for="(scenario, index) in scenarios" :key="scenario.id">
          <header><strong>{{ t('What-if') }} {{ index + 1 }}</strong><button v-if="scenarios.length > 1" type="button" :aria-label="t('Remove scenario')" @click="remove(scenario.id)">×</button></header>
          <small>{{ actionLabel(stepFor(scenario)) }}</small>
          <div class="mini-board">
            <section v-for="group in [['blue_bans', 'Blue bans'], ['blue_picks', 'Blue picks'], ['red_bans', 'Red bans'], ['red_picks', 'Red picks']]" :key="group[0]" :class="group[0].startsWith('blue') ? 'blue' : 'red'"><span>{{ t(group[1]) }}</span><div><template v-for="slot in 5" :key="slot"><img v-if="scenario.board[group[0]][slot - 1]" :src="heroAsset(scenario.board[group[0]][slot - 1])" :alt="String(scenario.board[group[0]][slot - 1])" /><i v-else>—</i></template></div></section>
          </div>
          <div class="card-actions">
            <button type="button" :disabled="scenario.history.length <= scenario.startHistoryLength" @click="undo(scenario)">{{ t('Undo') }}</button>
            <button type="button" class="pin" :class="{ added: isPinned(scenario) }" :disabled="!canPin(scenario)" @click="pinSnapshot(scenario)">{{ isPinned(scenario) ? t('Snapshot added to tree') : t('Add snapshot to tree') }}</button>
            <button type="button" class="apply" @click="applyScenario(scenario)">{{ t('Apply to practice board') }}</button>
          </div>
          <p v-if="stepFor(scenario)">{{ t('Choose a hero for') }} {{ actionLabel(stepFor(scenario)) }}</p><p v-else>{{ t('Draft complete') }}</p>
          <template v-if="stepFor(scenario)">
            <label class="hero-search"><span>{{ t('Search heroes') }}</span><input v-model="scenario.search" type="search" :placeholder="t('Find a hero…')" /><em aria-live="polite">{{ scenario.forecastLoading ? t('Updating probabilities…') : scenario.forecastError }}</em></label>
            <div class="hero-grid"><button v-for="hero in availableHeroes(scenario)" :key="hero.hero_id" type="button" :title="`${hero.hero_name} · ${(probabilityFor(scenario, hero.hero_id) * 100).toFixed(1)}%`" @click="choose(scenario, hero.hero_id)"><img v-if="heroAsset(hero.hero_id)" :src="heroAsset(hero.hero_id)" :alt="hero.hero_name" /><span v-else>{{ hero.hero_name.slice(0, 1) }}</span><small>{{ scenario.forecastLoading ? '…' : `${(probabilityFor(scenario, hero.hero_id) * 100).toFixed(1)}%` }}</small></button></div>
          </template>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.whatif-backdrop{position:fixed;z-index:70;inset:0;display:grid;place-items:center;padding:1rem;background:rgba(12,27,29,.58);backdrop-filter:blur(3px)}.whatif-modal{width:min(1440px,100%);max-height:90vh;overflow:auto;background:#f7f7f2;box-shadow:0 22px 55px rgba(0,0,0,.28)}button{min-height:30px;border:1px solid var(--line);background:#fff;color:var(--ink);font:700 .58rem var(--mono);cursor:pointer}button:disabled{opacity:.42;cursor:not-allowed}.modal-header{display:flex;justify-content:space-between;gap:1rem;padding:1.15rem 1.3rem;border-bottom:1px solid var(--line);background:#fff}.modal-header h2{margin:0;font:700 1.45rem var(--display);letter-spacing:-.035em}.modal-header p{max-width:62ch;margin:.35rem 0 0;color:var(--ink-soft);font-size:.7rem}.modal-header>div:last-child{display:flex;align-items:center;gap:.45rem}.modal-header span{font:700 .62rem var(--mono);color:var(--ink-soft)}.scenario-grid{display:grid;gap:1px;background:var(--line)}.columns-1{grid-template-columns:1fr}.columns-2,.columns-3,.columns-4{grid-template-columns:repeat(2,minmax(0,1fr))}.scenario-grid article{min-width:0;padding:.85rem;background:#f7f7f2}.scenario-grid article>header{display:flex;justify-content:space-between}.scenario-grid strong{font:700 .75rem var(--mono)}.scenario-grid small,.scenario-grid p{color:var(--ink-soft);font-size:.58rem}.mini-board{display:grid;grid-template-columns:1fr 1fr;gap:.45rem;margin:.65rem 0}.mini-board section{padding:.45rem;border:1px solid var(--line);background:#fff}.mini-board section.blue{border-top:2px solid #247aa5}.mini-board section.red{border-top:2px solid #b74942}.mini-board section>span{display:block;margin-bottom:.3rem;font:700 .52rem var(--mono);color:var(--ink-soft)}.mini-board section>div{display:flex;gap:.25rem}.mini-board img,.mini-board i{display:grid;width:clamp(24px,3vw,38px);aspect-ratio:1;place-items:center;border:1px solid var(--line);object-fit:cover;font-style:normal;color:var(--ink-soft)}.card-actions{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.4rem}.pin.added{border-color:#28745d;background:#e8f6ef;color:#28745d}.apply{background:var(--ink);color:#fff}.hero-search{display:grid;grid-template-columns:auto minmax(8rem,1fr);align-items:center;gap:.35rem .5rem;margin:.7rem 0 .45rem;color:var(--ink-soft);font:700 .54rem var(--mono)}.hero-search input{min-width:0;border:1px solid var(--line);background:#fff;color:var(--ink);font:600 .62rem var(--mono);padding:.38rem .45rem}.hero-search em{grid-column:1/-1;min-height:.7rem;color:var(--warn);font:500 .5rem var(--mono)}.hero-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(38px,1fr));gap:.25rem;max-height:175px;overflow:auto}.hero-grid button{position:relative;min-height:0;aspect-ratio:1;padding:0;overflow:hidden}.hero-grid img{width:100%;height:100%;object-fit:cover}.hero-grid span{display:grid;height:100%;place-items:center}.hero-grid small{position:absolute;right:0;bottom:0;padding:.08rem .14rem;background:rgba(12,27,29,.86);color:#fff;font:.45rem var(--mono);font-variant-numeric:tabular-nums}@media(max-width:760px){.whatif-backdrop{align-items:start;padding:0}.whatif-modal{min-height:100vh;max-height:100vh}.modal-header{position:sticky;top:0;z-index:1;padding:.85rem}.modal-header>div:last-child{align-items:flex-start;flex-wrap:wrap;justify-content:end}.columns-2,.columns-3,.columns-4{grid-template-columns:1fr}.mini-board img,.mini-board i{width:clamp(25px,8vw,38px)}}
</style>
