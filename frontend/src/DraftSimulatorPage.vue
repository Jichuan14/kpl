<script setup>
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchDraftModel,
  fetchSelectionCommentary,
  fetchSeasonTeams,
  fetchVisualizationSeasons,
  simulateDraft,
} from "./api";
import DraftCoachPanel from "./DraftCoachPanel.vue";
import TeamCombobox from "./TeamCombobox.vue";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { heroAsset } from "./heroAssets";
import { language, t } from "./i18n";
import { finishStartupLoading } from "./startupLoader";

const leagueId = selectedLeagueId;
const seasons = ref([]);
const model = ref(null);
const result = ref(null);
const commentary = ref(null);
const commentaryLoading = ref(false);
const commentaryEnabled = ref(false);
let commentaryRequestNumber = 0;
const loading = ref(false);
const simulating = ref(false);
const error = ref("");
const search = ref("");
const rollouts = ref(100);
const modelType = ref("learnable");
const bpOrder = ref(1);
const board = ref(emptyBoard());
const history = ref([]);
const globalMode = ref("single");
const seriesGame = ref(1);
const bestOf = ref(5);
const TEAM_A = "team-a";
const TEAM_B = "team-b";
const globalUsed = ref({ [TEAM_A]: [], [TEAM_B]: [] });
const seasonTeams = ref([]);
const selectedTeamIds = ref({ [TEAM_A]: "", [TEAM_B]: "" });
const teamsBySide = ref({ blue: TEAM_A, red: TEAM_B });
const seriesWins = ref({ [TEAM_A]: 0, [TEAM_B]: 0 });
const winnerSide = ref(null);
const nextBlueTeam = ref(null);
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
const availableModels = computed(() => model.value?.available_models || []);
const selectedModel = computed(() =>
  availableModels.value.find((candidate) => candidate.id === modelType.value)
);

const pickerTitle = computed(() => {
  if (pickerTarget.value === "global-blue") return addEarlierHeroLabel(teamsBySide.value.blue);
  if (pickerTarget.value === "global-red") return addEarlierHeroLabel(teamsBySide.value.red);
  return currentLabel.value;
});

const losingSide = computed(() =>
  winnerSide.value === "blue" ? "red" : winnerSide.value === "red" ? "blue" : null
);

const losingTeam = computed(() =>
  losingSide.value ? teamsBySide.value[losingSide.value] : null
);

const winsNeeded = computed(() => Math.ceil(bestOf.value / 2));

const seriesWinner = computed(() =>
  [TEAM_A, TEAM_B].find((team) => seriesWins.value[team] >= winsNeeded.value) || null
);

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
  const targetTeam = teamsBySide.value[targetSide];
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
          : globalUsed.value[targetTeam].includes(heroId) || usedHeroIds.value.has(heroId);
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

const teamsReady = computed(
  () =>
    Boolean(selectedTeam(TEAM_A) && selectedTeam(TEAM_B)) &&
    selectedTeamIds.value[TEAM_A] !== selectedTeamIds.value[TEAM_B]
);

const boardGroups = computed(() => [
  { key: "blue_bans", title: `${t("Blue bans")} · ${teamName(teamsBySide.value.blue)}`, tone: "blue" },
  { key: "blue_picks", title: `${t("Blue picks")} · ${teamName(teamsBySide.value.blue)}`, tone: "blue" },
  { key: "red_bans", title: `${t("Red bans")} · ${teamName(teamsBySide.value.red)}`, tone: "red" },
  { key: "red_picks", title: `${t("Red picks")} · ${teamName(teamsBySide.value.red)}`, tone: "red" },
]);

