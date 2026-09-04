<script setup>
import { computed, ref, watch } from "vue";
import { fetchUltimateCounterLineup, fetchUltimateLineups, scoreLineup, scoreNeutralLineup } from "./api";
import { heroAsset } from "./heroAssets";
import { heroSearchAliases } from "./heroSearchAliases";
import { language, t } from "./i18n";

const props = defineProps({
  leagueId: { type: String, required: true },
  heroes: { type: Array, default: () => [] },
  responseRows: { type: Array, default: () => [] },
  historicalLineups: { type: Array, default: () => [] },
});

const blueHeroIds = ref([]);
const redHeroIds = ref([]);
const activeSide = ref("blue");
const search = ref("");
const focusedRelationshipKey = ref("");
const ultimateLoading = ref(false);
const ultimateError = ref("");
const ultimateResult = ref(null);
const ultimateExpanded = ref(false);
const counterLoading = ref(false);
const counterError = ref("");
const generatedCounter = ref(null);
const selectedHistoricalLineupKey = ref("");
let counterRequestNumber = 0;
const historicalScore = ref(null);
const historicalScoreLoading = ref(false);
const historicalScoreError = ref("");
let historicalScoreRequestNumber = 0;
const neutralScore = ref(null);
const neutralScoreLoading = ref(false);
const neutralScoreError = ref("");
let neutralScoreRequestNumber = 0;
const laneLabels = {
  clash: "Clash",
  mid: "Mid",
  jungle: "Jungle",
  farm: "Farm",
  roam: "Roam",
  unknown: "Unknown",
};
const positionLanes = { 2: "mid", 4: "roam", 5: "jungle", 6: "clash", 7: "farm" };
const lanePositions = { mid: 2, roam: 4, jungle: 5, clash: 6, farm: 7 };

const selectedActiveSideIds = computed(
  () => new Set(teamIds(activeSide.value).map(Number))
);
const heroById = computed(
  () => new Map(props.heroes.map((hero) => [Number(hero.hero_id), hero]))
);
const supportedRows = computed(() =>
  props.responseRows.filter(
    (row) =>
      row.context_level === "overall" &&
      !row.is_peak_battle &&
      ["pick_synergy", "counter_pick"].includes(row.relation) &&
      Number(row.selections || 0) >= 2 &&
      Number(row.smoothed_lift || 0) > 1
  )
);

function teamIds(side) {
  return side === "blue" ? blueHeroIds.value : redHeroIds.value;
}

function otherTeamIds(side) {
  return side === "blue" ? redHeroIds.value : blueHeroIds.value;
}

function heroName(heroId) {
  return heroById.value.get(Number(heroId))?.hero_name || String(heroId);
}

function heroRecord(heroOrId) {
  return typeof heroOrId === "object"
    ? heroOrId
    : heroById.value.get(Number(heroOrId));
}

function heroPositions(heroOrId) {
  const hero = heroRecord(heroOrId);
  const positions = (hero?.positions || [])
    .map(Number)
    .filter((position) => positionLanes[position]);
  if (positions.length) return [...new Set(positions)];
  const fallback = lanePositions[hero?.primary_lane];
  return fallback ? [fallback] : [];
}

function heroLane(heroOrId) {
  const hero = heroRecord(heroOrId);
  const firstPosition = heroPositions(hero)[0];
  if (hero?.primary_lane && hero.primary_lane !== "unknown") return hero.primary_lane;
  if (firstPosition) return positionLanes[firstPosition];
  return hero?.primary_lane || "unknown";
}

function laneLabel(heroOrId) {
  const positions = heroPositions(heroOrId);
  if (positions.length) {
    return positions.map((position) => t(laneLabels[positionLanes[position]])).join(" / ");
  }
  return t(laneLabels[heroLane(heroOrId)] || laneLabels.unknown);
}

function rolesAreFeasible(heroIds) {
  const assignments = new Map();
  function assign(heroId, visited) {
    for (const position of heroPositions(heroId)) {
      if (visited.has(position)) continue;
      visited.add(position);
      const assignedHero = assignments.get(position);
      if (assignedHero === undefined || assign(assignedHero, visited)) {
        assignments.set(position, Number(heroId));
        return true;
      }
    }
    return false;
  }
  return heroIds.every((heroId) => assign(Number(heroId), new Set()));
}

function laneConflict(hero, side = activeSide.value) {
  return !rolesAreFeasible([...teamIds(side), Number(hero?.hero_id)]);
}

function relationshipWeight(row) {
  const lift = Math.max(1, Number(row?.smoothed_lift || 1));
  const support = Math.min(1, Number(row?.selections || 0) / 6);
  return Math.log2(lift) * support;
}

function directRelationship(relation, sourceId, targetId) {
  return supportedRows.value
    .filter(
      (row) =>
        row.relation === relation &&
        Number(row.source_hero_id) === Number(sourceId) &&
        Number(row.target_hero_id) === Number(targetId)
    )
    .sort((a, b) => relationshipWeight(b) - relationshipWeight(a))[0] || null;
}

function synergyRelationship(firstId, secondId) {
  const forward = directRelationship("pick_synergy", firstId, secondId);
  const reverse = directRelationship("pick_synergy", secondId, firstId);
  return relationshipWeight(forward) >= relationshipWeight(reverse) ? forward : reverse;
}

function candidateFit(heroId) {
  const allies = teamIds(activeSide.value);
  const enemies = otherTeamIds(activeSide.value);
  const synergy = allies.reduce(
    (sum, allyId) => sum + relationshipWeight(synergyRelationship(allyId, heroId)),
    0
  );
  const counters = enemies.reduce(
    (sum, enemyId) => sum + relationshipWeight(directRelationship("counter_pick", enemyId, heroId)),
    0
  );
  return { synergy, counters, total: synergy + counters };
}

function fuzzyScore(hero, query) {
  const needle = query.trim().toLocaleLowerCase().replaceAll(/\s+/g, "");
  if (!needle) return 0;
  const terms = `${hero.hero_name || ""} ${heroSearchAliases[hero.hero_name] || ""} ${hero.search_aliases || ""}`
    .toLocaleLowerCase()
    .split(/\s+/);
  let best = Number.POSITIVE_INFINITY;
  for (const term of terms) {
    if (term === needle) best = Math.min(best, 0);
    else if (term.includes(needle)) best = Math.min(best, 1 + term.indexOf(needle) / 100);
  }
  return best;
}

const heroOptions = computed(() => {
  const query = search.value.trim();
  return props.heroes
    .filter((hero) => !selectedActiveSideIds.value.has(Number(hero.hero_id)))
    .map((hero) => ({
      hero,
      fit: candidateFit(hero.hero_id),
      fuzzy: fuzzyScore(hero, query),
      laneConflict: laneConflict(hero),
    }))
    .filter((item) => !query || Number.isFinite(item.fuzzy))
    .sort(
      (a, b) =>
        a.fuzzy - b.fuzzy ||
        Number(a.laneConflict) - Number(b.laneConflict) ||
        b.fit.total - a.fit.total ||
        Number(b.hero.weighted_bp_action_count || 0) - Number(a.hero.weighted_bp_action_count || 0) ||
        a.hero.hero_name.localeCompare(b.hero.hero_name, language.value)
    )
    .slice(0, 40);
});

