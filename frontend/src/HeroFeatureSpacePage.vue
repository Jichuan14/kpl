<script setup>
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import {
  fetchBattleLineups,
  fetchHeroMatchupRecommendations,
  fetchHeroResponses,
  fetchLearnedFeatureSpace,
  fetchVisualizationSeasons,
} from "./api";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { heroAsset } from "./heroAssets";
import { mechanicLabel } from "./heroMechanicLabels";
import { heroSearchAliases } from "./heroSearchAliases";
import { supplementalHeroes } from "./supplementalHeroes";
import LineupAnalyzerWidget from "./LineupAnalyzerWidget.vue";
import { language, t } from "./i18n";
import { finishStartupLoading } from "./startupLoader";

const WIDTH = 820;
const HEIGHT = 520;
const PADDING = 48;
const INITIAL_HERO_LIMIT = 48;
const INITIAL_MATCHUP_RECOMMENDATION_LIMIT = 6;
const EXPANDED_MATCHUP_RECOMMENDATION_LIMIT = 24;
const ICON_SIZE = 30;
const EDGE_INSET = ICON_SIZE / 2 + 4;
const MAX_ZOOM = 2.5;
const FAVORITE_HERO_STORAGE_KEY = "draft-atlas-favorite-hero-ids";
const LEGACY_FAVORITE_HERO_STORAGE_KEY = "draft-atlas-favorite-hero-id";
const PLAYED_LANE_STORAGE_KEY = "draft-atlas-played-lane";
const MATCHUP_INSTRUCTIONS_SEEN_KEY = "draft-atlas-matchup-instructions-seen";
const MAX_FAVORITE_HEROES = 12;
const playableLanes = ["clash", "mid", "jungle", "farm", "roam"];

const seasons = ref([]);
const leagueId = selectedLeagueId;
const payload = shallowRef(null);
const responses = shallowRef(null);
const historicalLineups = shallowRef([]);
const loading = ref(false);
const error = ref("");
const selectedHeroId = ref(null);
const hoveredHeroId = ref(null);
const showAllHeroes = ref(false);
const favoriteHeroIds = ref([]);
const favoriteSearch = ref("");
const preferredLane = ref("");
const opponentHeroIds = ref([]);
const opponentSearch = ref("");
const matchupResult = shallowRef(null);
const matchupLoading = ref(false);
const matchupError = ref("");
const matchupRecommendationsExpanded = ref(false);
const showMatchupInstructions = ref(
  window.localStorage.getItem(MATCHUP_INSTRUCTIONS_SEEN_KEY) !== "true"
);
const zoom = ref(1);
const pan = ref({ x: 0, y: 0 });
const dragState = ref(null);
const activePointers = new Map();
let pinchState = null;
let requestController = null;
let matchupRequestNumber = 0;

const laneLabels = {
  clash: "Clash",
  mid: "Mid",
  jungle: "Jungle",
  farm: "Farm",
  roam: "Roam",
  unknown: "Unknown",
};

const rows = computed(() => payload.value?.rows || []);
const rankedRows = computed(() =>
  [...rows.value].sort(
    (a, b) =>
      Number(b.weighted_bp_action_count || 0) - Number(a.weighted_bp_action_count || 0) ||
      Number(b.bp_action_count || 0) - Number(a.bp_action_count || 0) ||
      a.hero_name.localeCompare(b.hero_name, language.value)
  )
);
const pickerHeroes = computed(() => {
  const heroesById = new Map(
    supplementalHeroes.map((hero) => [Number(hero.hero_id), hero])
  );
  for (const hero of rankedRows.value) {
    heroesById.set(Number(hero.hero_id), hero);
  }
  return [...heroesById.values()];
});
const supportedHeroIds = computed(() => new Set(rows.value.map((hero) => Number(hero.hero_id))));
const visibleRows = computed(() =>
  showAllHeroes.value ? rankedRows.value : rankedRows.value.slice(0, INITIAL_HERO_LIMIT)
);
const selectedHero = computed(
  () => rows.value.find((row) => Number(row.hero_id) === Number(selectedHeroId.value)) || rows.value[0] || null
);
const selectedNeighbors = computed(() => {
  if (!selectedHero.value) return [];
  const byId = new Map(rows.value.map((row) => [Number(row.hero_id), row]));
  return (selectedHero.value.nearest_hero_ids || []).map((heroId) => byId.get(Number(heroId))).filter(Boolean);
});
const selectedMechanics = computed(() =>
  (selectedHero.value?.gameplay_mechanic_keys || []).slice(0, 8).map(mechanicLabel)
);
const selectedFavorites = computed(() => {
  const byId = new Map(pickerHeroes.value.map((row) => [Number(row.hero_id), row]));
  return favoriteHeroIds.value.map((heroId) => byId.get(Number(heroId))).filter(Boolean);
});
function fuzzyHeroScore(hero, query) {
  const needle = query.trim().toLocaleLowerCase().replaceAll(/\s+/g, "");
  if (!needle) return 0;
  const name = String(hero.hero_name || "").toLocaleLowerCase();
  const aliases = `${heroSearchAliases[hero.hero_name] || ""} ${hero.search_aliases || ""}`;
  let best = Number.POSITIVE_INFINITY;
  for (const term of `${name} ${aliases}`.split(/\s+/)) {
    if (term === needle) best = Math.min(best, 0);
    else if (term.includes(needle)) best = Math.min(best, 1 + term.indexOf(needle) / 100);
    let cursor = 0;
    for (const character of term) {
      if (character === needle[cursor]) cursor += 1;
      if (cursor === needle.length) best = Math.min(best, 10 + (term.length - needle.length) / 100);
    }
  }
  return best;
}
function fuzzyHeroOptions(heroes, query) {
  return heroes
    .map((hero) => ({ hero, score: fuzzyHeroScore(hero, query) }))
    .filter((item) => Number.isFinite(item.score))
    .sort((a, b) => a.score - b.score)
    .map((item) => item.hero);
}
const favoriteOptions = computed(() => {
  const selected = new Set(favoriteHeroIds.value.map(Number));
  const opponents = new Set(opponentHeroIds.value.map(Number));
  return fuzzyHeroOptions(pickerHeroes.value.filter(
    (hero) =>
      !selected.has(Number(hero.hero_id)) &&
      !opponents.has(Number(hero.hero_id))
  ), favoriteSearch.value);
});
const selectedOpponents = computed(() => {
  const byId = new Map(pickerHeroes.value.map((row) => [Number(row.hero_id), row]));
  return opponentHeroIds.value.map((heroId) => byId.get(Number(heroId))).filter(Boolean);
});
const opponentOptions = computed(() => {
  const selected = new Set(opponentHeroIds.value.map(Number));
  return fuzzyHeroOptions(pickerHeroes.value.filter(
    (hero) =>
      !favoriteHeroIds.value.includes(Number(hero.hero_id)) &&
      !selected.has(Number(hero.hero_id))
  ), opponentSearch.value);
});
const matchupRecommendations = computed(() => matchupResult.value?.recommendations || []);
const canExpandMatchupRecommendations = computed(
  () => Number(matchupResult.value?.methodology?.candidate_count || 0) > matchupRecommendations.value.length
);
const responseGroups = computed(() => {
  const heroId = Number(selectedHero.value?.hero_id);
  const patternRows = responses.value?.rows || [];
  const topRows = (relation) => {
    const matches = patternRows.filter(
      (row) =>
        row.relation === relation &&
        row.context_level === "overall" &&
        !row.is_peak_battle &&
        Number(row.source_hero_id) === heroId &&
        Number(row.target_hero_id) !== heroId
    );
    const supported = matches.filter((row) => Number(row.selections || 0) >= 3);
    const uniqueTargets = new Map();
    for (const row of supported.length ? supported : matches) {
      const existing = uniqueTargets.get(Number(row.target_hero_id));
      if (
        !existing ||
        Number(row.smoothed_lift || 0) > Number(existing.smoothed_lift || 0) ||
        (Number(row.smoothed_lift || 0) === Number(existing.smoothed_lift || 0) &&
          Number(row.selections || 0) > Number(existing.selections || 0))
      ) {
        uniqueTargets.set(Number(row.target_hero_id), row);
      }
    }
    return [...uniqueTargets.values()]
      .sort(
        (a, b) =>
          Number(b.smoothed_lift || 0) - Number(a.smoothed_lift || 0) ||
          Number(b.smoothed_probability || 0) - Number(a.smoothed_probability || 0) ||
          Number(b.selections || 0) - Number(a.selections || 0)
      )
      .slice(0, 3);
  };
  return [
    { title: "Best partners", rows: topRows("pick_synergy") },
    { title: "Countered by", rows: topRows("counter_pick") },
    { title: "Opponent bans next", rows: topRows("counter_ban") },
  ];
});
const laneCounts = computed(() =>
  visibleRows.value.reduce((counts, row) => {
    const lane = row.primary_lane || "unknown";
    counts[lane] = (counts[lane] || 0) + 1;
    return counts;
  }, {})
);
const viewportTransform = computed(() => {
  const centerX = WIDTH / 2;
  const centerY = HEIGHT / 2;
  return `translate(${pan.value.x} ${pan.value.y}) translate(${centerX} ${centerY}) scale(${zoom.value}) translate(${-centerX} ${-centerY})`;
});
const bounds = computed(() => {
  const xs = rows.value.map((row) => Number(row.x));
  const ys = rows.value.map((row) => Number(row.y));
  const minX = Math.min(...xs, -1);
  const maxX = Math.max(...xs, 1);
  const minY = Math.min(...ys, -1);
  const maxY = Math.max(...ys, 1);
  return { minX, maxX, minY, maxY };
});
function projectX(value) {
  const range = bounds.value.maxX - bounds.value.minX || 1;
  const edge = PADDING + EDGE_INSET;
  return edge + ((Number(value) - bounds.value.minX) / range) * (WIDTH - edge * 2);
}