const coachDraftState = computed(() => {
  if (!currentStep.value || !teamsReady.value) return null;
  const blue = selectedTeam(teamsBySide.value.blue);
  const red = selectedTeam(teamsBySide.value.red);
  return {
    model_type: modelType.value,
    blue_team_id: String(blue.team_id),
    blue_team_name: blue.team_name,
    red_team_id: String(red.team_id),
    red_team_name: red.team_name,
    bp_order: bpOrder.value,
    blue_picks: [...board.value.blue_picks],
    red_picks: [...board.value.red_picks],
    blue_bans: [...board.value.blue_bans],
    red_bans: [...board.value.red_bans],
    blue_used_previous_battles: [
      ...globalUsed.value[teamsBySide.value.blue],
    ],
    red_used_previous_battles: [
      ...globalUsed.value[teamsBySide.value.red],
    ],
  };
});

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
  return heroAsset(heroId);
}

function teamName(team) {
  return selectedTeam(team)?.team_name || t(team === TEAM_A ? "Blue Team" : "Red Team");
}

function selectedTeam(team) {
  const teamId = selectedTeamIds.value[team];
  return seasonTeams.value.find(
    (candidate) => String(candidate.team_id) === String(teamId)
  );
}

function sideLabel(side) {
  return t(side === "blue" ? "Blue" : "Red");
}

function sideUsedLabel(side) {
  return `${sideLabel(side)} · ${teamName(teamsBySide.value[side])} ${t("used earlier")}`;
}

function addEarlierHeroLabel(team) {
  return t("Add {team}'s earlier-game hero").replace("{team}", teamName(team));
}

function earlierGamesLabel(team) {
  return t("{team} earlier games").replace("{team}", teamName(team));
}

function gameWinnerLabel(game) {
  return t("Game {game} winner").replace("{game}", game);
}

function loserColorChoiceLabel(team) {
  return t("{team} chooses next color").replace("{team}", teamName(team));
}

function startGameLabel(game) {
  return t("Start game {game}").replace("{game}", game);
}

function seriesWinnerLabel() {
  return `${teamName(seriesWinner.value)} wins BO${bestOf.value}`;
}

function seriesStatusLabel() {
  return `BO${bestOf.value} · ${t("Game")} ${seriesGame.value} · ${t(
    globalMode.value === "custom" ? "custom prior usage" : "tracked from earlier games"
  )}`;
}

function forecastLabel() {
  const step = result.value?.next_step;
  return step ? `${sideLabel(step.side)} ${t(step.action)}` : "";
}

function resetSeriesTeams() {
  globalUsed.value = { [TEAM_A]: [], [TEAM_B]: [] };
  teamsBySide.value = { blue: TEAM_A, red: TEAM_B };
  seriesWins.value = { [TEAM_A]: 0, [TEAM_B]: 0 };
  winnerSide.value = null;
  nextBlueTeam.value = null;
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
  commentary.value = null;
  model.value = null;
  seasonTeams.value = [];
  selectedTeamIds.value = { [TEAM_A]: "", [TEAM_B]: "" };
  modelType.value = "learnable";
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  globalMode.value = "single";
  seriesGame.value = 1;
  bestOf.value = 5;
  resetSeriesTeams();
  pickerTarget.value = "draft";
  try {
    const [draftModel, teams] = await Promise.all([
      fetchDraftModel(leagueId.value),
      fetchSeasonTeams(leagueId.value),
    ]);
    model.value = draftModel;
    seasonTeams.value = teams;
    modelType.value = draftModel.available_models?.some(
      (candidate) => candidate.id === "learnable" && candidate.available
    )
      ? "learnable"
      : "stats";
    const wolves =
      teams.find((team) => String(team.team_id) === "10001") ||
      teams.find((team) => String(team.team_name).includes("狼队"));
    const ag =
      teams.find((team) => String(team.team_id) === "10027") ||
      teams.find((team) => String(team.team_name).includes("AG超玩会"));
    if (wolves && ag && String(wolves.team_id) !== String(ag.team_id)) {
      selectedTeamIds.value = {
        [TEAM_A]: String(wolves.team_id),
        [TEAM_B]: String(ag.team_id),
      };
    }
  } catch (err) {
    model.value = null;
    error.value = err.message || "Could not load this season's draft model.";
  } finally {
    loading.value = false;
  }
}