function addHero(heroId) {
  const target = teamIds(activeSide.value);
  const id = Number(heroId);
  const hero = heroById.value.get(id);
  if (target.includes(id) || target.length >= 5 || laneConflict(hero)) return;
  selectedHistoricalLineupKey.value = "";
  target.push(id);
  focusedRelationshipKey.value = "";
  search.value = "";
  if (target.length === 5 && otherTeamIds(activeSide.value).length < 5) {
    activeSide.value = activeSide.value === "blue" ? "red" : "blue";
  }
}

function removeHero(side, heroId) {
  const target = side === "blue" ? blueHeroIds : redHeroIds;
  target.value = target.value.filter((id) => Number(id) !== Number(heroId));
  selectedHistoricalLineupKey.value = "";
  activeSide.value = side;
  focusedRelationshipKey.value = "";
}

function clearLineups() {
  counterRequestNumber += 1;
  blueHeroIds.value = [];
  redHeroIds.value = [];
  activeSide.value = "blue";
  search.value = "";
  focusedRelationshipKey.value = "";
  counterLoading.value = false;
  counterError.value = "";
  generatedCounter.value = null;
  selectedHistoricalLineupKey.value = "";
}

function historicalLineupLabel(battle) {
  const date = String(battle.start_time || "").slice(0, 10);
  return `${date} · ${battle.blue_team_name} vs ${battle.red_team_name} · ${battleSequenceLabel(battle.battle_seq)}`;
}

function battleSequenceLabel(sequence) {
  return language.value === "en" ? `Game ${sequence}` : `第 ${sequence} 局`;
}

const selectedHistoricalLineup = computed(() =>
  props.historicalLineups.find(
    (row) => row.key === selectedHistoricalLineupKey.value
  ) || null
);

function loadHistoricalLineup(battle, event) {
  if (!battle) return;
  counterRequestNumber += 1;
  counterLoading.value = false;
  counterError.value = "";
  generatedCounter.value = null;
  selectedHistoricalLineupKey.value = battle.key;
  blueHeroIds.value = battle.blue.map((hero) => Number(hero.hero_id));
  redHeroIds.value = battle.red.map((hero) => Number(hero.hero_id));
  activeSide.value = "blue";
  search.value = "";
  focusedRelationshipKey.value = "";
  event?.currentTarget?.closest("details")?.removeAttribute("open");
  void loadHistoricalScore(battle);
}

async function loadHistoricalScore(battle) {
  const requestNumber = ++historicalScoreRequestNumber;
  historicalScore.value = null;
  historicalScoreError.value = "";
  if (!battle.blue_team_id || !battle.red_team_id) {
    historicalScoreError.value = t("Team IDs are unavailable for this historical score.");
    return;
  }
  historicalScoreLoading.value = true;
  try {
    const result = await scoreLineup({
      league_id: props.leagueId,
      blue_team_id: battle.blue_team_id,
      red_team_id: battle.red_team_id,
      blue_hero_ids: battle.blue.map((hero) => Number(hero.hero_id)),
      red_hero_ids: battle.red.map((hero) => Number(hero.hero_id)),
    });
    if (
      requestNumber === historicalScoreRequestNumber &&
      selectedHistoricalLineupKey.value === battle.key
    ) historicalScore.value = result;
  } catch (error) {
    if (requestNumber === historicalScoreRequestNumber) {
      historicalScoreError.value = error.message || t("Could not score this historical lineup.");
    }
  } finally {
    if (requestNumber === historicalScoreRequestNumber) historicalScoreLoading.value = false;
  }
}

const historicalWinnerName = computed(() => {
  if (!selectedHistoricalLineup.value) return "";
  return Number(selectedHistoricalLineup.value.winner_camp) === 1
    ? selectedHistoricalLineup.value.blue_team_name
    : Number(selectedHistoricalLineup.value.winner_camp) === 2
      ? selectedHistoricalLineup.value.red_team_name
      : t("Unknown");
});

const historicalModelFavorite = computed(() => {
  if (!historicalScore.value || !selectedHistoricalLineup.value) return "";
  return Number(historicalScore.value.blue_advantage) >= Number(historicalScore.value.red_advantage)
    ? selectedHistoricalLineup.value.blue_team_name
    : selectedHistoricalLineup.value.red_team_name;
});

const neutralModelFavorite = computed(() => {
  if (!neutralScore.value) return "";
  return Number(neutralScore.value.blue_advantage) >= Number(neutralScore.value.red_advantage)
    ? t("Blue lineup")
    : t("Red lineup");
});

async function loadNeutralScore(blueHeroIdsSnapshot, redHeroIdsSnapshot) {
  const requestNumber = ++neutralScoreRequestNumber;
  neutralScore.value = null;
  neutralScoreError.value = "";
  neutralScoreLoading.value = true;
  try {
    const result = await scoreNeutralLineup({
      league_id: props.leagueId,
      blue_hero_ids: blueHeroIdsSnapshot,
      red_hero_ids: redHeroIdsSnapshot,
    });
    if (requestNumber === neutralScoreRequestNumber) neutralScore.value = result;
  } catch (error) {
    if (requestNumber === neutralScoreRequestNumber) {
      neutralScoreError.value = error.message || t("Could not score these lineups.");
    }
  } finally {
    if (requestNumber === neutralScoreRequestNumber) neutralScoreLoading.value = false;
  }
}

const ultimateProfiles = computed(() => ultimateResult.value?.profiles || []);
const ultimateProfileLabels = {
  best_overall: "Best overall meta lineup",
  safest: "Safest lineup across the meta",
  highest_upside: "Highest-upside lineup",
  main_counter: "Main counter lineup",
};
const ultimateProfileDescriptions = {
  best_overall: "Highest average score across current meta scenarios.",
  safest: "Best downside protection against the wider meta.",
  highest_upside: "Highest ceiling in favorable meta matchups.",
  main_counter: "Built specifically to threaten the best overall lineup.",
};

async function loadUltimateLineups() {
  if (!props.leagueId || ultimateLoading.value) return;
  if (ultimateResult.value) {
    ultimateExpanded.value = !ultimateExpanded.value;
    return;
  }
  ultimateExpanded.value = true;
  ultimateLoading.value = true;
  ultimateError.value = "";
  try {
    ultimateResult.value = await fetchUltimateLineups(props.leagueId);
  } catch (error) {
    ultimateResult.value = null;
    ultimateError.value = error.message || t("Could not calculate ultimate lineups.");
  } finally {
    ultimateLoading.value = false;
  }
}

const blueLineupComplete = computed(() => blueHeroIds.value.length === 5);

