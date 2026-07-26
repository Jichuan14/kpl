<script setup>
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchLearnedFeatureSpace,
  fetchVisualizationPatterns,
  fetchVisualizationSeasons,
} from "./api";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { heroAsset } from "./heroAssets";
import { language, t } from "./i18n";
import { finishStartupLoading } from "./startupLoader";

const WIDTH = 820;
const HEIGHT = 520;
const PADDING = 48;
const INITIAL_HERO_LIMIT = 48;
const ICON_SIZE = 30;
const EDGE_INSET = ICON_SIZE / 2 + 4;
const MAX_ZOOM = 2.5;

const seasons = ref([]);
const leagueId = selectedLeagueId;
const payload = ref(null);
const patterns = ref(null);
const loading = ref(false);
const error = ref("");
const selectedHeroId = ref(null);
const hoveredHeroId = ref(null);
const showAllHeroes = ref(false);
const zoom = ref(1);
const pan = ref({ x: 0, y: 0 });
const dragState = ref(null);

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
const responseGroups = computed(() => {
  const heroId = Number(selectedHero.value?.hero_id);
  const patternRows = patterns.value?.rows || [];
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
  return heroAsset(hero?.hero_id);
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
  dragState.value = { pointerId: event.pointerId, ...position };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function panMap(event) {
  if (!dragState.value || dragState.value.pointerId !== event.pointerId) return;
  const position = pointerPosition(event);
  pan.value = clampPan({
    x: pan.value.x + position.x - dragState.value.x,
    y: pan.value.y + position.y - dragState.value.y,
  });
  dragState.value = { pointerId: event.pointerId, ...position };
}

function endPan(event) {
  if (!dragState.value || dragState.value.pointerId !== event.pointerId) return;
  dragState.value = null;
  event.currentTarget.releasePointerCapture(event.pointerId);
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
  loading.value = true;
  error.value = "";
  payload.value = null;
  patterns.value = null;
  try {
    const [featureSpace, patternData] = await Promise.all([
      fetchLearnedFeatureSpace(leagueId.value),
      fetchVisualizationPatterns({ leagueId: leagueId.value }).catch(() => null),
    ]);
    payload.value = featureSpace;
    patterns.value = patternData;
    selectedHeroId.value = rankedRows.value[0]?.hero_id || null;
    showAllHeroes.value = false;
    resetView();
  } catch {
    error.value = t("No learned feature space is available for this season. Train the learnable model first.");
  } finally {
    loading.value = false;
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

watch(leagueId, loadFeatureSpace);
</script>

<template>
  <main class="feature-space-page">
    <header class="feature-space-hero">
      <div>
        <p class="feature-space-eyebrow">{{ t("Learned model space") }}</p>
        <h1>{{ t("Hero feature space") }}</h1>
        <p>
          {{ t("Each point is a hero's learned candidate representation after training. Nearby heroes are similar to the model in both their specialty profile and historical draft behavior.") }}
        </p>
      </div>
      <label class="season-picker">
        <span>{{ t("Competition") }}</span>
        <select v-model="leagueId" :disabled="loading">
          <option v-for="season in seasons" :key="season.league_id" :value="season.league_id">
            {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
          </option>
        </select>
        <small v-if="payload">{{ t("Learned similarity map") }} · {{ t("Closer icons mean more similar draft behavior") }}</small>
      </label>
    </header>

    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="loading" class="message">{{ t("Loading learned feature space…") }}</p>

    <template v-else-if="payload">
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
            @wheel.prevent="zoomWithWheel"
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
              <p>{{ selectedHero.feature_known ? t("Specialty profile available") : t("Specialty profile unavailable") }}</p>
            </div>
          </div>
          <dl>
            <div><dt>{{ t("Damage") }}</dt><dd>{{ selectedHero.damage_types?.join(" · ") || t("Unknown") }}</dd></div>
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
    </template>
  </main>
</template>

<style scoped>
.feature-space-page { width:min(1220px, calc(100% - 2rem)); margin:0 auto; padding:2.25rem 0 5rem; }
.feature-space-hero { display:flex; align-items:end; justify-content:space-between; gap:1.5rem; }.feature-space-eyebrow { margin:0 0 .45rem; color:var(--accent-deep); font-size:.66rem; letter-spacing:.13em; text-transform:uppercase; }.feature-space-hero h1, .hero-detail h2 { margin:0; font-family:var(--display); letter-spacing:-.045em; }.feature-space-hero h1 { font-size:clamp(2.4rem, 5vw, 4rem); line-height:.95; }.feature-space-hero > div > p:last-child { max-width:46rem; margin:.8rem 0 0; color:var(--ink-soft); font-size:.82rem; line-height:1.55; }
.season-picker { display:grid; min-width:310px; gap:.4rem; }.season-picker span { color:var(--ink-soft); font-size:.64rem; letter-spacing:.1em; text-transform:uppercase; }.season-picker select { min-height:42px; padding:.55rem .7rem; border:1px solid var(--line); background:rgba(255,255,255,.85); color:var(--ink); font:inherit; }.season-picker small { color:var(--ink-soft); font-size:.66rem; }
.message { margin:1.5rem 0; color:var(--ink-soft); }.message.error { color:var(--warn); }
.legend { display:flex; flex-wrap:wrap; gap:.45rem 1rem; margin-top:1.5rem; padding:.7rem 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }.map-reading { flex-basis:100%; color:var(--ink-soft); font-size:.7rem; line-height:1.45; }.map-reading strong { margin-right:.45rem; color:var(--ink); }.legend-item { display:inline-flex; align-items:center; gap:.35rem; color:var(--ink-soft); font-size:.7rem; }.legend-item i { display:block; width:.65rem; height:.65rem; border-radius:50%; background:#8b9797; }.legend-item.clash i, .hero-icon-frame.clash { stroke:#d97b44; background:#d97b44; }.legend-item.mid i, .hero-icon-frame.mid { stroke:#6c78cb; background:#6c78cb; }.legend-item.jungle i, .hero-icon-frame.jungle { stroke:#4f9d70; background:#4f9d70; }.legend-item.farm i, .hero-icon-frame.farm { stroke:#c79b34; background:#c79b34; }.legend-item.roam i, .hero-icon-frame.roam { stroke:#a369ae; background:#a369ae; }.legend-item small { color:var(--ink-soft); }
.space-layout { display:grid; grid-template-columns:minmax(0, 1fr) 260px; gap:1rem; margin-top:1rem; }.space-plot-wrap, .hero-detail { border:1px solid var(--line); background:rgba(255,255,255,.76); }.space-plot-wrap { padding:.6rem; }.map-controls { display:flex; align-items:center; flex-wrap:wrap; gap:.35rem; padding:0 0 .55rem; }.map-controls > span { flex:1; color:var(--ink-soft); font-size:.67rem; }.map-controls button { min-height:28px; padding:.25rem .45rem; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink); font:inherit; font-size:.65rem; cursor:pointer; }.map-controls button:hover:not(:disabled) { border-color:var(--accent-deep); }.map-controls button:disabled { cursor:not-allowed; opacity:.45; }.space-plot { display:block; width:100%; height:auto; overflow:visible; touch-action:none; }.hero-icon-frame { fill:rgba(255,255,255,.92); stroke:#8b9797; stroke-width:2; vector-effect:non-scaling-stroke; cursor:pointer; transition:stroke-width .14s ease; }.hero-icon-frame:hover, .hero-icon-frame:focus, .hero-icon-frame.hovered { stroke:var(--ink); stroke-width:3; outline:none; }.hero-icon-frame.selected { stroke:var(--ink); stroke-width:3.5; }.hero-icon { pointer-events:none; }.hero-icon-fallback { fill:var(--ink); font:600 12px var(--display); text-anchor:middle; pointer-events:none; }.plot-note { margin:.25rem .5rem .1rem; color:var(--ink-soft); font-size:.68rem; }
.hero-detail { padding:1rem; }.selected-hero { display:flex; align-items:center; gap:.8rem; }.selected-hero img { width:3.3rem; height:3.3rem; object-fit:cover; }.hero-detail h2 { font-size:1.5rem; }.selected-hero > div > p:last-child { margin:.25rem 0 0; color:var(--ink-soft); font-size:.68rem; }.hero-detail dl { margin:1rem 0; }.hero-detail dl > div { padding:.65rem 0; border-top:1px solid var(--line); }.hero-detail dt { color:var(--ink-soft); font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; }.hero-detail dd { margin:.25rem 0 0; color:var(--ink); font-size:.74rem; line-height:1.45; }.neighbor-list { display:grid; gap:.35rem; }.neighbor-list button { display:grid; grid-template-columns:1.8rem minmax(0, 1fr) auto; align-items:center; gap:.45rem; padding:.3rem; border:1px solid var(--line); background:rgba(255,255,255,.75); color:var(--ink); text-align:left; font:inherit; cursor:pointer; }.neighbor-list button:hover { border-color:var(--accent-deep); }.neighbor-list img { width:1.8rem; height:1.8rem; object-fit:cover; }.neighbor-list span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.72rem; }.neighbor-list small { color:var(--ink-soft); font-size:.6rem; }
.hero-response-section { margin-top:1rem; padding:1rem 1.1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.76); }.hero-response-header { display:flex; align-items:end; justify-content:space-between; gap:1rem; }.hero-response-header h2 { margin:0; font-family:var(--display); font-size:1.8rem; letter-spacing:-.035em; }.hero-response-header > div > p:last-child { max-width:38rem; margin:.35rem 0 0; color:var(--ink-soft); font-size:.72rem; line-height:1.45; }.hero-response-picker { display:grid; min-width:220px; gap:.35rem; }.hero-response-picker > span { color:var(--ink-soft); font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; }.hero-response-picker > div { display:grid; grid-template-columns:2.45rem minmax(0, 1fr); align-items:center; border:1px solid var(--line); background:rgba(255,255,255,.88); }.hero-response-picker img { width:2.45rem; height:2.45rem; object-fit:cover; }.hero-response-picker select { min-width:0; min-height:39px; padding:0 .55rem; border:0; outline:0; background:transparent; color:var(--ink); font:inherit; font-size:.75rem; }.hero-response-grid { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:.65rem; margin-top:1rem; }.hero-response-card { min-height:124px; padding:.7rem; border:1px solid var(--line); background:rgba(255,255,255,.62); }.hero-response-card h3 { margin:0; color:var(--ink-soft); font-size:.64rem; letter-spacing:.09em; text-transform:uppercase; }.response-icon-list { display:flex; align-items:flex-start; gap:.5rem; margin-top:.75rem; }.response-icon-list button { display:grid; gap:.18rem; width:3.35rem; padding:0; border:0; background:transparent; color:var(--ink); cursor:pointer; }.response-icon-list button:hover img, .response-icon-list button:focus-visible img { outline:2px solid var(--accent-deep); outline-offset:2px; }.response-icon-list img { width:3.35rem; height:3.35rem; object-fit:cover; box-shadow:0 0 0 1px var(--line); }.response-icon-list small { color:var(--ink-soft); font-size:.63rem; text-align:center; }.response-empty { margin:.95rem 0 0; color:var(--ink-soft); font-size:.68rem; }
@media (max-width:820px) { .feature-space-hero { align-items:stretch; flex-direction:column; }.season-picker { width:100%; }.space-layout { grid-template-columns:1fr; }.hero-detail { min-height:0; } }
@media (max-width:700px) { .hero-response-header { align-items:stretch; flex-direction:column; }.hero-response-picker { min-width:0; }.hero-response-grid { grid-template-columns:1fr; }.hero-response-card { min-height:0; } }
@media (max-width:620px) { .feature-space-page { width:calc(100% - 1rem); padding-top:1.25rem; }.space-plot-wrap { padding:.15rem; }.legend { gap:.4rem .7rem; } }
</style>