async function forecast() {
  if (!teamsReady.value) {
    result.value = null;
    return;
  }
  if (!model.value || !currentStep.value || simulating.value) return;
  const blue = selectedTeam(teamsBySide.value.blue);
  const red = selectedTeam(teamsBySide.value.red);
  simulating.value = true;
  error.value = "";
  try {
    result.value = await simulateDraft({
      league_id: leagueId.value,
      model_type: modelType.value,
      blue_team_id: String(blue.team_id),
      blue_team_name: blue.team_name,
      red_team_id: String(red.team_id),
      red_team_name: red.team_name,
      bp_order: bpOrder.value,
      ...board.value,
      blue_used_previous_battles: globalUsed.value[teamsBySide.value.blue],
      red_used_previous_battles: globalUsed.value[teamsBySide.value.red],
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
  if (!teamsReady.value) return;
  if (pickerTarget.value !== "draft") {
    const side = pickerTarget.value.replace("global-", "");
    const team = teamsBySide.value[side];
    if (globalUsed.value[team].includes(Number(heroId))) return;
    globalUsed.value[team].push(Number(heroId));
    search.value = "";
    await forecast();
    return;
  }
  if (!currentStep.value || usedHeroIds.value.has(Number(heroId))) return;
  const preSelectionState = coachDraftState.value;
  if (commentaryEnabled.value && preSelectionState) {
    const requestNumber = ++commentaryRequestNumber;
    commentaryLoading.value = true;
    fetchSelectionCommentary({
      league_id: leagueId.value,
      ...preSelectionState,
      action: currentStep.value.action,
      side: currentStep.value.side,
      selected_hero_id: Number(heroId),
      rollouts: 100,
    }).then((payload) => {
      if (commentaryEnabled.value && requestNumber === commentaryRequestNumber) {
        commentary.value = payload;
      }
    }).catch(() => {
      if (requestNumber === commentaryRequestNumber) commentary.value = null;
    }).finally(() => {
      if (requestNumber === commentaryRequestNumber) commentaryLoading.value = false;
    });
  }
  const field = `${currentStep.value.side}_${
    currentStep.value.action === "pick" ? "picks" : "bans"
  }`;
  board.value[field].push(Number(heroId));
  history.value.push({ field, heroId: Number(heroId), bpOrder: bpOrder.value });
  bpOrder.value += 1;
  search.value = "";
  await forecast();
}

watch(commentaryEnabled, (enabled) => {
  if (enabled) return;
  commentaryRequestNumber += 1;
  commentaryLoading.value = false;
  commentary.value = null;
});

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
  commentary.value = null;
  await forecast();
}

async function startGlobalBp() {
  if (!teamsReady.value) return;
  globalMode.value = "match";
  seriesGame.value = 1;
  resetSeriesTeams();
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "draft";
  await forecast();
}

async function customizeGlobalBp() {
  if (!teamsReady.value) return;
  globalMode.value = "custom";
  seriesGame.value = 2;
  resetSeriesTeams();
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "global-blue";
  await forecast();
}

async function clearGlobalBp() {
  globalMode.value = "single";
  seriesGame.value = 1;
  resetSeriesTeams();
  pickerTarget.value = "draft";
  await forecast();
}

async function startNextBattle() {
  if (
    currentStep.value ||
    seriesWinner.value ||
    !winnerSide.value ||
    !nextBlueTeam.value
  ) return;
  for (const side of ["blue", "red"]) {
    const team = teamsBySide.value[side];
    globalUsed.value[team] = [
      ...new Set([...globalUsed.value[team], ...board.value[`${side}_picks`]]),
    ];
  }
  const nextRedTeam = nextBlueTeam.value === TEAM_A ? TEAM_B : TEAM_A;
  teamsBySide.value = { blue: nextBlueTeam.value, red: nextRedTeam };
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  seriesGame.value += 1;
  winnerSide.value = null;
  nextBlueTeam.value = null;
  await forecast();
}

function recordGameWinner(side) {
  if (winnerSide.value === side) return;
  if (winnerSide.value) {
    const previousWinner = teamsBySide.value[winnerSide.value];
    seriesWins.value[previousWinner] -= 1;
  }
  const winner = teamsBySide.value[side];
  seriesWins.value[winner] += 1;
  winnerSide.value = side;
  nextBlueTeam.value = null;
}

async function removeGlobalHero(side, heroId) {
  const team = teamsBySide.value[side];
  globalUsed.value[team] = globalUsed.value[team].filter((id) => id !== heroId);
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
  } finally {
    finishStartupLoading();
  }
});