async function generateCounterLineup() {
  if (!props.leagueId || !blueLineupComplete.value || counterLoading.value) return;
  const requestNumber = ++counterRequestNumber;
  const targetHeroIds = [...blueHeroIds.value];
  counterLoading.value = true;
  counterError.value = "";
  generatedCounter.value = null;
  try {
    const result = await fetchUltimateCounterLineup({
      leagueId: props.leagueId,
      targetHeroIds,
    });
    if (
      requestNumber !== counterRequestNumber ||
      targetHeroIds.some((heroId, index) => heroId !== blueHeroIds.value[index])
    ) return;
    generatedCounter.value = result.profile;
    redHeroIds.value = result.profile.heroes.map((hero) => Number(hero.hero_id));
    selectedHistoricalLineupKey.value = "";
    activeSide.value = "blue";
    focusedRelationshipKey.value = "";
  } catch (error) {
    if (requestNumber !== counterRequestNumber) return;
    counterError.value = error.message || t("Could not calculate a counter lineup.");
  } finally {
    if (requestNumber === counterRequestNumber) counterLoading.value = false;
  }
}

function loadProfile(profile, side = "blue") {
  const ids = profile.heroes.map((hero) => Number(hero.hero_id));
  if (side === "blue") blueHeroIds.value = ids;
  else redHeroIds.value = ids;
  selectedHistoricalLineupKey.value = "";
  activeSide.value = side === "blue" ? "red" : "blue";
  focusedRelationshipKey.value = "";
}

function compareCounterProfile() {
  const best = ultimateProfiles.value.find((profile) => profile.key === "best_overall");
  const counter = ultimateProfiles.value.find((profile) => profile.key === "main_counter");
  if (!best || !counter) return;
  blueHeroIds.value = best.heroes.map((hero) => Number(hero.hero_id));
  redHeroIds.value = counter.heroes.map((hero) => Number(hero.hero_id));
  selectedHistoricalLineupKey.value = "";
  activeSide.value = "blue";
  focusedRelationshipKey.value = "";
}

function metaScore(value) {
  return (Number(value || 0) * 100).toFixed(1);
}

watch(
  () => props.leagueId,
  () => {
    ultimateResult.value = null;
    ultimateError.value = "";
    ultimateExpanded.value = false;
    generatedCounter.value = null;
    counterError.value = "";
    clearLineups();
  }
);

watch(
  () => [...blueHeroIds.value],
  () => {
    counterRequestNumber += 1;
    counterLoading.value = false;
    generatedCounter.value = null;
    counterError.value = "";
  },
  { flush: "sync" }
);

watch(selectedHistoricalLineupKey, (key) => {
  if (key) return;
  historicalScoreRequestNumber += 1;
  historicalScore.value = null;
  historicalScoreLoading.value = false;
  historicalScoreError.value = "";
});

function synergyRows(ids) {
  const rows = [];
  for (let first = 0; first < ids.length; first += 1) {
    for (let second = first + 1; second < ids.length; second += 1) {
      const evidence = synergyRelationship(ids[first], ids[second]);
      if (!evidence) continue;
      rows.push({
        key: `synergy-${ids[first]}-${ids[second]}`,
        label: `${heroName(ids[first])} + ${heroName(ids[second])}`,
        sourceId: Number(ids[first]),
        targetId: Number(ids[second]),
        evidence,
      });
    }
  }
  return rows.sort((a, b) => relationshipWeight(b.evidence) - relationshipWeight(a.evidence));
}

function counterRows(attackerIds, defenderIds, side) {
  const rows = [];
  for (const attackerId of attackerIds) {
    for (const defenderId of defenderIds) {
      const evidence = directRelationship("counter_pick", defenderId, attackerId);
      if (!evidence) continue;
      rows.push({
        key: `counter-${side}-${attackerId}-${defenderId}`,
        label: `${heroName(attackerId)} → ${heroName(defenderId)}`,
        sourceId: Number(attackerId),
        targetId: Number(defenderId),
        evidence,
      });
    }
  }
  return rows.sort((a, b) => relationshipWeight(b.evidence) - relationshipWeight(a.evidence));
}

const analysisGroups = computed(() => [
  { key: "blue-synergy", title: t("Blue synergy"), tone: "blue", rows: synergyRows(blueHeroIds.value) },
  { key: "red-synergy", title: t("Red synergy"), tone: "red", rows: synergyRows(redHeroIds.value) },
  { key: "blue-counter", title: t("Blue counter edges"), tone: "blue", rows: counterRows(blueHeroIds.value, redHeroIds.value, "blue") },
  { key: "red-counter", title: t("Red counter edges"), tone: "red", rows: counterRows(redHeroIds.value, blueHeroIds.value, "red") },
]);

const hasSelections = computed(() => blueHeroIds.value.length + redHeroIds.value.length > 0);
const lineupsComplete = computed(
  () => blueHeroIds.value.length === 5 && redHeroIds.value.length === 5
);
const hasAnalysis = computed(() => analysisGroups.value.some((group) => group.rows.length));

watch(
  () => [
    props.leagueId,
    selectedHistoricalLineupKey.value,
    blueHeroIds.value.join(","),
    redHeroIds.value.join(","),
  ],
  () => {
    neutralScoreRequestNumber += 1;
    neutralScore.value = null;
    neutralScoreLoading.value = false;
    neutralScoreError.value = "";
    if (!lineupsComplete.value || selectedHistoricalLineupKey.value) return;
    void loadNeutralScore([...blueHeroIds.value], [...redHeroIds.value]);
  }
);

const graphEdges = computed(() => [
  ...synergyRows(blueHeroIds.value).map((row) => ({ ...row, key: `blue-${row.key}`, type: "synergy", side: "blue" })),
  ...synergyRows(redHeroIds.value).map((row) => ({ ...row, key: `red-${row.key}`, type: "synergy", side: "red" })),
  ...counterRows(blueHeroIds.value, redHeroIds.value, "blue").map((row) => ({ ...row, type: "counter", side: "blue" })),
  ...counterRows(redHeroIds.value, blueHeroIds.value, "red").map((row) => ({ ...row, type: "counter", side: "red" })),
].sort((a, b) => relationshipWeight(b.evidence) - relationshipWeight(a.evidence)));

const focusedRelationship = computed(
  () => graphEdges.value.find((edge) => edge.key === focusedRelationshipKey.value) || graphEdges.value[0] || null
);

function heroGraphY(heroId, side) {
  const index = teamIds(side).findIndex((id) => Number(id) === Number(heroId));
  return 62 + Math.max(0, index) * 82;
}

function relationshipPath(edge) {
  const sourceY = heroGraphY(edge.sourceId, edge.side);
  if (edge.type === "synergy") {
    const targetY = heroGraphY(edge.targetId, edge.side);
    const x = edge.side === "blue" ? 178 : 822;
    const outsideX = edge.side === "blue" ? 42 : 958;
    return `M ${x} ${sourceY} C ${outsideX} ${sourceY}, ${outsideX} ${targetY}, ${x} ${targetY}`;
  }
  const opponentSide = edge.side === "blue" ? "red" : "blue";
  const targetY = heroGraphY(edge.targetId, opponentSide);
  const sourceX = edge.side === "blue" ? 202 : 798;
  const targetX = edge.side === "blue" ? 798 : 202;
  const bend = edge.side === "blue" ? -14 : 14;
  return `M ${sourceX} ${sourceY} C 420 ${sourceY + bend}, 580 ${targetY + bend}, ${targetX} ${targetY}`;
}