function projectY(value) {
  const range = bounds.value.maxY - bounds.value.minY || 1;
  const edge = PADDING + EDGE_INSET;
  return HEIGHT - edge - ((Number(value) - bounds.value.minY) / range) * (HEIGHT - edge * 2);
}

function frameSize() {
  return ICON_SIZE / zoom.value;
}

function iconSize() {
  return (ICON_SIZE - 4) / zoom.value;
}

function heroIcon(hero) {
  return hero?.catalog_filler ? "" : heroAsset(hero?.hero_id);
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(0)}%`;
}

function selectHero(heroId) {
  selectedHeroId.value = Number(heroId);
  if (!showAllHeroes.value && !visibleRows.value.some((row) => Number(row.hero_id) === Number(heroId))) {
    showAllHeroes.value = true;
  }
}

function persistFavorites() {
  try {
    window.localStorage.setItem(
      FAVORITE_HERO_STORAGE_KEY,
      JSON.stringify(favoriteHeroIds.value.map(Number))
    );
  } catch {
    // Private browsing can disable persistent storage; the current pool still works.
  }
}

function dismissMatchupInstructions() {
  showMatchupInstructions.value = false;
  try {
    window.localStorage.setItem(MATCHUP_INSTRUCTIONS_SEEN_KEY, "true");
  } catch {
    // The hint stays hidden for this visit if persistent storage is unavailable.
  }
}

function selectPreferredLane(lane) {
  preferredLane.value = preferredLane.value === lane ? "" : lane;
  try {
    window.localStorage.setItem(PLAYED_LANE_STORAGE_KEY, preferredLane.value);
  } catch {
    // The selected lane remains available for this session when storage is blocked.
  }
  matchupResult.value = null;
  matchupError.value = "";
  matchupRecommendationsExpanded.value = false;
  if (opponentHeroIds.value.length && !matchupLoading.value) {
    void recommendForMatchup();
  }
}

function addFavorite(heroId) {
  const id = Number(heroId);
  if (
    !id ||
    favoriteHeroIds.value.length >= MAX_FAVORITE_HEROES ||
    favoriteHeroIds.value.includes(id)
  ) return;
  favoriteHeroIds.value = [...favoriteHeroIds.value, id];
  favoriteSearch.value = "";
  opponentHeroIds.value = opponentHeroIds.value.filter(
    (opponentId) => Number(opponentId) !== id
  );
  persistFavorites();
  matchupResult.value = null;
  matchupError.value = "";
  matchupRecommendationsExpanded.value = false;
}

function removeFavorite(heroId) {
  favoriteHeroIds.value = favoriteHeroIds.value.filter(
    (id) => Number(id) !== Number(heroId)
  );
  persistFavorites();
  matchupResult.value = null;
  matchupError.value = "";
  matchupRecommendationsExpanded.value = false;
}

function addOpponent(heroId) {
  const id = Number(heroId);
  if (!id || opponentHeroIds.value.length >= 5 || opponentHeroIds.value.includes(id)) return;
  opponentHeroIds.value = [...opponentHeroIds.value, id];
  opponentSearch.value = "";
  matchupResult.value = null;
  matchupRecommendationsExpanded.value = false;
}

function removeOpponent(heroId) {
  opponentHeroIds.value = opponentHeroIds.value.filter(
    (id) => Number(id) !== Number(heroId)
  );
  matchupResult.value = null;
  matchupRecommendationsExpanded.value = false;
}

async function recommendForMatchup(limit = INITIAL_MATCHUP_RECOMMENDATION_LIMIT) {
  if (!opponentHeroIds.value.length || matchupLoading.value) return;
  const requestNumber = ++matchupRequestNumber;
  matchupLoading.value = true;
  matchupError.value = "";
  try {
    const supportedFavorites = favoriteHeroIds.value.filter((heroId) =>
      supportedHeroIds.value.has(Number(heroId))
    );
    const supportedOpponents = opponentHeroIds.value.filter((heroId) =>
      supportedHeroIds.value.has(Number(heroId))
    );
    if (!supportedOpponents.length) {
      matchupError.value = t("The selected heroes do not yet have professional BP data.");
      return;
    }
    const request = {
      league_id: leagueId.value,
      favorite_hero_ids: supportedFavorites.map(Number),
      opponent_hero_ids: supportedOpponents.map(Number),
      preferred_lane: preferredLane.value || null,
    };
    // Older running API processes do not yet accept `limit`. Keep the normal
    // recommendation flow usable until that process is refreshed.
    const result = limit === INITIAL_MATCHUP_RECOMMENDATION_LIMIT
      ? await fetchHeroMatchupRecommendations(request)
      : await fetchHeroMatchupRecommendations({ ...request, limit }).catch(async (err) => {
          if (err.status !== 422) throw err;
          matchupRecommendationsExpanded.value = false;
          return fetchHeroMatchupRecommendations(request);
        });
    if (requestNumber === matchupRequestNumber) matchupResult.value = result;
  } catch (err) {
    if (requestNumber === matchupRequestNumber) {
      matchupResult.value = null;
      matchupError.value = err.message || t("Could not calculate hero recommendations.");
    }
  } finally {
    if (requestNumber === matchupRequestNumber) matchupLoading.value = false;
  }
}

function toggleMatchupRecommendations() {
  matchupRecommendationsExpanded.value = !matchupRecommendationsExpanded.value;
  void recommendForMatchup(
    matchupRecommendationsExpanded.value
      ? EXPANDED_MATCHUP_RECOMMENDATION_LIMIT
      : INITIAL_MATCHUP_RECOMMENDATION_LIMIT
  );
}

function recommendationNote(row) {
  if (row.supported_opponents === row.opponent_count) return t("Supported against every selected opponent");
  if (row.supported_opponents > 0) {
    return t("Supported against {count} selected opponents").replace(
      "{count}", row.supported_opponents
    );
  }
  return t("Limited direct matchup evidence");
}

function resetView() {
  zoom.value = 1;
  pan.value = { x: 0, y: 0 };
}

function clampPan(nextPan) {
  const maxX = ((WIDTH - PADDING * 2) * (zoom.value - 1)) / 2;
  const maxY = ((HEIGHT - PADDING * 2) * (zoom.value - 1)) / 2;
  return {
    x: Math.min(maxX, Math.max(-maxX, nextPan.x)),
    y: Math.min(maxY, Math.max(-maxY, nextPan.y)),
  };
}

function changeZoom(amount) {
  zoom.value = Math.min(MAX_ZOOM, Math.max(1, Number((zoom.value + amount).toFixed(2))));
  pan.value = clampPan(pan.value);
}

function zoomWithWheel(event) {
  changeZoom(event.deltaY < 0 ? 0.25 : -0.25);
}

function toggleHeroDensity() {
  showAllHeroes.value = !showAllHeroes.value;
  resetView();
}

function pointerPosition(event) {
  const box = event.currentTarget.getBoundingClientRect();
  return {
    x: ((event.clientX - box.left) / box.width) * WIDTH,
    y: ((event.clientY - box.top) / box.height) * HEIGHT,
  };
}

function startPan(event) {
  const position = pointerPosition(event);
  activePointers.set(event.pointerId, position);
  if (activePointers.size === 2) {
    const [first, second] = [...activePointers.values()];
    pinchState = {
      distance: Math.hypot(second.x - first.x, second.y - first.y),
      zoom: zoom.value,
    };
    dragState.value = null;
    event.currentTarget.setPointerCapture(event.pointerId);
    return;
  }
  dragState.value = { pointerId: event.pointerId, ...position };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function panMap(event) {
  const position = pointerPosition(event);
  if (activePointers.has(event.pointerId)) activePointers.set(event.pointerId, position);
  if (pinchState && activePointers.size >= 2) {
    const [first, second] = [...activePointers.values()];
    const distance = Math.hypot(second.x - first.x, second.y - first.y);
    const nextZoom = Math.min(
      MAX_ZOOM,
      Math.max(1, pinchState.zoom * (distance / pinchState.distance))
    );
    zoom.value = Number(nextZoom.toFixed(2));
    pan.value = clampPan(pan.value);
    return;
  }
  if (!dragState.value || dragState.value.pointerId !== event.pointerId) return;
  pan.value = clampPan({
    x: pan.value.x + position.x - dragState.value.x,
    y: pan.value.y + position.y - dragState.value.y,
  });
  dragState.value = { pointerId: event.pointerId, ...position };
}

function endPan(event) {
  activePointers.delete(event.pointerId);
  if (activePointers.size < 2) pinchState = null;
  if (dragState.value?.pointerId === event.pointerId) dragState.value = null;
  if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
}

function laneLabel(lane) {
  return t(laneLabels[lane] || laneLabels.unknown);
}

async function loadSeasons() {
  seasons.value = (await fetchVisualizationSeasons()) || [];
  selectAvailableLeague(seasons.value);
}

async function loadFeatureSpace() {
  if (!leagueId.value) return;
  requestController?.abort();
  const controller = new AbortController();
  requestController = controller;
  loading.value = true;
  error.value = "";
  payload.value = null;
  responses.value = null;
  historicalLineups.value = [];
  try {
    const [featureSpace, responseData, historicalData] = await Promise.all([
      fetchLearnedFeatureSpace(leagueId.value),
      fetchHeroResponses(leagueId.value, { signal: controller.signal }).catch(() => null),
      fetchBattleLineups(leagueId.value, { signal: controller.signal }).catch(() => null),
    ]);
    if (requestController !== controller) return;
    payload.value = featureSpace;
    responses.value = responseData;
    historicalLineups.value = historicalData?.battles || [];
    const requestedHeroId = Number(new URLSearchParams(window.location.search).get("hero"));
    selectedHeroId.value = rows.value.some((row) => Number(row.hero_id) === requestedHeroId)
      ? requestedHeroId
      : rankedRows.value[0]?.hero_id || null;
    let storedFavoriteIds = [];
    let hasStoredFavoritePool = false;
    try {
      const storedValue = window.localStorage.getItem(FAVORITE_HERO_STORAGE_KEY);
      hasStoredFavoritePool = storedValue !== null;
      const stored = JSON.parse(storedValue || "[]");
      if (Array.isArray(stored)) storedFavoriteIds = stored.map(Number);
      if (!hasStoredFavoritePool && !storedFavoriteIds.length) {
        const legacy = Number(
          window.localStorage.getItem(LEGACY_FAVORITE_HERO_STORAGE_KEY)
        );
        if (legacy) storedFavoriteIds = [legacy];
      }
    } catch {
      storedFavoriteIds = [];
    }
    const availableIds = new Set(pickerHeroes.value.map((row) => Number(row.hero_id)));
    favoriteHeroIds.value = [...new Set(storedFavoriteIds)]
      .filter((heroId) => availableIds.has(heroId))
      .slice(0, MAX_FAVORITE_HEROES);
    try {
      const storedLane = window.localStorage.getItem(PLAYED_LANE_STORAGE_KEY) || "";
      preferredLane.value = favoriteHeroIds.value.length && playableLanes.includes(storedLane)
        ? storedLane
        : "";
    } catch {
      preferredLane.value = "";
    }
    persistFavorites();
    opponentHeroIds.value = [];
    matchupResult.value = null;
    matchupError.value = "";
    showAllHeroes.value = false;
    resetView();
  } catch (err) {
    if (requestController !== controller) return;
    if (err.name === "AbortError") return;
    error.value = t("No learned feature space is available for this season. Train the learnable model first.");
  } finally {
    if (requestController === controller) loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadFeatureSpace();
  } finally {
    finishStartupLoading();
  }
});

watch(leagueId, () => {
  matchupRequestNumber += 1;
  loadFeatureSpace();
});
onBeforeUnmount(() => requestController?.abort());
</script>

<template>
  <main class="feature-space-page">
    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="loading" class="message">{{ t("Loading learned feature space…") }}</p>

    <template v-else-if="payload">
      <LineupAnalyzerWidget
        :league-id="leagueId"
        :heroes="pickerHeroes"
        :response-rows="responses?.rows || []"
        :historical-lineups="historicalLineups"
      />

      <details class="feature-space-deep-dive">
        <summary>
          <span>
            <strong>{{ t("Explore the hero feature space") }}</strong>
            <small>{{ t("Open the learned map, hero similarities, and historical BP reactions.") }}</small>
          </span>
          <b aria-hidden="true">+</b>
        </summary>
        <div class="deep-dive-content">
      <section class="legend" :aria-label="t('Lane legend')">
        <span class="map-reading"><strong>{{ t("How to read the map") }}</strong>{{ t("Nearby icons mean the model treats those heroes as more similar. The layout directions have no fixed gameplay meaning.") }}</span>
        <span v-for="lane in Object.keys(laneLabels)" :key="lane" :class="['legend-item', lane]">
          <i></i>{{ laneLabel(lane) }} <small>{{ laneCounts[lane] || 0 }}</small>
        </span>
      </section>

      <section class="space-layout">
        <div class="space-plot-wrap">
          <div class="map-controls">
            <span>{{ visibleRows.length }} / {{ rows.length }} {{ t("heroes shown") }}</span>
            <button type="button" @click="toggleHeroDensity">{{ showAllHeroes ? t("Show frequent heroes") : t("Show all heroes") }}</button>
            <button type="button" :disabled="zoom <= 1" :aria-label="t('Zoom out')" @click="changeZoom(-0.25)">−</button>
            <button type="button" :disabled="zoom >= MAX_ZOOM" :aria-label="t('Zoom in')" @click="changeZoom(0.25)">+</button>
            <button type="button" :disabled="zoom === 1 && pan.x === 0 && pan.y === 0" @click="resetView">{{ t("Reset view") }}</button>
          </div>
          <svg
            class="space-plot"
            :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
            role="img"
            :aria-label="t('Interactive learned hero feature-space scatter plot')"
            @pointerdown="startPan"
            @pointermove="panMap"
            @pointerup="endPan"
            @pointercancel="endPan"
            @wheel="zoomWithWheel"
          >
            <defs>
              <clipPath id="feature-space-frame">
                <rect :x="PADDING" :y="PADDING" :width="WIDTH - PADDING * 2" :height="HEIGHT - PADDING * 2" />
              </clipPath>
            </defs>
            <g clip-path="url(#feature-space-frame)">
              <g :transform="viewportTransform">
              <g v-for="hero in visibleRows" :key="hero.hero_id">
              <rect
                :x="projectX(hero.x) - frameSize() / 2"
                :y="projectY(hero.y) - frameSize() / 2"
                :width="frameSize()"
                :height="frameSize()"
                :rx="4 / zoom"
                :class="['hero-icon-frame', hero.primary_lane || 'unknown', { selected: Number(hero.hero_id) === Number(selectedHero?.hero_id), hovered: Number(hero.hero_id) === Number(hoveredHeroId) }]"
                tabindex="0"
                :aria-label="`${hero.hero_name} · ${laneLabel(hero.primary_lane)}`"
                @pointerdown.stop
                @click="selectHero(hero.hero_id)"
                @keydown.enter.prevent="selectHero(hero.hero_id)"
                @keydown.space.prevent="selectHero(hero.hero_id)"
                @focus="hoveredHeroId = hero.hero_id"
                @blur="hoveredHeroId = null"
                @mouseenter="hoveredHeroId = hero.hero_id"
                @mouseleave="hoveredHeroId = null"
              />
              <image
                v-if="heroIcon(hero)"
                :x="projectX(hero.x) - iconSize() / 2"
                :y="projectY(hero.y) - iconSize() / 2"
                :width="iconSize()"
                :height="iconSize()"
                :href="heroIcon(hero)"
                preserveAspectRatio="xMidYMid slice"
                class="hero-icon"
              />
              <text v-else :x="projectX(hero.x)" :y="projectY(hero.y) + 4 / zoom" class="hero-icon-fallback" :style="{ fontSize: `${12 / zoom}px` }">{{ hero.hero_name.slice(0, 1) }}</text>
              </g>
              </g>
            </g>
          </svg>
          <p class="plot-note">{{ showAllHeroes ? t("All heroes are visible. Drag the map or use the zoom controls to inspect dense areas.") : t("The initial view keeps only the most frequent BP heroes. Show all heroes or zoom and drag to explore.") }}</p>
        </div>

        <aside v-if="selectedHero" class="hero-detail">
          <div class="selected-hero">
            <img v-if="heroIcon(selectedHero)" :src="heroIcon(selectedHero)" :alt="selectedHero.hero_name" />
            <div>
              <p class="feature-space-eyebrow">{{ laneLabel(selectedHero.primary_lane) }}</p>
              <h2>{{ selectedHero.hero_name }}</h2>
              <p>{{ selectedHero.feature_known ? t("Gameplay feature profile available") : t("Gameplay feature profile unavailable") }}</p>
            </div>
          </div>
          <dl>
            <div><dt>{{ t("Damage") }}</dt><dd>{{ selectedHero.damage_types?.join(" · ") || t("Unknown") }}</dd></div>
            <div v-if="selectedMechanics.length"><dt>{{ t("Gameplay mechanics") }}</dt><dd class="mechanic-list"><span v-for="mechanic in selectedMechanics" :key="mechanic.key">{{ mechanic.label }}</span></dd></div>
            <div><dt>{{ t("Nearest learned heroes") }}</dt><dd>{{ t("Five closest points in the 16-D learned candidate space.") }}</dd></div>
          </dl>
          <div class="neighbor-list">
            <button v-for="hero in selectedNeighbors" :key="hero.hero_id" type="button" @click="selectHero(hero.hero_id)">
              <img v-if="heroIcon(hero)" :src="heroIcon(hero)" :alt="hero.hero_name" />
              <span>{{ hero.hero_name }}</span>
              <small>{{ laneLabel(hero.primary_lane) }}</small>
            </button>
          </div>
        </aside>
      </section>

      <section v-if="selectedHero" class="hero-response-section" aria-labelledby="hero-response-heading">
        <header class="hero-response-header">
          <div>
            <p class="feature-space-eyebrow">{{ t("Hero draft response") }}</p>
            <h2 id="hero-response-heading">{{ t("What follows this pick?") }}</h2>
            <p>{{ t("Choose a hero to see the historical teammates and opponent reactions that followed its pick.") }}</p>
          </div>
          <label class="hero-response-picker">
            <span>{{ t("Select hero") }}</span>
            <div>
              <img v-if="heroIcon(selectedHero)" :src="heroIcon(selectedHero)" :alt="selectedHero.hero_name" />
              <select v-model="selectedHeroId" @change="selectHero(selectedHeroId)">
                <option v-for="hero in rankedRows" :key="hero.hero_id" :value="hero.hero_id">{{ hero.hero_name }}</option>
              </select>
            </div>
          </label>
        </header>
        <div class="hero-response-grid">
          <article v-for="group in responseGroups" :key="group.title" class="hero-response-card">
            <h3>{{ t(group.title) }}</h3>
            <div v-if="group.rows.length" class="response-icon-list">
              <button
                v-for="row in group.rows"
                :key="`${group.title}-${row.target_hero_id}`"
                type="button"
                :title="`${row.target_hero_name} · ${t('Historical chance')} ${percent(row.smoothed_probability)}`"
                :aria-label="`${row.target_hero_name} · ${t('Historical chance')} ${percent(row.smoothed_probability)}`"
                @click="selectHero(row.target_hero_id)"
              >
                <img :src="heroAsset(row.target_hero_id)" :alt="row.target_hero_name" />
                <small>{{ percent(row.smoothed_probability) }}</small>
              </button>
            </div>
            <p v-else class="response-empty">{{ t("No supported pattern") }}</p>
          </article>
        </div>
      </section>
        </div>
      </details>

      <section class="matchup-lab" aria-labelledby="matchup-lab-heading">
        <header class="matchup-lab-header">
          <div>
            <p class="feature-space-eyebrow">{{ t("Favorite hero matchup lab") }}</p>
            <h1 id="matchup-lab-heading">{{ t("What should I play into their heroes?") }}</h1>
            <p>{{ t("Build a saved pool of heroes you enjoy, add the opponent picks you can see, and get a ranked shortlist for the positions you actually play.") }}</p>
          </div>
          <label class="season-picker">
            <span>{{ t("Competition") }}</span>
            <select v-model="leagueId" :disabled="loading">
              <option v-for="season in seasons" :key="season.league_id" :value="season.league_id">
                {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
              </option>
            </select>
            <small>{{ t("Historical draft evidence · Favorite-style similarity") }}</small>
          </label>
        </header>

        <aside class="bp-reference-note">
          <span aria-hidden="true">BP</span>
          <div>
            <strong>{{ t("Use this as a draft reference—not a promise of a counter.") }}</strong>
            <p>{{ t("Recommendations come from professional match bans and picks. Ranked games, patches, team composition, and personal skill can behave very differently, so use the result as a useful second opinion.") }}</p>
          </div>
        </aside>

        <section v-if="showMatchupInstructions" class="matchup-onboarding" :aria-label="t('How to get a recommendation')">
          <ol class="matchup-steps">
            <li><b>1</b><span><strong>{{ t("Add your hero pool") }}</strong><small>{{ t("Optional—leave it empty to consider every hero.") }}</small></span></li>
            <li><b>2</b><span><strong>{{ t("Add their visible picks") }}</strong><small>{{ t("One to five opponent heroes.") }}</small></span></li>
            <li><b>3</b><span><strong>{{ t("Choose your lane") }}</strong><small>{{ t("Optional—use all lanes for a broad answer.") }}</small></span></li>
          </ol>
          <button type="button" class="dismiss-onboarding" @click="dismissMatchupInstructions">{{ t("Got it") }}</button>
        </section>

        <div class="matchup-builder">
          <div class="favorite-picker">
            <span>{{ t("1 · My favorite heroes (optional)") }} · {{ favoriteHeroIds.length }}/{{ MAX_FAVORITE_HEROES }}</span>
            <div class="opponent-search-wrap">
              <input
                v-model="favoriteSearch"
                type="search"
                :disabled="favoriteHeroIds.length >= MAX_FAVORITE_HEROES"
                :placeholder="t('Search a favorite hero…')"
              />
              <div v-if="favoriteSearch && favoriteOptions.length" class="opponent-options">
                <button
                  v-for="hero in favoriteOptions.slice(0, 8)"
                  :key="hero.hero_id"
                  type="button"
                  @click="addFavorite(hero.hero_id)"
                >
                  <img v-if="heroIcon(hero)" :src="heroIcon(hero)" :alt="hero.hero_name" />
                  <span>{{ hero.hero_name }}</span>
                  <small>{{ hero.catalog_filler ? t("No professional BP data") : laneLabel(hero.primary_lane) }}</small>
                </button>
              </div>
            </div>
            <div v-if="selectedFavorites.length" class="favorite-chips">
              <button
                v-for="hero in selectedFavorites"
                :key="hero.hero_id"
                type="button"
                :aria-label="`${t('Remove')} ${hero.hero_name}`"
                @click="removeFavorite(hero.hero_id)"
              >
                <img v-if="heroIcon(hero)" :src="heroIcon(hero)" :alt="hero.hero_name" />
                <span>{{ hero.hero_name }}</span>
                <b>×</b>
              </button>
            </div>
            <p v-else class="favorite-empty">{{ t("Leave this empty to consider every hero, or add the heroes you enjoy.") }}</p>
          </div>

          <div class="opponent-picker">
            <span>{{ t("2 · Opponent picks") }} · {{ opponentHeroIds.length }}/5</span>
            <div class="opponent-search-wrap">
              <input
                v-model="opponentSearch"
                type="search"
                :disabled="opponentHeroIds.length >= 5"
                :placeholder="t('Search an opponent hero…')"
              />
              <div v-if="opponentSearch && opponentOptions.length" class="opponent-options">
                <button
                  v-for="hero in opponentOptions.slice(0, 8)"
                  :key="hero.hero_id"
                  type="button"
                  @click="addOpponent(hero.hero_id)"
                >
                  <img v-if="heroIcon(hero)" :src="heroIcon(hero)" :alt="hero.hero_name" />
                  <span>{{ hero.hero_name }}</span>
                  <small>{{ hero.catalog_filler ? t("No professional BP data") : laneLabel(hero.primary_lane) }}</small>
                </button>
              </div>
            </div>
            <div v-if="selectedOpponents.length" class="opponent-chips">
              <button
                v-for="hero in selectedOpponents"
                :key="hero.hero_id"
                type="button"
                :aria-label="`${t('Remove')} ${hero.hero_name}`"
                @click="removeOpponent(hero.hero_id)"
              >
                <img v-if="heroIcon(hero)" :src="heroIcon(hero)" :alt="hero.hero_name" />
                <span>{{ hero.hero_name }}</span>
                <b>×</b>
              </button>
            </div>
          </div>

          <div class="matchup-actions">
            <div class="lane-picker" role="group" :aria-label="t('Lane I am playing')">
              <span>{{ t("3 · Lane I am playing (optional)") }}</span>
              <div>
                <button
                  type="button"
                  :class="{ active: !preferredLane }"
                  :aria-pressed="!preferredLane"
                  @click="selectPreferredLane('')"
                >{{ favoriteHeroIds.length ? t("All favorite lanes") : t("All lanes") }}</button>
                <button
                  v-for="lane in playableLanes"
                  :key="lane"
                  type="button"
                  :class="{ active: preferredLane === lane }"
                  :aria-pressed="preferredLane === lane"
                  @click="selectPreferredLane(lane)"
                >{{ laneLabel(lane) }}</button>
              </div>
            </div>
            <button
              class="matchup-submit"
              type="button"
              :disabled="!opponentHeroIds.length || matchupLoading"
              @click="recommendForMatchup"
            >
              {{ matchupLoading ? t("Calculating…") : t("Recommend heroes") }}
            </button>
          </div>
          <p v-if="[...selectedFavorites, ...selectedOpponents].some((hero) => hero.catalog_filler)" class="matchup-data-note">
            {{ t("Placeholder heroes are searchable and saved, but are excluded from recommendations until professional BP data is available.") }}
          </p>
        </div>

        <p v-if="matchupError" class="matchup-message error">{{ matchupError }}</p>
        <p v-else-if="!matchupResult" class="matchup-message">
          {{ t("Add at least one opponent pick to see recommendations.") }}
        </p>

        <template v-else>
          <div class="matchup-summary">
            <div>
              <span>{{ matchupResult.methodology.uses_favorite_pool ? t("Favorite pool status") : t("Recommendation scope") }}</span>
              <div class="favorite-ranks" data-i18n-ignore>
                <strong v-for="favorite in matchupResult.favorites" :key="favorite.hero_id">
                  {{ favorite.hero_name }} · #{{ favorite.rank }} / {{ favorite.candidate_count }}
                </strong>
              </div>
            </div>
            <p>
              {{ !matchupResult.methodology.uses_favorite_pool
                ? t("No favorite pool selected: recommendations consider every hero.")
                : matchupResult.methodology.selected_lane
                ? t("Recommendations are filtered to {lane}.").replace("{lane}", laneLabel(matchupResult.methodology.selected_lane))
                : matchupResult.methodology.lane_constraints.length
                ? t("Recommendations cover the positions represented in your favorite pool.")
                : t("Your favorites have no stable position in the current data, so recommendations can include every role.") }}
            </p>
          </div>

          <div class="matchup-results">
            <article
              v-for="hero in matchupRecommendations"
              :key="hero.hero_id"
              :class="{ favorite: hero.is_favorite }"
            >
              <span class="matchup-rank">#{{ hero.rank }}</span>
              <button type="button" class="matchup-hero" @click="selectHero(hero.hero_id)">
                <img v-if="heroAsset(hero.hero_id)" :src="heroAsset(hero.hero_id)" :alt="hero.hero_name" />
                <span>
                  <strong>{{ hero.hero_name }}</strong>
                  <small>{{ laneLabel(hero.primary_lane) }}<template v-if="hero.is_favorite"> · {{ t("Your favorite") }}</template></small>
                </span>
              </button>
              <div class="matchup-score">
                <strong>{{ Number(hero.score).toFixed(1) }}</strong>
                <span>{{ t("fit score") }}</span>
              </div>
              <div class="matchup-evidence">
                <strong>{{ recommendationNote(hero) }}</strong>
                <span>{{ hero.evidence_selections }} {{ t("historical response picks") }} · {{ percent(hero.style_similarity) }} {{ t("style match") }}</span>
              </div>
            </article>
          </div>
          <button
            v-if="canExpandMatchupRecommendations || matchupRecommendationsExpanded"
            class="matchup-expand"
            type="button"
            :disabled="matchupLoading"
            @click="toggleMatchupRecommendations"
          >
            {{ matchupLoading
              ? t("Calculating…")
              : matchupRecommendationsExpanded
              ? t("Show fewer recommendations")
              : t("Show more recommendations") }}
          </button>
          <p class="matchup-disclaimer">{{ t("This ranks historically supported responses, not guaranteed gameplay counters. Patch changes, team composition, and player comfort still matter.") }}</p>
        </template>
      </section>
    </template>
  </main>
</template>

<style scoped>
.feature-space-page { display:flex; flex-direction:column; width:min(1220px, calc(100% - 2rem)); margin:0 auto; padding:1.35rem 0 5rem; }
.feature-space-hero { display:flex; align-items:end; justify-content:space-between; gap:1.5rem; }.feature-space-eyebrow { margin:0 0 .45rem; color:var(--accent-deep); font-size:.66rem; letter-spacing:.13em; text-transform:uppercase; }.feature-space-hero h1, .hero-detail h2 { margin:0; font-family:var(--display); letter-spacing:-.045em; }.feature-space-hero h1 { font-size:clamp(2.4rem, 5vw, 4rem); line-height:.95; }.feature-space-hero > div > p:last-child { max-width:46rem; margin:.8rem 0 0; color:var(--ink-soft); font-size:.82rem; line-height:1.55; }
.season-picker { display:grid; min-width:310px; gap:.4rem; }.season-picker span { color:var(--ink-soft); font-size:.64rem; letter-spacing:.1em; text-transform:uppercase; }.season-picker select { min-height:42px; padding:.55rem .7rem; border:1px solid var(--line); background:rgba(255,255,255,.85); color:var(--ink); font:inherit; }.season-picker small { color:var(--ink-soft); font-size:.66rem; }
.message { margin:1.5rem 0; color:var(--ink-soft); }.message.error { color:var(--warn); }
.feature-space-next { order:2; margin:2.25rem 0 .35rem; color:var(--accent-deep); font:700 .67rem var(--display); letter-spacing:.11em; text-transform:uppercase; }.legend { order:3; display:flex; flex-wrap:wrap; gap:.45rem 1rem; margin-top:0; padding:.7rem 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }.map-reading { flex-basis:100%; color:var(--ink-soft); font-size:.7rem; line-height:1.45; }.map-reading strong { margin-right:.45rem; color:var(--ink); }.legend-item { display:inline-flex; align-items:center; gap:.35rem; color:var(--ink-soft); font-size:.7rem; }.legend-item i { display:block; width:.65rem; height:.65rem; border-radius:50%; background:#8b9797; }.legend-item.clash i, .hero-icon-frame.clash { stroke:#d97b44; background:#d97b44; }.legend-item.mid i, .hero-icon-frame.mid { stroke:#6c78cb; background:#6c78cb; }.legend-item.jungle i, .hero-icon-frame.jungle { stroke:#4f9d70; background:#4f9d70; }.legend-item.farm i, .hero-icon-frame.farm { stroke:#c79b34; background:#c79b34; }.legend-item.roam i, .hero-icon-frame.roam { stroke:#a369ae; background:#a369ae; }.legend-item small { color:var(--ink-soft); }
.space-layout { order:4; display:grid; grid-template-columns:minmax(0, 1fr) 260px; gap:1rem; margin-top:1rem; }.space-plot-wrap, .hero-detail { border:1px solid var(--line); background:rgba(255,255,255,.76); }.space-plot-wrap { padding:.6rem; }.map-controls { display:flex; align-items:center; flex-wrap:wrap; gap:.35rem; padding:0 0 .55rem; }.map-controls > span { flex:1; color:var(--ink-soft); font-size:.76rem; }.map-controls button { min-height:44px; padding:.25rem .6rem; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink); font:inherit; font-size:.78rem; cursor:pointer; }.map-controls button:hover:not(:disabled) { border-color:var(--accent-deep); }.map-controls button:disabled { cursor:not-allowed; opacity:.45; }.space-plot { display:block; width:100%; height:auto; overflow:visible; touch-action:pan-y; }.hero-icon-frame { fill:rgba(255,255,255,.92); stroke:#8b9797; stroke-width:2; vector-effect:non-scaling-stroke; cursor:pointer; transition:stroke-width .14s ease; }.hero-icon-frame:hover, .hero-icon-frame:focus, .hero-icon-frame.hovered { stroke:var(--ink); stroke-width:3; outline:none; }.hero-icon-frame.selected { stroke:var(--ink); stroke-width:3.5; }.hero-icon { pointer-events:none; }.hero-icon-fallback { fill:var(--ink); font:600 12px var(--display); text-anchor:middle; pointer-events:none; }.plot-note { margin:.25rem .5rem .1rem; color:var(--ink-soft); font-size:.78rem; }
.hero-detail { padding:1rem; }.selected-hero { display:flex; align-items:center; gap:.8rem; }.selected-hero img { width:3.3rem; height:3.3rem; object-fit:cover; }.hero-detail h2 { font-size:1.5rem; }.selected-hero > div > p:last-child { margin:.25rem 0 0; color:var(--ink-soft); font-size:.68rem; }.hero-detail dl { margin:1rem 0; }.hero-detail dl > div { padding:.65rem 0; border-top:1px solid var(--line); }.hero-detail dt { color:var(--ink-soft); font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; }.hero-detail dd { margin:.25rem 0 0; color:var(--ink); font-size:.74rem; line-height:1.45; }.mechanic-list { display:flex; flex-wrap:wrap; gap:.3rem; }.mechanic-list span { padding:.18rem .36rem; border:1px solid var(--line); background:rgba(255,255,255,.72); color:var(--ink-soft); font-size:.62rem; line-height:1.25; }.neighbor-list { display:grid; gap:.35rem; }.neighbor-list button { display:grid; grid-template-columns:1.8rem minmax(0, 1fr) auto; align-items:center; gap:.45rem; padding:.3rem; border:1px solid var(--line); background:rgba(255,255,255,.75); color:var(--ink); text-align:left; font:inherit; cursor:pointer; }.neighbor-list button:hover { border-color:var(--accent-deep); }.neighbor-list img { width:1.8rem; height:1.8rem; object-fit:cover; }.neighbor-list span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.72rem; }.neighbor-list small { color:var(--ink-soft); font-size:.6rem; }
.hero-response-section { order:5; margin-top:1rem; padding:1rem 1.1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.76); }.hero-response-header { display:flex; align-items:end; justify-content:space-between; gap:1rem; }.hero-response-header h2 { margin:0; font-family:var(--display); font-size:1.8rem; letter-spacing:-.035em; }.hero-response-header > div > p:last-child { max-width:38rem; margin:.35rem 0 0; color:var(--ink-soft); font-size:.72rem; line-height:1.45; }.hero-response-picker { display:grid; min-width:220px; gap:.35rem; }.hero-response-picker > span { color:var(--ink-soft); font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; }.hero-response-picker > div { display:grid; grid-template-columns:2.45rem minmax(0, 1fr); align-items:center; border:1px solid var(--line); background:rgba(255,255,255,.88); }.hero-response-picker img { width:2.45rem; height:2.45rem; object-fit:cover; }.hero-response-picker select { min-width:0; min-height:39px; padding:0 .55rem; border:0; outline:0; background:transparent; color:var(--ink); font:inherit; font-size:.75rem; }.hero-response-grid { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:.65rem; margin-top:1rem; }.hero-response-card { min-height:124px; padding:.7rem; border:1px solid var(--line); background:rgba(255,255,255,.62); }.hero-response-card h3 { margin:0; color:var(--ink-soft); font-size:.64rem; letter-spacing:.09em; text-transform:uppercase; }.response-icon-list { display:flex; align-items:flex-start; gap:.5rem; margin-top:.75rem; }.response-icon-list button { display:grid; gap:.18rem; width:3.35rem; padding:0; border:0; background:transparent; color:var(--ink); cursor:pointer; }.response-icon-list button:hover img, .response-icon-list button:focus-visible img { outline:2px solid var(--accent-deep); outline-offset:2px; }.response-icon-list img { width:3.35rem; height:3.35rem; object-fit:cover; box-shadow:0 0 0 1px var(--line); }.response-icon-list small { color:var(--ink-soft); font-size:.63rem; text-align:center; }.response-empty { margin:.95rem 0 0; color:var(--ink-soft); font-size:.68rem; }
.matchup-lab { order:1; margin-top:0; padding:1.1rem; border:1px solid var(--accent-deep); background:linear-gradient(135deg,rgba(255,255,255,.92),rgba(225,243,235,.94)); box-shadow:0 14px 36px rgba(16,42,46,.08); }.matchup-lab-header { display:flex; align-items:end; justify-content:space-between; gap:1.5rem; }.matchup-lab-header h1 { margin:0; font:800 clamp(1.65rem,3.4vw,2.55rem)/1 var(--display); letter-spacing:-.045em; }.matchup-lab-header p { max-width:42rem; margin:.4rem 0 0; color:var(--ink-soft); font-size:.72rem; line-height:1.5; }.matchup-builder { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1rem; align-items:start; margin-top:.85rem; }.favorite-picker,.opponent-picker { display:grid; align-content:start; gap:.4rem; min-width:0; }.favorite-picker>span,.opponent-picker>span,.matchup-summary span,.lane-picker>span { color:var(--ink-soft); font-size:.62rem; letter-spacing:.09em; text-transform:uppercase; }.opponent-search-wrap { position:relative; }.opponent-search-wrap input { box-sizing:border-box; width:100%; min-height:45px; padding:.6rem .75rem; border:1px solid var(--line); background:#fff; color:var(--ink); font:inherit; }.opponent-options { position:absolute; z-index:8; top:calc(100% + 3px); left:0; right:0; display:grid; max-height:250px; padding:.3rem; border:1px solid var(--line); background:#fff; box-shadow:0 10px 30px rgba(16,42,46,.13); overflow:auto; }.opponent-options button { display:grid; grid-template-columns:2rem minmax(0,1fr) auto; align-items:center; gap:.5rem; padding:.35rem; border:0; background:transparent; color:var(--ink); text-align:left; font:inherit; }.opponent-options button:hover { background:rgba(15,138,107,.08); }.opponent-options img { width:2rem; height:2rem; object-fit:cover; }.opponent-options span { font-size:.75rem; }.opponent-options small { color:var(--ink-soft); font-size:.62rem; }.favorite-chips,.opponent-chips { display:flex; flex-wrap:wrap; align-content:start; min-height:2rem; gap:.3rem; }.favorite-chips button,.opponent-chips button { display:flex; align-items:center; gap:.3rem; padding:.2rem .4rem .2rem .2rem; border:1px solid var(--line); background:rgba(255,255,255,.9); color:var(--ink); font:inherit; }.favorite-chips img,.opponent-chips img { width:1.7rem; height:1.7rem; object-fit:cover; }.favorite-chips span,.opponent-chips span { font-size:.67rem; }.favorite-chips small { color:var(--ink-soft); font-size:.56rem; }.favorite-chips b,.opponent-chips b { color:var(--ink-soft); font-size:.85rem; }.favorite-empty { display:flex; align-items:center; min-height:2rem; margin:0; padding:0 .55rem; border:1px dashed var(--line); color:var(--ink-soft); font-size:.65rem; }.matchup-actions { grid-column:1/-1; display:flex; align-items:end; justify-content:space-between; gap:1rem; padding-top:.15rem; border-top:1px solid rgba(16,42,46,.15); }.lane-picker { display:grid; gap:.4rem; }.lane-picker>div { display:flex; flex-wrap:wrap; gap:.35rem; }.lane-picker button { min-height:32px; padding:.3rem .55rem; border:1px solid var(--line); background:rgba(255,255,255,.7); color:var(--ink-soft); font:600 .66rem var(--display); cursor:pointer; }.lane-picker button:hover,.lane-picker button.active { border-color:var(--accent-deep); background:var(--accent-deep); color:#fff; }.matchup-submit { flex:0 0 auto; min-height:45px; padding:.6rem 1.15rem; border:1px solid var(--ink); background:var(--ink); color:#fff; font:700 .75rem var(--display); }.matchup-submit:disabled { cursor:not-allowed; opacity:.45; }.matchup-message { margin:1rem 0 0; padding:.8rem; border:1px dashed var(--line); color:var(--ink-soft); font-size:.72rem; text-align:center; }.matchup-message.error { color:var(--warn); }.matchup-summary { display:flex; justify-content:space-between; gap:1.2rem; margin-top:1rem; padding:.8rem 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }.favorite-ranks { display:flex; flex-wrap:wrap; gap:.25rem .7rem; margin-top:.25rem; }.matchup-summary strong { display:block; font:700 .82rem var(--display); }.matchup-summary p { max-width:34rem; margin:0; color:var(--ink-soft); font-size:.68rem; line-height:1.5; }.matchup-results { display:grid; gap:.45rem; margin-top:.65rem; }.matchup-results article { display:grid; grid-template-columns:35px minmax(180px,.8fr) 75px minmax(220px,1.2fr); gap:.75rem; align-items:center; padding:.65rem .75rem; border:1px solid var(--line); background:rgba(255,255,255,.78); }.matchup-results article.favorite { border-color:rgba(196,92,38,.45); background:rgba(255,248,231,.72); }.matchup-rank { color:var(--accent); font:700 .72rem var(--mono); }.matchup-hero { display:flex; align-items:center; gap:.6rem; padding:0; border:0; background:transparent; color:var(--ink); text-align:left; }.matchup-hero img { width:2.8rem; height:2.8rem; object-fit:cover; border-radius:50%; }.matchup-hero strong,.matchup-hero small { display:block; }.matchup-hero strong { font:700 .9rem var(--display); }.matchup-hero small { margin-top:.12rem; color:var(--ink-soft); font-size:.6rem; }.matchup-score strong,.matchup-score span,.matchup-evidence strong,.matchup-evidence span { display:block; }.matchup-score strong { color:var(--accent-deep); font:800 1.3rem var(--display); }.matchup-score span { color:var(--ink-soft); font-size:.55rem; text-transform:uppercase; }.matchup-evidence strong { font-size:.7rem; }.matchup-evidence span { margin-top:.2rem; color:var(--ink-soft); font-size:.61rem; }.matchup-expand { display:block; min-height:40px; margin:.75rem auto 0; padding:.5rem .85rem; border:1px solid var(--accent-deep); background:rgba(255,255,255,.84); color:var(--accent-deep); font:700 .68rem var(--display); cursor:pointer; }.matchup-expand:hover:not(:disabled) { background:var(--accent-deep); color:#fff; }.matchup-expand:disabled { cursor:wait; opacity:.65; }.matchup-disclaimer { margin:.75rem 0 0; color:var(--ink-soft); font-size:.65rem; line-height:1.5; }
.feature-space-hero { position:relative; padding:1.5rem; border:1px solid var(--line); background:radial-gradient(circle at 85% 10%,rgba(65,174,128,.18),transparent 34%),linear-gradient(135deg,rgba(255,255,255,.9),rgba(238,245,241,.78)); overflow:hidden; }
.feature-space-hero>div { position:relative; z-index:1; }
.hero-proof-pills { display:flex; flex-wrap:wrap; gap:.4rem; margin-top:1rem; }
.hero-proof-pills span { padding:.35rem .55rem; border:1px solid rgba(15,138,107,.22); border-radius:999px; background:rgba(255,255,255,.62); color:var(--accent-deep); font:700 .61rem var(--display); }
.bp-reference-note { display:grid; grid-template-columns:2rem minmax(0,1fr); gap:.65rem; align-items:center; margin-top:.7rem; padding:.55rem .65rem; border:1px solid rgba(195,129,48,.28); background:rgba(255,247,224,.72); }
.bp-reference-note>span { display:grid; width:2rem; height:2rem; place-items:center; border-radius:50%; background:#f2d79b; color:#73511f; font:800 .62rem var(--display); }
.bp-reference-note strong { font:800 .8rem var(--display); }
.bp-reference-note p { margin:.25rem 0 0; color:var(--ink-soft); font-size:.68rem; line-height:1.55; }
.matchup-steps { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.55rem; margin:1rem 0 0; padding:0; list-style:none; }
.matchup-steps li { display:flex; gap:.55rem; align-items:center; min-width:0; padding:.65rem; border:1px solid var(--line); background:rgba(255,255,255,.54); }
.matchup-steps li>b { display:grid; flex:0 0 auto; width:1.7rem; height:1.7rem; place-items:center; border-radius:50%; background:var(--ink); color:#fff; font:800 .65rem var(--display); }
.matchup-steps li span,.matchup-steps li strong,.matchup-steps li small { display:block; min-width:0; }
.matchup-steps li strong { font:800 .7rem var(--display); }
.matchup-steps li small { margin-top:.12rem; color:var(--ink-soft); font-size:.58rem; line-height:1.35; }
.matchup-onboarding { position:relative; margin-top:1rem; padding-bottom:2rem; }
.matchup-onboarding .matchup-steps { margin-top:0; }
.dismiss-onboarding { position:absolute; right:0; bottom:0; padding:0; border:0; background:transparent; color:var(--accent-deep); font:700 .65rem var(--display); cursor:pointer; }
.dismiss-onboarding:hover { color:var(--ink); text-decoration:underline; }
.matchup-data-note { grid-column:1/-1; margin:0; color:var(--ink-soft); font-size:.64rem; line-height:1.45; }
.feature-space-deep-dive { order:3; margin-top:1.25rem; border:1px solid var(--line); background:rgba(255,255,255,.58); }
.feature-space-deep-dive>summary { display:flex; justify-content:space-between; align-items:center; gap:1rem; min-height:72px; padding:1rem 1.1rem; cursor:pointer; list-style:none; }
.feature-space-deep-dive>summary::-webkit-details-marker { display:none; }
.feature-space-deep-dive>summary span,.feature-space-deep-dive>summary strong,.feature-space-deep-dive>summary small { display:block; }
.feature-space-deep-dive>summary strong { font:800 1rem var(--display); }
.feature-space-deep-dive>summary small { margin-top:.2rem; color:var(--ink-soft); font-size:.66rem; }
.feature-space-deep-dive>summary b { display:grid; flex:0 0 auto; width:2rem; height:2rem; place-items:center; border:1px solid var(--line); border-radius:50%; font:400 1.2rem var(--display); transition:transform .18s ease; }
.feature-space-deep-dive[open]>summary { border-bottom:1px solid var(--line); }
.feature-space-deep-dive[open]>summary b { transform:rotate(45deg); }
.deep-dive-content { padding:0 1rem 1rem; }
.deep-dive-content .legend { margin-top:1rem; }
@media (max-width:900px) { .matchup-results article { grid-template-columns:30px minmax(150px,1fr) 70px; }.matchup-evidence { grid-column:2/-1; } }
@media (max-width:820px) { .feature-space-hero { align-items:stretch; flex-direction:column; }.season-picker { width:100%; }.space-layout { grid-template-columns:1fr; }.hero-detail { min-height:0; }.matchup-lab-header { align-items:start; flex-direction:column; }.matchup-lab-header>span { text-align:left; } }
@media (max-width:700px) { .feature-space-hero { padding:1rem; }.hero-response-header { align-items:stretch; flex-direction:column; }.hero-response-picker { min-width:0; }.hero-response-grid { grid-template-columns:1fr; }.hero-response-card { min-height:0; }.matchup-steps { grid-template-columns:1fr; }.matchup-builder { grid-template-columns:1fr; }.matchup-actions { grid-column:auto; align-items:stretch; flex-direction:column; }.matchup-submit { width:100%; }.matchup-summary { flex-direction:column; }.matchup-results article { grid-template-columns:27px minmax(120px,1fr) 62px; gap:.45rem; padding:.6rem .45rem; }.matchup-hero img { width:2.3rem; height:2.3rem; }.matchup-evidence { grid-column:2/-1; }.deep-dive-content { padding:0 .55rem .65rem; }.dismiss-onboarding { bottom:0; } }
@media (max-width:620px) { .feature-space-page { width:calc(100% - 1rem); padding-top:1.25rem; }.space-plot-wrap { padding:.15rem; }.space-plot { touch-action:none; }.legend { gap:.4rem .7rem; } }
</style>