watch(leagueId, loadModel);
watch(modelType, forecast);
watch(selectedTeamIds, forecast, { deep: true });
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
      <section class="model-choice" aria-label="Forecast model">
        <div>
          <p class="simulator-eyebrow">{{ t("Forecast model") }}</p>
          <h2>{{ t("Choose how the next legal BP action is predicted") }}</h2>
        </div>
        <div class="model-choice-options">
          <button
            v-for="candidate in availableModels"
            :key="candidate.id"
            type="button"
            :class="{ active: modelType === candidate.id }"
            :disabled="!candidate.available || simulating"
            @click="modelType = candidate.id"
          >
            <strong>{{ t(candidate.label) }}</strong>
            <span>{{ t(candidate.description) }}</span>
            <small v-if="!candidate.available">{{ t("Not available for this season") }}</small>
          </button>
        </div>
      </section>

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
            {{ t("Earlier-game picks follow the team, even when it changes between Blue and Red. After each game, record the winner, then let the losing team choose its next color.") }}
          </p>
        </div>
        <div class="global-actions">
          <button type="button" :class="{ active: globalMode === 'single' }" :disabled="!teamsReady" @click="clearGlobalBp">Single game</button>
          <button type="button" :class="{ active: globalMode === 'match' }" :disabled="!teamsReady" @click="startGlobalBp">Start Global BP</button>
          <button type="button" :class="{ active: globalMode === 'custom' }" :disabled="!teamsReady" @click="customizeGlobalBp">Customize used heroes</button>
          <TeamCombobox
            v-model="selectedTeamIds[TEAM_A]"
            :label="t('Blue in game 1')"
            :teams="seasonTeams"
            :excluded-id="selectedTeamIds[TEAM_B]"
            :disabled="loading || history.length > 0 || seriesGame > 1"
          />
          <TeamCombobox
            v-model="selectedTeamIds[TEAM_B]"
            :label="t('Red in game 1')"
            :teams="seasonTeams"
            :excluded-id="selectedTeamIds[TEAM_A]"
            :disabled="loading || history.length > 0 || seriesGame > 1"
          />
          <label class="series-format">
            <span>Series</span>
            <select v-model.number="bestOf" :disabled="globalMode === 'single'">
              <option :value="5">BO5</option>
              <option :value="7">BO7</option>
            </select>
          </label>
        </div>
        <p v-if="!teamsReady" class="team-required">
          Search and select two teams from this season to start the simulation and give the coach its Blue/Red context.
        </p>
        <div v-if="globalMode !== 'single'" class="global-used">
          <div v-for="side in ['blue', 'red']" :key="side" class="used-team" :class="side">
            <span data-i18n-ignore>{{ sideUsedLabel(side) }}</span>
            <button
              v-for="heroId in globalUsed[teamsBySide[side]]"
              :key="`${side}-${heroId}`"
              type="button"
              :title="`Remove ${heroName(heroId)}`"
              @click="removeGlobalHero(side, heroId)"
            >
              <img :src="heroIcon(heroId)" :alt="heroName(heroId)" />
            </button>
            <small v-if="!globalUsed[teamsBySide[side]].length">None selected</small>
          </div>
          <div v-if="globalMode === 'match'" class="next-battle series-progress">
            <small>BO{{ bestOf }} · {{ teamName(TEAM_A) }} {{ seriesWins[TEAM_A] }}–{{ seriesWins[TEAM_B] }} {{ teamName(TEAM_B) }} · Game {{ seriesGame }}</small>
            <template v-if="seriesWinner">
              <strong data-i18n-ignore>{{ seriesWinnerLabel() }}</strong>
            </template>
            <template v-else-if="currentStep">
              <strong>Finish this draft to continue</strong>
            </template>
            <template v-else>
              <span data-i18n-ignore>{{ gameWinnerLabel(seriesGame) }}</span>
              <div class="series-choice">
                <button type="button" :class="{ active: winnerSide === 'blue' }" @click="recordGameWinner('blue')">{{ t("Blue wins") }}</button>
                <button type="button" :class="{ active: winnerSide === 'red' }" @click="recordGameWinner('red')">{{ t("Red wins") }}</button>
              </div>
              <template v-if="losingTeam">
                <span data-i18n-ignore>{{ loserColorChoiceLabel(losingTeam) }}</span>
                <div class="series-choice">
                  <button type="button" :class="{ active: nextBlueTeam === losingTeam }" @click="nextBlueTeam = losingTeam">{{ t("Play Blue") }}</button>
                  <button type="button" :class="{ active: nextBlueTeam !== null && nextBlueTeam !== losingTeam }" @click="nextBlueTeam = losingTeam === TEAM_A ? TEAM_B : TEAM_A">{{ t("Play Red") }}</button>
                </div>
              </template>
              <button type="button" :disabled="!winnerSide || !nextBlueTeam" @click="startNextBattle" data-i18n-ignore>{{ startGameLabel(seriesGame + 1) }}</button>
            </template>
          </div>
          <small v-else data-i18n-ignore>{{ seriesStatusLabel() }}</small>
        </div>
      </section>

      <div class="simulator-workspace">
        <div class="simulator-main-column">
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
                  <h2 data-i18n-ignore>{{ forecastLabel() }}</h2>
                  <small v-if="selectedModel">{{ t(selectedModel.label) }}</small>
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

          <section v-if="commentary || commentaryLoading" class="commentary-panel">
            <p class="simulator-eyebrow">BP commentator</p>
            <p v-if="commentaryLoading" class="commentary-loading">Generating commentary…</p>
            <h2 v-else>{{ commentary.commentary }}</h2>
          </section>

          <section class="hero-picker">
            <div class="picker-heading">
              <div>
                <p class="simulator-eyebrow">{{ pickerTarget === 'draft' ? 'Add the next action' : 'Global BP setup' }}</p>
                <h2 data-i18n-ignore>{{ pickerTitle }}</h2>
              </div>
              <button
                type="button"
                class="commentary-toggle"
                :class="{ active: commentaryEnabled }"
                :aria-pressed="commentaryEnabled"
                @click="commentaryEnabled = !commentaryEnabled"
              >
                <strong>AI 解说</strong>
                <small>{{ commentaryEnabled ? '已开启 · 每步调用 Kimi' : '未开启' }}</small>
              </button>
              <input v-model="search" type="search" placeholder="Find a hero…" :disabled="!teamsReady || (pickerTarget === 'draft' && !currentStep)" />
            </div>
            <div v-if="globalMode !== 'single'" class="picker-targets">
              <button type="button" :class="{ active: pickerTarget === 'draft' }" @click="pickerTarget = 'draft'">Current draft</button>
              <button type="button" :class="{ active: pickerTarget === 'global-blue' }" @click="pickerTarget = 'global-blue'" data-i18n-ignore>{{ earlierGamesLabel(teamsBySide.blue) }}</button>
              <button type="button" :class="{ active: pickerTarget === 'global-red' }" @click="pickerTarget = 'global-red'" data-i18n-ignore>{{ earlierGamesLabel(teamsBySide.red) }}</button>
            </div>
            <div class="hero-options">
              <button
                v-for="hero in availableHeroes"
                :key="hero.hero_id"
                type="button"
                :disabled="!teamsReady || (pickerTarget === 'draft' && !currentStep) || simulating"
                :title="`${hero.hero_name} · ${percent(probabilityByHeroId.get(Number(hero.hero_id)) || 0)}`"
                @click="chooseHero(hero.hero_id)"
              >
                <img v-if="heroIcon(hero.hero_id)" :src="heroIcon(hero.hero_id)" :alt="hero.hero_name" />
                <span v-else>{{ hero.hero_name.slice(0, 1) }}</span>
                <small>{{ percent(probabilityByHeroId.get(Number(hero.hero_id)) || 0) }}</small>
              </button>
            </div>
          </section>
        </div>

        <aside class="coach-rail" aria-label="Draft Coach conversation">
          <DraftCoachPanel
            :league-id="leagueId"
            :season-name="selectedSeason?.league_name || leagueId"
            :draft-state="coachDraftState"
          />
        </aside>
      </div>
    </template>
  </main>