function relationshipWidth(edge) {
  return Math.min(6, 1.5 + relationshipWeight(edge.evidence));
}

function relationshipOpacity(edge) {
  if (!focusedRelationshipKey.value) return 0.62;
  return focusedRelationshipKey.value === edge.key ? 1 : 0.14;
}

function focusRelationship(edge) {
  focusedRelationshipKey.value = edge.key;
}

function clearRelationshipFocus() {
  focusedRelationshipKey.value = "";
}

function liftLabel(row) {
  return `${Number(row.smoothed_lift || 0).toFixed(1)}× · ${Number(row.selections || 0)} ${t("picks")}`;
}
</script>

<template>
  <section class="lineup-analyzer" aria-labelledby="lineup-analyzer-heading">
    <header>
      <div>
        <p class="lineup-eyebrow">{{ t("5v5 lineup analyzer") }}</p>
        <h2 id="lineup-analyzer-heading">{{ t("Build two lineups. See every strong connection.") }}</h2>
        <p>{{ t("Hero suggestions move up when professional BP data shows synergy with your side or a counter response into the other side.") }}</p>
      </div>
      <div class="lineup-header-actions">
        <button
          type="button"
          class="ultimate-trigger"
          :disabled="ultimateLoading"
          :aria-expanded="ultimateExpanded"
          aria-controls="ultimate-lineup-results"
          @click="loadUltimateLineups"
        >
          {{ ultimateLoading
            ? t('Searching the meta…')
            : ultimateExpanded
              ? t('Hide ultimate lineups')
              : ultimateResult
                ? t('Show ultimate lineups')
                : t('Find ultimate lineups') }}
        </button>
        <button type="button" :disabled="!hasSelections" @click="clearLineups">{{ t("Clear lineups") }}</button>
      </div>
    </header>

    <p v-if="ultimateExpanded && ultimateError" class="ultimate-error">{{ ultimateError }}</p>
    <section id="ultimate-lineup-results" v-if="ultimateExpanded && ultimateProfiles.length" class="ultimate-results" aria-live="polite">
      <header>
        <div>
          <span>{{ t("Team-neutral peak-duel search") }}</span>
          <strong>{{ t("Ultimate lineup profiles") }}</strong>
        </div>
        <small>{{ ultimateResult.methodology.candidate_lineups_evaluated }} {{ t("legal candidates") }} · {{ ultimateResult.methodology.meta_scenarios }} {{ t("meta scenarios") }}</small>
      </header>
      <div class="ultimate-profile-grid">
        <article v-for="(profile, index) in ultimateProfiles" :key="profile.key" :class="profile.key">
          <div class="ultimate-profile-heading">
            <span>#{{ index + 1 }}</span>
            <div>
              <h3>{{ t(ultimateProfileLabels[profile.key]) }}</h3>
              <p>{{ t(ultimateProfileDescriptions[profile.key]) }}</p>
            </div>
            <div class="ultimate-profile-score">
              <strong>{{ metaScore(profile.score) }}</strong>
              <small>{{ t("meta score") }}</small>
            </div>
          </div>
          <div class="ultimate-heroes">
            <div v-for="hero in profile.heroes" :key="hero.hero_id">
              <img :src="heroAsset(hero.hero_id)" :alt="hero.hero_name" />
              <span>{{ hero.hero_name }}</span>
              <small>{{ t(laneLabels[hero.assigned_lane] || 'Unknown') }}</small>
            </div>
          </div>
          <div class="ultimate-profile-actions">
            <button type="button" @click="loadProfile(profile)">{{ t("Load into Blue") }}</button>
            <button v-if="profile.key === 'main_counter'" type="button" @click="compareCounterProfile">{{ t("Compare with #1") }}</button>
          </div>
        </article>
      </div>
      <p>{{ t("Computed on demand from existing season artifacts. Team and player preferences are excluded.") }}</p>
    </section>

    <div v-if="historicalLineups.length" class="historical-lineup-picker">
      <span>
        <strong>{{ t("Load a past KPL battle") }}</strong>
        <small>{{ t("Select a completed game to place both official lineups on the board.") }}</small>
      </span>
      <details class="historical-lineup-dropdown">
        <summary>
          <span>{{ selectedHistoricalLineup ? historicalLineupLabel(selectedHistoricalLineup) : t("Choose a past battle…") }}</span>
          <div v-if="selectedHistoricalLineup" class="selected-lineup-preview" aria-hidden="true">
            <span class="blue-box">
              <img v-for="hero in selectedHistoricalLineup.blue" :key="`selected-blue-${hero.hero_id}`" :src="heroAsset(hero.hero_id)" alt="" />
            </span>
            <b>VS</b>
            <span class="red-box">
              <img v-for="hero in selectedHistoricalLineup.red" :key="`selected-red-${hero.hero_id}`" :src="heroAsset(hero.hero_id)" alt="" />
            </span>
          </div>
        </summary>
        <div class="historical-lineup-options" role="listbox">
          <button
            v-for="battle in historicalLineups"
            :key="battle.key"
            type="button"
            :class="{ selected: battle.key === selectedHistoricalLineupKey }"
            role="option"
            :aria-selected="battle.key === selectedHistoricalLineupKey"
            @click="loadHistoricalLineup(battle, $event)"
          >
            <span class="historical-match-row">
              <strong class="blue-name">{{ battle.blue_team_name }}</strong>
              <b>VS</b>
              <strong class="red-name">{{ battle.red_team_name }}</strong>
              <small>{{ String(battle.start_time || '').slice(0, 10) }} · {{ battleSequenceLabel(battle.battle_seq) }}</small>
            </span>
            <span class="historical-heroes-row">
              <span class="historical-team-box blue-box">
                <span v-for="hero in battle.blue" :key="`blue-${battle.key}-${hero.hero_id}`">
                  <img :src="heroAsset(hero.hero_id)" :alt="hero.hero_name" :title="`${hero.hero_name} · ${laneLabel(positionLanes[hero.position] || 'unknown')}`" />
                </span>
              </span>
              <b>VS</b>
              <span class="historical-team-box red-box">
                <span v-for="hero in battle.red" :key="`red-${battle.key}-${hero.hero_id}`">
                  <img :src="heroAsset(hero.hero_id)" :alt="hero.hero_name" :title="`${hero.hero_name} · ${laneLabel(positionLanes[hero.position] || 'unknown')}`" />
                </span>
              </span>
            </span>
          </button>
        </div>
      </details>
    </div>

    <div class="lineup-boards">
      <article v-for="side in ['blue', 'red']" :key="side" :class="['lineup-side', side, { active: activeSide === side }]">
        <button type="button" class="lineup-side-heading" @click="activeSide = side">
          <span>{{ side === 'blue' ? t('Blue lineup') : t('Red lineup') }}</span>
          <small>{{ teamIds(side).length }} / 5</small>
        </button>
        <div class="lineup-slots">
          <button
            v-for="heroId in teamIds(side)"
            :key="heroId"
            type="button"
            :title="`${t('Remove')} ${heroName(heroId)}`"
            @click="removeHero(side, heroId)"
          >
            <img :src="heroAsset(heroId)" :alt="heroName(heroId)" />
            <span>{{ heroName(heroId) }} · {{ laneLabel(heroId) }}</span>
          </button>
          <button
            v-for="slot in Math.max(0, 5 - teamIds(side).length)"
            :key="`empty-${side}-${slot}`"
            type="button"
            class="empty-slot"
            @click="activeSide = side"
          >+</button>
        </div>
      </article>
    </div>

    <div v-if="!selectedHistoricalLineup" :class="['counter-generator', { ready: blueLineupComplete }]">
      <div>
        <span>{{ t("Counter the Blue lineup") }}</span>
        <strong>{{ blueLineupComplete ? t("Blue is ready — build its strongest counter") : t("Complete all five Blue slots first") }}</strong>
        <small v-if="generatedCounter">
          {{ t("Red counter generated") }} · {{ t("counter score") }} {{ metaScore(generatedCounter.score) }}
        </small>
        <small v-else>{{ t("Uses global meta, synergy, composition, and direct matchup evidence.") }}</small>
      </div>
      <button
        type="button"
        :disabled="!blueLineupComplete || counterLoading"
        @click="generateCounterLineup"
      >
        {{ counterLoading ? t("Building counter…") : t("Generate Red counter") }}
      </button>
    </div>
    <p v-if="!selectedHistoricalLineup && counterError" class="ultimate-error">{{ counterError }}</p>

    <div v-if="!lineupsComplete" class="lineup-picker">
      <label>
        <span>{{ activeSide === 'blue' ? t('Add to Blue') : t('Add to Red') }}</span>
        <input v-model="search" type="search" :placeholder="t('Search heroes for this lineup…')" />
      </label>
      <p>{{ t("Ranked for the heroes already on this board") }}</p>
      <div class="lineup-hero-options">
        <button
          v-for="item in heroOptions"
          :key="item.hero.hero_id"
          type="button"
          :disabled="teamIds(activeSide).length >= 5 || item.laneConflict"
          :title="item.laneConflict ? t('No open compatible lane for {lanes} on this side.').replace('{lanes}', laneLabel(item.hero)) : ''"
          @click="addHero(item.hero.hero_id)"
        >
          <img :src="heroAsset(item.hero.hero_id)" :alt="item.hero.hero_name" />
          <span>
            <strong>{{ item.hero.hero_name }}</strong>
            <small v-if="item.laneConflict" class="lane-conflict">
              {{ t('No open compatible lane: {lanes}').replace('{lanes}', laneLabel(item.hero)) }}
            </small>
            <small v-else-if="item.fit.total > 0">
              <i v-if="item.fit.synergy > 0">{{ t("Synergy") }} +{{ item.fit.synergy.toFixed(1) }}</i>
              <i v-if="item.fit.counters > 0">{{ t("Counter") }} +{{ item.fit.counters.toFixed(1) }}</i>
            </small>
            <small v-else>{{ laneLabel(item.hero) }} · {{ t("No direct relationship yet") }}</small>
          </span>
        </button>
      </div>
    </div>

    <div v-if="hasSelections" class="lineup-analysis">
      <div v-if="hasAnalysis" class="relationship-graph-shell">
        <div class="relationship-legend">
          <span><i class="synergy-line"></i>{{ t("Ally synergy") }}</span>
          <span><i class="counter-line"></i>{{ t("Counter direction") }}</span>
          <small>{{ t("Thicker lines indicate stronger historical lift.") }}</small>
        </div>
        <div class="relationship-graph-scroll">
          <svg
            class="relationship-graph"
            viewBox="0 0 1000 455"
            role="img"
            :aria-label="t('Interactive graph of lineup synergy and counter relationships')"
            @mouseleave="clearRelationshipFocus"
          >
            <defs>
              <marker id="counter-arrow-blue" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#397fa8" />
              </marker>
              <marker id="counter-arrow-red" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#b75952" />
              </marker>
            </defs>

            <text x="178" y="28" class="graph-team-title blue">{{ t("Blue lineup") }}</text>
            <text x="822" y="28" class="graph-team-title red">{{ t("Red lineup") }}</text>

            <path
              v-for="edge in graphEdges"
              :key="edge.key"
              :d="relationshipPath(edge)"
              fill="none"
              :class="['relationship-edge', edge.type, edge.side, { focused: focusedRelationshipKey === edge.key }]"
              :style="{ strokeWidth: relationshipWidth(edge), opacity: relationshipOpacity(edge) }"
              :marker-end="edge.type === 'counter' ? `url(#counter-arrow-${edge.side})` : undefined"
              tabindex="0"
              :aria-label="`${edge.label} · ${liftLabel(edge.evidence)}`"
              @mouseenter="focusRelationship(edge)"
              @focus="focusRelationship(edge)"
              @click="focusRelationship(edge)"
            />

            <g v-for="side in ['blue', 'red']" :key="`nodes-${side}`">
              <g
                v-for="heroId in teamIds(side)"
                :key="`node-${side}-${heroId}`"
                :transform="`translate(${side === 'blue' ? 178 : 822} ${heroGraphY(heroId, side)})`"
                class="relationship-node"
              >
                <circle r="27" :class="side" />
                <image :href="heroAsset(heroId)" x="-23" y="-23" width="46" height="46" preserveAspectRatio="xMidYMid slice" />
                <text
                  :x="side === 'blue' ? -38 : 38"
                  y="5"
                  :text-anchor="side === 'blue' ? 'end' : 'start'"
                >{{ heroName(heroId) }}</text>
                <text
                  :x="side === 'blue' ? -38 : 38"
                  y="21"
                  :text-anchor="side === 'blue' ? 'end' : 'start'"
                  class="node-lane"
                >{{ laneLabel(heroId) }}</text>
              </g>
            </g>
          </svg>
        </div>
        <article v-if="focusedRelationship" class="relationship-focus" :class="[focusedRelationship.type, focusedRelationship.side]">
          <span>{{ focusedRelationship.type === 'synergy' ? t('Synergy connection') : t('Counter arrow') }}</span>
          <strong>{{ focusedRelationship.label }}</strong>
          <div>
            <b>{{ Number(focusedRelationship.evidence.smoothed_lift || 0).toFixed(1) }}×</b>
            <small>{{ t("historical lift") }}</small>
          </div>
          <div>
            <b>{{ Number(focusedRelationship.evidence.selections || 0) }}</b>
            <small>{{ t("supporting picks") }}</small>
          </div>
        </article>
      </div>
      <p v-else class="lineup-empty-analysis">{{ t("Add more heroes to reveal supported synergy and counter combinations.") }}</p>
      <section v-if="selectedHistoricalLineup" class="historical-score-card" aria-live="polite">
        <header>
          <div>
            <span>{{ t("Recorded result") }}</span>
            <strong>{{ t("Actual winner") }} · {{ historicalWinnerName }}</strong>
          </div>
          <small>{{ String(selectedHistoricalLineup.start_time || '').slice(0, 10) }} · {{ battleSequenceLabel(selectedHistoricalLineup.battle_seq) }}</small>
        </header>
        <p v-if="historicalScoreLoading">{{ t("Scoring the historical lineup…") }}</p>
        <p v-else-if="historicalScoreError" class="score-error">{{ historicalScoreError }}</p>
        <template v-else-if="historicalScore">
          <div class="historical-score-values">
            <div class="blue-score">
              <small>{{ selectedHistoricalLineup.blue_team_name }}</small>
              <strong>{{ metaScore(historicalScore.blue_advantage) }}</strong>
            </div>
            <div class="score-track" aria-hidden="true">
              <span :style="{ width: `${metaScore(historicalScore.blue_advantage)}%` }"></span>
            </div>
            <div class="red-score">
              <small>{{ selectedHistoricalLineup.red_team_name }}</small>
              <strong>{{ metaScore(historicalScore.red_advantage) }}</strong>
            </div>
          </div>
          <footer>
            <span>{{ t("Lineup model favored") }} <strong>{{ historicalModelFavorite }}</strong></span>
            <small>{{ t("Relative lineup score, not a literal win probability.") }}</small>
          </footer>
        </template>
      </section>
      <section v-else-if="lineupsComplete" class="historical-score-card neutral" aria-live="polite">
        <header>
          <div>
            <span>{{ t("Team-neutral lineup model") }}</span>
            <strong>{{ t("User-selected lineup score") }}</strong>
          </div>
          <small>{{ t("Team preferences excluded") }}</small>
        </header>
        <p v-if="neutralScoreLoading">{{ t("Scoring the selected lineups…") }}</p>
        <p v-else-if="neutralScoreError" class="score-error">{{ neutralScoreError }}</p>
        <template v-else-if="neutralScore">
          <div class="historical-score-values">
            <div class="blue-score">
              <small>{{ t("Blue lineup") }}</small>
              <strong>{{ metaScore(neutralScore.blue_advantage) }}</strong>
            </div>
            <div class="score-track" aria-hidden="true">
              <span :style="{ width: `${metaScore(neutralScore.blue_advantage)}%` }"></span>
            </div>
            <div class="red-score">
              <small>{{ t("Red lineup") }}</small>
              <strong>{{ metaScore(neutralScore.red_advantage) }}</strong>
            </div>
          </div>
          <footer>
            <span>{{ t("Lineup model favored") }} <strong>{{ neutralModelFavorite }}</strong></span>
            <small>{{ t("Relative lineup score, not a literal win probability.") }}</small>
          </footer>
        </template>
      </section>
      <p class="lineup-lane-rule">{{ t("Each lineup must assign every hero to a distinct eligible lane, using the same role check as the BP simulator.") }}</p>
      <p class="lineup-disclaimer">{{ t("These are historical draft associations, not guaranteed in-game counters or win probabilities.") }}</p>
    </div>
  </section>