</template>

<style scoped>
.simulator-page { width: min(1560px, calc(100% - 2rem)); margin: 0 auto; padding: 2.25rem 0 5rem; }
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
.model-choice { display:grid; grid-template-columns:minmax(14rem, .8fr) minmax(30rem, 1.8fr); gap:1rem 1.5rem; align-items:center; margin-top:1.5rem; padding:1rem 1.15rem; border:1px solid var(--accent-deep); background:linear-gradient(120deg, rgba(232,191,108,.24), rgba(255,255,255,.82)); }.model-choice h2 { margin:0; font:700 1.28rem var(--display); letter-spacing:-.035em; }.model-choice-options { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:.65rem; }.model-choice button { display:grid; gap:.22rem; min-height:84px; padding:.7rem .8rem; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink); text-align:left; font:inherit; cursor:pointer; }.model-choice button strong { font-size:.8rem; }.model-choice button span, .model-choice button small { color:var(--ink-soft); font-size:.66rem; line-height:1.35; }.model-choice button.active { border-color:var(--accent-deep); box-shadow:inset 3px 0 var(--accent-deep); background:#fffaf0; }.model-choice button:disabled { cursor:not-allowed; opacity:.58; }
.simulator-status { align-items: center; margin-top: 1.5rem; padding: 1rem 1.15rem; border: 1px solid var(--line); background: rgba(255,255,255,.72); }
.simulator-status > div:first-child span, .simulator-status small { display: block; color: var(--ink-soft); font-size: .65rem; letter-spacing: .08em; text-transform: uppercase; }
.simulator-status strong { display: block; margin: .18rem 0; font: 700 1.25rem var(--display); }
.simulator-actions { display: flex; align-items: end; gap: .5rem; }.simulator-actions label { display: grid; gap: .3rem; }
.simulator-actions button, .hero-options button, .draft-slots button { border: 1px solid var(--line); background: rgba(255,255,255,.86); color: var(--ink); font: inherit; cursor: pointer; }
.simulator-actions button { min-height: 42px; padding: .55rem .75rem; }.simulator-actions button:disabled, .hero-options button:disabled, .draft-slots button:disabled { cursor: default; opacity: .45; }
.global-bp-panel { display:grid; grid-template-columns:minmax(14rem, 1fr) auto; gap:1rem 1.5rem; margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.72); }.global-bp-panel h2 { margin:0; font:700 1.35rem var(--display); letter-spacing:-.04em; }.global-bp-panel > div:first-child > p:last-child { max-width:38rem; margin:.4rem 0 0; color:var(--ink-soft); font-size:.72rem; }.global-actions, .picker-targets { display:flex; flex-wrap:wrap; gap:.45rem; align-items:center; }.global-actions button, .picker-targets button, .next-battle { min-height:36px; padding:.45rem .6rem; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink-soft); font:inherit; font-size:.67rem; cursor:pointer; }.global-actions button.active, .picker-targets button.active, .series-choice button.active { border-color:var(--accent-deep); background:var(--ink); color:#fff; }.series-format, .team-name { display:grid; gap:.12rem; color:var(--ink-soft); font-size:.58rem; letter-spacing:.08em; text-transform:uppercase; }.series-format select, .team-name input { min-height:30px; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink); font:inherit; font-size:.67rem; }.team-name input { width:9rem; padding:0 .45rem; text-transform:none; letter-spacing:normal; }.global-used { display:grid; grid-template-columns:1fr 1fr auto; gap:.8rem; grid-column:1 / -1; padding-top:.8rem; border-top:1px solid var(--line); }.global-used > .used-team { display:flex; align-items:center; flex-wrap:wrap; gap:.35rem; }.global-used > .used-team > span { width:100%; color:var(--ink-soft); font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; }.global-used > .used-team button { width:2rem; height:2rem; padding:0; border:1px solid var(--line); background:#fff; cursor:pointer; }.global-used img { width:100%; height:100%; object-fit:cover; }.global-used small { align-self:center; color:var(--ink-soft); font-size:.66rem; }.global-used > .next-battle { align-self:end; min-height:36px; width:auto; height:auto; padding:.45rem .6rem; border-color:var(--accent-deep); background:var(--accent); color:#fff; white-space:nowrap; }.series-progress { display:grid; gap:.45rem; min-width:13rem; }.series-progress > span { font-size:.67rem; }.series-choice { display:flex; gap:.35rem; }.series-choice button, .series-progress > button { min-height:30px; padding:.35rem .5rem; border:1px solid rgba(255,255,255,.6); background:rgba(255,255,255,.18); color:#fff; font:inherit; font-size:.67rem; cursor:pointer; }.series-progress > button:disabled { cursor:not-allowed; opacity:.55; }.global-used > .next-battle:disabled { cursor:not-allowed; opacity:.5; }
.global-actions button:disabled { cursor:not-allowed; opacity:.45; }
.team-required { grid-column:1 / -1; margin:0; padding:.65rem .75rem; border:1px solid #d9b663; background:#fff8e7; color:var(--ink-soft); font-size:.68rem; }
.simulator-workspace { display:grid; grid-template-columns:minmax(0, 1fr) minmax(340px, 390px); gap:.85rem; align-items:start; margin-top:.75rem; }.simulator-main-column { min-width:0; }.coach-rail { position:sticky; top:1rem; min-width:0; }.simulator-layout { align-items: stretch; margin-top:0; gap:.75rem; }.draft-board { display: grid; flex: 1; min-width:0; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .75rem; }
.draft-group, .forecast-panel, .hero-picker { border: 1px solid var(--line); background: rgba(255,255,255,.76); }.draft-group { min-height: 160px; padding: 1rem; }.draft-group > p { margin: 0 0 .8rem; font-size: .67rem; letter-spacing: .1em; text-transform: uppercase; }.draft-group.blue > p { color: #286999; }.draft-group.red > p { color: #a84b4b; }
.draft-slots { display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:.35rem; }.draft-slots button, .draft-slots span { display:grid; place-items:center; width:100%; max-width:4rem; aspect-ratio:1; padding:0; font-size:.7rem; text-align:left; }.draft-slots button img { width:100%; height:100%; object-fit:cover; }.draft-slots span { border: 1px dashed var(--line); color: var(--ink-soft); }
.forecast-panel { width: min(31%, 320px); min-width:250px; padding: 1rem; }.forecast-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }.forecast-heading h2 { font-size: 1.5rem; }.forecast-heading > span, .forecast-heading small { color: var(--ink-soft); font-size: .68rem; }
.probability-list { margin-top: 1rem; }.probability-list > div { display: grid; grid-template-columns:2rem minmax(4rem,1.8fr) 3rem; gap: .55rem; align-items: center; margin-top: .55rem; font-size: .7rem; }.probability-list img { width:2rem; height:2rem; object-fit:cover; }.probability-list em { color: var(--ink-soft); font-style: normal; text-align: right; }.probability-track { height: .42rem; overflow: hidden; background: rgba(16,42,46,.1); }.probability-track i { display:block; height:100%; background: var(--accent); }
.end-ban-list { margin-top: 1.2rem; padding-top: .85rem; border-top: 1px solid var(--line); }.end-ban-list p { margin:0 0 .5rem; color: var(--ink-soft); font-size:.65rem; }.end-ban-list span { display:inline-flex; align-items:center; gap:.25rem; margin:.25rem .6rem 0 0; font-size:.7rem; }.end-ban-list img { width:1.6rem; height:1.6rem; object-fit:cover; }
.commentary-panel { margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--accent-deep); background:linear-gradient(120deg, rgba(232,191,108,.18), rgba(255,255,255,.84)); }.commentary-panel h2 { max-width:70rem; margin:.25rem 0 0; font:700 1rem/1.55 var(--display); letter-spacing:-.015em; }.commentary-loading { margin:0; color:var(--ink-soft); font-size:.75rem; }.commentary-toggle { display:grid; gap:.12rem; min-width:8.5rem; padding:.42rem .6rem; border:1px solid var(--line); background:rgba(255,255,255,.8); color:var(--ink-soft); text-align:left; font:inherit; cursor:pointer; }.commentary-toggle strong { color:var(--ink); font-size:.72rem; }.commentary-toggle small { font-size:.58rem; }.commentary-toggle.active { border-color:var(--accent-deep); background:rgba(232,191,108,.2); }.commentary-toggle.active strong { color:var(--accent-deep); }
.hero-picker { margin-top: .75rem; padding: 1rem; }.picker-heading { display:flex; align-items:end; justify-content:space-between; gap:1rem; }.picker-heading h2 { font-size:1.4rem; }.picker-heading input { width:min(100%, 260px); }.picker-targets { margin-top:.85rem; }.hero-options { display:grid; grid-template-columns:repeat(auto-fill, minmax(3.6rem, 1fr)); gap:.45rem; margin-top:1rem; max-height:360px; overflow:auto; }.hero-options button { position:relative; display:grid; place-items:center; aspect-ratio:1; padding:0; overflow:hidden; }.hero-options button img { width:100%; height:100%; object-fit:cover; }.hero-options button small { position:absolute; right:0; bottom:0; padding:.14rem .2rem; background:rgba(16,42,46,.84); color:#fff; font-size:.56rem; }.hero-options button:hover:not(:disabled), .draft-slots button:not(:disabled):hover { border-color: var(--accent); color: var(--accent-deep); }
@media (max-width: 1000px) { .simulator-workspace { grid-template-columns:1fr; }.coach-rail { position:static; }.coach-rail { grid-row:1; }.simulator-main-column { grid-row:2; } }
@media (max-width: 860px) { .simulator-hero, .simulator-status, .simulator-layout { flex-direction:column; align-items:stretch; }.simulator-season, .forecast-panel { width:100%; }.forecast-panel { min-width:0; }.model-choice { grid-template-columns:1fr; }.simulator-actions { justify-content:space-between; }.draft-board { grid-template-columns:1fr; }.global-bp-panel { grid-template-columns:1fr; }.global-used { grid-template-columns:1fr; }.next-battle { justify-self:start; } }
@media (max-width: 620px) { .simulator-page { width:calc(100% - 1rem); padding-top:1.25rem; }.simulator-status { gap:1rem; }.simulator-actions { flex-wrap:wrap; }.picker-heading { align-items:stretch; flex-direction:column; }.picker-heading input { width:100%; }.hero-options { grid-template-columns:repeat(auto-fill, minmax(3.25rem, 1fr)); } }
</style>