</template>

<style scoped>
.lineup-analyzer { order:2; margin-top:1.5rem; padding:1.35rem; border:1px solid var(--line); background:linear-gradient(135deg,rgba(255,255,255,.94),rgba(238,244,251,.9)); box-shadow:0 14px 36px rgba(16,42,46,.07); }
.lineup-analyzer>header { display:flex; align-items:end; justify-content:space-between; gap:1.5rem; }
.lineup-eyebrow { margin:0 0 .4rem; color:var(--accent-deep); font-size:.63rem; letter-spacing:.12em; text-transform:uppercase; }
.lineup-analyzer h2 { margin:0; font:800 clamp(1.65rem,3.5vw,2.7rem)/1 var(--display); letter-spacing:-.04em; }
.lineup-analyzer>header p:last-child { max-width:47rem; margin:.55rem 0 0; color:var(--ink-soft); font-size:.72rem; line-height:1.5; }
.lineup-header-actions { display:flex; align-items:center; gap:.4rem; }
.lineup-header-actions button { min-height:38px; padding:.45rem .7rem; border:1px solid var(--line); background:#fff; color:var(--ink-soft); font:700 .65rem var(--display); white-space:nowrap; }
.lineup-header-actions .ultimate-trigger { border-color:var(--ink); background:var(--ink); color:#fff; }
.lineup-header-actions button:disabled { opacity:.45; }
.ultimate-error { margin:.8rem 0 0; padding:.65rem; border:1px solid var(--warn); color:var(--warn); font-size:.65rem; }
.ultimate-results { margin-top:1rem; padding:.8rem; border:1px solid var(--accent-deep); background:rgba(237,248,243,.66); }
.ultimate-results>header { display:flex; align-items:end; justify-content:space-between; gap:1rem; }
.ultimate-results>header span,.ultimate-results>header strong { display:block; }.ultimate-results>header span { color:var(--accent-deep); font-size:.55rem; letter-spacing:.1em; text-transform:uppercase; }.ultimate-results>header strong { margin-top:.15rem; font:800 1rem var(--display); }.ultimate-results>header small { color:var(--ink-soft); font-size:.55rem; }
.ultimate-profile-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.55rem; margin-top:.7rem; }
.ultimate-profile-grid>article { min-width:0; padding:.65rem; border:1px solid var(--line); background:#fff; }.ultimate-profile-grid>article.main_counter { border-color:#b75952; }
.ultimate-profile-heading { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:.5rem; align-items:start; }.ultimate-profile-heading>span { display:grid; width:1.55rem; height:1.55rem; place-items:center; border-radius:50%; background:var(--ink); color:#fff; font:800 .55rem var(--display); }.ultimate-profile-heading h3 { margin:0; font:800 .72rem var(--display); }.ultimate-profile-heading p { margin:.14rem 0 0; color:var(--ink-soft); font-size:.54rem; line-height:1.35; }.ultimate-profile-score { text-align:right; }.ultimate-profile-score strong,.ultimate-profile-score small { display:block; }.ultimate-profile-score strong { color:var(--accent-deep); font:800 1.15rem var(--display); }.ultimate-profile-score small { color:var(--ink-soft); font-size:.45rem; text-transform:uppercase; }
.ultimate-heroes { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:.3rem; margin-top:.6rem; }.ultimate-heroes>div { min-width:0; text-align:center; }.ultimate-heroes img { display:block; width:100%; aspect-ratio:1; object-fit:cover; }.ultimate-heroes span,.ultimate-heroes small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.ultimate-heroes span { margin-top:.18rem; font-size:.56rem; }.ultimate-heroes small { color:var(--ink-soft); font-size:.48rem; }
.ultimate-profile-actions { display:flex; gap:.35rem; margin-top:.55rem; }.ultimate-profile-actions button { min-height:30px; padding:.3rem .45rem; border:1px solid var(--accent-deep); background:#fff; color:var(--accent-deep); font:700 .56rem var(--display); }.ultimate-profile-actions button:last-child:not(:first-child) { background:var(--accent-deep); color:#fff; }
.ultimate-results>p { margin:.65rem 0 0; color:var(--ink-soft); font-size:.53rem; }
.historical-lineup-picker { display:grid; grid-template-columns:minmax(0,1fr) minmax(18rem,32rem); align-items:center; gap:1rem; margin-top:1rem; padding:.7rem .8rem; border:1px solid var(--line); background:rgba(255,255,255,.68); }
.historical-lineup-picker span,.historical-lineup-picker strong,.historical-lineup-picker small { display:block; }
.historical-lineup-picker strong { font:700 .72rem var(--display); }
.historical-lineup-picker small { margin-top:.18rem; color:var(--ink-soft); font-size:.53rem; }
.historical-lineup-dropdown { position:relative; min-width:0; }
.historical-lineup-dropdown>summary { display:grid; min-height:40px; box-sizing:border-box; align-items:center; gap:.35rem; padding:.45rem 1.8rem .45rem .55rem; border:1px solid var(--line); background:#fff; color:var(--ink); font:600 .6rem var(--display); cursor:pointer; list-style:none; }
.historical-lineup-dropdown>summary::-webkit-details-marker { display:none; }
.historical-lineup-dropdown>summary::after { position:absolute; top:.75rem; right:.65rem; content:"⌄"; color:var(--ink-soft); }
.historical-lineup-dropdown[open]>summary::after { content:"⌃"; }
.selected-lineup-preview { display:grid!important; grid-template-columns:1fr auto 1fr; align-items:center; gap:.3rem; }
.selected-lineup-preview>span { display:grid; grid-template-columns:repeat(5,1rem); gap:.1rem; width:max-content; padding:.12rem; }
.selected-lineup-preview>span:last-child { justify-self:end; }
.selected-lineup-preview img { display:block; width:1rem; height:1rem; object-fit:cover; }
.selected-lineup-preview b,.historical-heroes-row>b { color:var(--ink-soft); font-size:.48rem; }
.historical-lineup-options { position:absolute; z-index:20; top:calc(100% + .25rem); right:0; width:min(32rem,calc(100vw - 2rem)); max-height:28rem; overflow-x:hidden; overflow-y:auto; border:1px solid var(--line); background:#fff; box-shadow:0 16px 36px rgba(16,42,46,.18); }
.historical-lineup-options>button { position:relative; display:grid; grid-template-columns:minmax(0,1fr) 2rem minmax(0,1fr); width:100%; gap:.4rem; padding:.55rem; border:0; border-bottom:1px solid var(--line); background:#fff; color:var(--ink); font:inherit; text-align:left; cursor:pointer; }
.historical-lineup-options>button:hover,.historical-lineup-options>button.selected { background:rgba(29,111,91,.08); }
.historical-match-row,.historical-heroes-row { display:contents!important; }
.historical-match-row .blue-name { grid-column:1; grid-row:1; align-self:center; color:#286999; text-align:right; }
.historical-match-row .red-name { grid-column:3; grid-row:1; align-self:center; color:#a84b4b; }
.historical-match-row>b { grid-column:2; grid-row:1; align-self:center; justify-self:center; font-size:.5rem; }
.historical-match-row>small { position:absolute; top:.55rem; right:.55rem; margin:0; white-space:nowrap; }
.historical-heroes-row>b { grid-column:2; grid-row:2; align-self:center; justify-self:center; }
.historical-team-box { display:grid!important; grid-template-columns:repeat(5,1.75rem); gap:.14rem; width:max-content; padding:.2rem; border:1px solid; }
.historical-team-box.blue-box { grid-column:1; grid-row:2; justify-self:end; }
.historical-team-box.red-box { grid-column:3; grid-row:2; justify-self:start; }
.blue-box { border-color:#397fa8!important; background:rgba(57,127,168,.09); }.red-box { border-color:#b75952!important; background:rgba(183,89,82,.09); }
.historical-team-box>span { min-width:0; }.historical-team-box img { display:block; width:100%; aspect-ratio:1; object-fit:cover; }
.lineup-boards { display:grid; grid-template-columns:1fr 1fr; gap:.75rem; margin-top:1.1rem; }
.lineup-side { padding:.7rem; border:1px solid var(--line); background:rgba(255,255,255,.72); }
.lineup-side.blue.active { border-color:#397fa8; box-shadow:inset 0 3px #397fa8; }
.lineup-side.red.active { border-color:#b75952; box-shadow:inset 0 3px #b75952; }
.lineup-side-heading { display:flex; width:100%; justify-content:space-between; padding:0 0 .55rem; border:0; background:transparent; color:var(--ink); font:700 .72rem var(--display); }
.lineup-side-heading small { color:var(--ink-soft); }
.lineup-slots { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:.35rem; }
.lineup-slots>button { position:relative; min-width:0; aspect-ratio:1; padding:0; overflow:hidden; border:1px solid var(--line); background:#fff; color:var(--ink-soft); }
.lineup-slots img { width:100%; height:100%; object-fit:cover; }
.lineup-slots span { position:absolute; right:0; bottom:0; left:0; padding:.15rem; overflow:hidden; background:rgba(16,42,46,.78); color:#fff; font-size:.5rem; text-overflow:ellipsis; white-space:nowrap; }
.lineup-slots .empty-slot { border-style:dashed; font:400 1.25rem var(--display); }
.counter-generator { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin-top:.65rem; padding:.7rem .8rem; border:1px dashed var(--line); background:rgba(255,255,255,.55); }
.counter-generator.ready { border-style:solid; border-color:#b75952; background:rgba(183,89,82,.06); }
.counter-generator span,.counter-generator strong,.counter-generator small { display:block; }
.counter-generator span { color:#b75952; font-size:.52rem; letter-spacing:.09em; text-transform:uppercase; }
.counter-generator strong { margin-top:.12rem; font:700 .72rem var(--display); }
.counter-generator small { margin-top:.18rem; color:var(--ink-soft); font-size:.53rem; }
.counter-generator button { flex:0 0 auto; min-height:38px; padding:.45rem .75rem; border:1px solid #b75952; background:#b75952; color:#fff; font:700 .62rem var(--display); }
.counter-generator button:disabled { opacity:.38; }
.lineup-picker { margin-top:.8rem; padding:.8rem; border:1px solid var(--line); background:rgba(255,255,255,.62); }
.lineup-picker>label { display:grid; grid-template-columns:auto minmax(12rem,24rem); align-items:center; gap:.7rem; }
.lineup-picker>label span { color:var(--ink); font:700 .7rem var(--display); }
.lineup-picker input { box-sizing:border-box; min-height:40px; padding:.5rem .65rem; border:1px solid var(--line); background:#fff; color:var(--ink); font:inherit; }
.lineup-picker>p { margin:.55rem 0 0; color:var(--ink-soft); font-size:.59rem; }
.lineup-hero-options { display:grid; grid-template-columns:repeat(auto-fill,minmax(9.5rem,1fr)); gap:.4rem; max-height:250px; margin-top:.65rem; overflow:auto; }
.lineup-hero-options>button { display:grid; grid-template-columns:2.6rem minmax(0,1fr); align-items:center; gap:.45rem; min-width:0; padding:.3rem; border:1px solid var(--line); background:#fff; color:var(--ink); text-align:left; font:inherit; }
.lineup-hero-options>button:hover:not(:disabled) { border-color:var(--accent-deep); }
.lineup-hero-options>button:disabled { opacity:.45; }
.lineup-hero-options img { width:2.6rem; height:2.6rem; object-fit:cover; }
.lineup-hero-options span,.lineup-hero-options strong,.lineup-hero-options small { display:block; min-width:0; }
.lineup-hero-options strong { overflow:hidden; font-size:.69rem; text-overflow:ellipsis; white-space:nowrap; }
.lineup-hero-options small { margin-top:.14rem; color:var(--ink-soft); font-size:.52rem; }
.lineup-hero-options i { display:inline-block; margin-right:.3rem; color:var(--accent-deep); font-style:normal; }
.lineup-hero-options .lane-conflict { color:var(--warn); }
.lineup-analysis { margin-top:.8rem; }
.relationship-graph-shell { padding:.75rem; border:1px solid var(--line); background:rgba(255,255,255,.74); }
.relationship-legend { display:flex; flex-wrap:wrap; align-items:center; gap:.45rem 1rem; padding-bottom:.6rem; border-bottom:1px solid var(--line); color:var(--ink-soft); font-size:.6rem; }
.relationship-legend span { display:flex; align-items:center; gap:.35rem; }
.relationship-legend i { position:relative; display:block; width:2.1rem; height:3px; background:#3e8e72; }
.relationship-legend .counter-line { background:#397fa8; }
.relationship-legend .counter-line::after { position:absolute; top:-3px; right:-1px; border-width:4px 0 4px 6px; border-style:solid; border-color:transparent transparent transparent #397fa8; content:""; }
.relationship-legend small { margin-left:auto; }
.relationship-graph-scroll { overflow-x:auto; }
.relationship-graph { display:block; width:100%; min-width:740px; height:auto; }
.graph-team-title { font:700 15px var(--display); text-anchor:middle; }.graph-team-title.blue { fill:#397fa8; }.graph-team-title.red { fill:#b75952; }
.relationship-edge { stroke-linecap:round; cursor:pointer; transition:opacity .15s ease, stroke-width .15s ease; }
.relationship-edge.synergy { stroke:#3e8e72; }
.relationship-edge.counter.blue { stroke:#397fa8; }
.relationship-edge.counter.red { stroke:#b75952; }
.relationship-edge:focus,.relationship-edge.focused { outline:none; filter:drop-shadow(0 2px 2px rgba(16,42,46,.22)); }
.relationship-node { pointer-events:none; }
.relationship-node circle { fill:#fff; stroke-width:4; }.relationship-node circle.blue { stroke:#397fa8; }.relationship-node circle.red { stroke:#b75952; }
.relationship-node image { clip-path:circle(22px at center); }
.relationship-node text { fill:var(--ink); font:700 13px var(--display); }.relationship-node .node-lane { fill:var(--ink-soft); font:500 9px var(--display); }
.relationship-focus { display:grid; grid-template-columns:minmax(9rem,1fr) minmax(12rem,2fr) auto auto; gap:.65rem 1rem; align-items:center; margin-top:.4rem; padding:.65rem .75rem; border-left:4px solid #3e8e72; background:#fff; }
.relationship-focus.counter.blue { border-left-color:#397fa8; }.relationship-focus.counter.red { border-left-color:#b75952; }
.relationship-focus>span { color:var(--ink-soft); font-size:.56rem; letter-spacing:.08em; text-transform:uppercase; }.relationship-focus>strong { font:700 .75rem var(--display); }
.relationship-focus>div { text-align:right; }.relationship-focus b,.relationship-focus small { display:block; }.relationship-focus b { font:800 .9rem var(--display); }.relationship-focus small { color:var(--ink-soft); font-size:.5rem; }
.historical-score-card { margin-top:.55rem; padding:.75rem; border:1px solid var(--line); background:#fff; }
.historical-score-card.neutral { border-color:var(--accent-deep); background:rgba(237,248,243,.5); }
.historical-score-card>header { display:flex; align-items:end; justify-content:space-between; gap:1rem; }
.historical-score-card>header span,.historical-score-card>header strong { display:block; }.historical-score-card>header span { color:var(--accent-deep); font-size:.52rem; letter-spacing:.09em; text-transform:uppercase; }.historical-score-card>header strong { margin-top:.14rem; font:800 .82rem var(--display); }.historical-score-card>header small { color:var(--ink-soft); font-size:.52rem; }
.historical-score-card>p { margin:.65rem 0 0; color:var(--ink-soft); font-size:.6rem; }.historical-score-card>p.score-error { color:var(--warn); }
.historical-score-values { display:grid; grid-template-columns:minmax(7rem,1fr) minmax(10rem,2fr) minmax(7rem,1fr); align-items:center; gap:.7rem; margin-top:.75rem; }
.historical-score-values>div:first-child { text-align:right; }.historical-score-values>div:last-child { text-align:left; }.historical-score-values small,.historical-score-values strong { display:block; }.historical-score-values small { color:var(--ink-soft); font-size:.52rem; }.historical-score-values strong { margin-top:.1rem; font:800 1.2rem var(--display); }.blue-score strong { color:#286999; }.red-score strong { color:#a84b4b; }
.score-track { height:.55rem; overflow:hidden; border-radius:999px; background:#b75952; }.score-track span { display:block; height:100%; background:#397fa8; }
.historical-score-card>footer { display:flex; justify-content:space-between; gap:1rem; margin-top:.65rem; padding-top:.55rem; border-top:1px solid var(--line); font-size:.56rem; }.historical-score-card>footer small { color:var(--ink-soft); }
.lineup-empty-analysis { margin:0; padding:.7rem; border:1px dashed var(--line); color:var(--ink-soft); font-size:.65rem; text-align:center; }
.lineup-lane-rule { margin:.65rem 0 0; color:var(--accent-deep); font:700 .58rem var(--display); }
.lineup-disclaimer { margin:.65rem 0 0; color:var(--ink-soft); font-size:.58rem; line-height:1.45; }
button { cursor:pointer; }
@media (max-width:720px) {
  .lineup-analyzer>header { align-items:start; flex-direction:column; }
  .lineup-header-actions { width:100%; }.lineup-header-actions button { flex:1; }
  .ultimate-profile-grid { grid-template-columns:1fr; }
  .historical-lineup-picker { grid-template-columns:1fr; }
  .historical-lineup-options { right:auto; left:0; width:100%; }
  .historical-lineup-options>button { grid-template-columns:minmax(0,1fr) 1.4rem minmax(0,1fr); gap:.2rem; padding:.45rem; }
  .historical-team-box { grid-template-columns:repeat(5,minmax(0,1fr)); gap:.08rem; width:min(100%,7.6rem); box-sizing:border-box; padding:.12rem; }
  .historical-match-row>small { position:static; grid-column:1 / -1; grid-row:3; justify-self:end; margin-top:.1rem; }
  .lineup-boards { grid-template-columns:1fr; }
  .counter-generator { align-items:stretch; flex-direction:column; }
  .lineup-picker>label { grid-template-columns:1fr; gap:.35rem; }
  .lineup-hero-options { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .relationship-focus { grid-template-columns:1fr auto auto; }.relationship-focus>span { grid-column:1/-1; }
  .historical-score-values { grid-template-columns:1fr; }.historical-score-values>div:first-child,.historical-score-values>div:last-child { text-align:left; }.historical-score-card>footer { flex-direction:column; }
}
</style>
