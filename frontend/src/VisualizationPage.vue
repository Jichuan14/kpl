<script setup>
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import {
  fetchMetaHistory,
  fetchPatternManifest,
  fetchVisualizationPatterns,
  fetchVisualizationSeasons,
} from "./api";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { heroAsset } from "./heroAssets";
import { language, t } from "./i18n";
import { finishStartupLoading } from "./startupLoader";

const seasons = ref([]);
const leagueId = selectedLeagueId;
const payload = shallowRef(null);
const loading = ref(false);
const metaLoading = ref(false);
const error = ref("");
const metaHistory = ref([]);
const selectedMetaHeroId = ref("");
const hoveredMetaPoint = ref(null);

const relation = ref("counter_pick");
const responseScope = ref("all");
const context = ref("overall");
const side = ref("all");
const metric = ref("selections");
const support = ref(3);
const resultCount = ref("20");
const search = ref("");
const debouncedSearch = ref("");
let patternController = null;
let searchTimer = null;
let relationScrollY = null;

const relationOptions = [
  { value: "counter_pick", label: "Counter picks", short: "Counter picks" },
  { value: "counter_ban", label: "Bans into enemy picks", short: "Counter bans" },
  { value: "pick_synergy", label: "Heroes picked together", short: "Synergies" },
  { value: "ban_response", label: "What follows a ban", short: "Ban responses" },
];

const metricOptions = [
  { value: "smoothed_lift", label: "More common than usual" },
  { value: "smoothed_probability", label: "Most likely when available" },
  { value: "selections", label: "Most often seen" },
  { value: "win_rate", label: "Best battle win rate" },
];

const currentSeason = computed(() =>
  seasons.value.find((season) => season.league_id === leagueId.value)
);

const currentRelation = computed(
  () =>
    relationOptions.find((option) => option.value === relation.value) ||
    relationOptions[0]
);

const rows = computed(() => payload.value?.rows || []);

const topMetaHeroes = computed(() =>
  (payload.value?.meta_heroes || [])
    .filter((hero) => hero.early_priority_count > 0)
    .slice(0, 12)
);

const metaHeroOptions = computed(() => {
  const heroes = new Map();
  for (const entry of metaHistory.value) {
    for (const hero of entry.meta_heroes) {
      if (hero.early_priority_count > 0 && !heroes.has(Number(hero.hero_id))) {
        heroes.set(Number(hero.hero_id), hero);
      }
    }
  }
  const currentPopularity = new Map(
    (payload.value?.meta_heroes || []).map((hero) => [
      Number(hero.hero_id),
      Number(hero.early_priority_rate || 0),
    ])
  );
  return [...heroes.values()].sort((a, b) => {
    const popularityA = currentPopularity.get(Number(a.hero_id)) || 0;
    const popularityB = currentPopularity.get(Number(b.hero_id)) || 0;
    return popularityB - popularityA || a.hero_name.localeCompare(b.hero_name, language.value);
  });
});

const metaSeries = computed(() =>
  metaHistory.value.map((entry) => {
    const hero = entry.meta_heroes.find(
      (candidate) => Number(candidate.hero_id) === Number(selectedMetaHeroId.value)
    );
    return {
      ...entry.season,
      hero,
      rate: Number(hero?.early_priority_rate || 0),
      rank: hero?.priority_rank || null,
    };
  })
);

const metaMaximumRate = computed(() =>
  Math.max(...metaSeries.value.map((entry) => entry.rate), 0.01)
);

const metaChartPoints = computed(() => {
  const width = 620;
  const left = 26;
  const right = 12;
  const top = 12;
  const bottom = 28;
  const height = 180;
  const span = Math.max(1, metaSeries.value.length - 1);
  return metaSeries.value.map((entry, index) => ({
    ...entry,
    x: left + ((width - left - right) * index) / span,
    y: height - bottom - ((height - top - bottom) * entry.rate) / metaMaximumRate.value,
  }));
});

const metaChartLine = computed(() =>
  metaChartPoints.value.map((entry) => `${entry.x},${entry.y}`).join(" ")
);

const selectedMetaHero = computed(() =>
  metaHeroOptions.value.find(
    (hero) => Number(hero.hero_id) === Number(selectedMetaHeroId.value)
  )
);

const currentSeasonMetaHeroes = computed(() =>
  (payload.value?.meta_heroes || [])
    .filter((hero) => hero.early_priority_count > 0)
    .sort((a, b) => a.priority_rank - b.priority_rank)
    .slice(0, 12)
);

const filteredRows = computed(() => {
  const needle = debouncedSearch.value.trim().toLocaleLowerCase();
  return rows.value
    .filter(
      (row) =>
        row.relation === relation.value &&
        row.context_level === context.value &&
        !row.is_peak_battle &&
        row.selections >= Number(support.value || 1) &&
        (relation.value !== "ban_response" ||
          responseScope.value === "all" ||
          row.response_scope === responseScope.value) &&
        (context.value === "overall" ||
          side.value === "all" ||
          row.side === side.value) &&
        (!needle ||
          row.source_hero_name.toLocaleLowerCase().includes(needle) ||
          row.target_hero_name.toLocaleLowerCase().includes(needle))
    )
    .sort(
      (a, b) =>
        metricValue(b) - metricValue(a) ||
        b.selections - a.selections ||
        a.relationship.localeCompare(b.relationship)
    );
});

const shownRows = computed(() => {
  // The visual chart stays responsive even if a user asks to inspect all rows.
  if (resultCount.value === "all") return filteredRows.value.slice(0, 50);
  return filteredRows.value.slice(0, Number(resultCount.value));
});

const tableRows = computed(() =>
  resultCount.value === "all" ? filteredRows.value.slice(0, 200) : shownRows.value
);

const maximumMetric = computed(() =>
  Math.max(...shownRows.value.map((row) => Math.max(0, metricValue(row))), 0.001)
);

function metricValue(row) {
  const value = row?.[metric.value];
  return value == null ? Number.NEGATIVE_INFINITY : Number(value);
}

function metricText(row) {
  const value = metricValue(row);
  if (!Number.isFinite(value)) return "—";
  if (metric.value === "smoothed_probability" || metric.value === "win_rate") {
    return percent(value);
  }
  if (metric.value === "smoothed_lift") return `${value.toFixed(2)}×`;
  return Math.round(value).toLocaleString(language.value);
}

function percent(value) {
  return value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
}

function number(value) {
  return Number(value || 0).toLocaleString(language.value);
}

function selectRelation(nextRelation) {
  if (relation.value === nextRelation) return;
  relationScrollY = window.scrollY;
  relation.value = nextRelation;
}

function initial(name) {
  return String(name || "?").slice(0, 1);
}

function heroIcon(heroId) {
  return heroAsset(heroId);
}

function barWidth(row) {
  const value = Math.max(0, metricValue(row));
  return `${Math.max(1.5, (value / maximumMetric.value) * 100)}%`;
}

function metaBanWidth(hero) {
  return `${Math.min(100, Number(hero.opening_ban_rate || 0) * 100)}%`;
}

function metaPickWidth(hero) {
  if (!hero.eligible_battle_count) return "0%";
  return `${Math.min(
    100,
    (Number(hero.blue_first_pick_count || 0) /
      Number(hero.eligible_battle_count)) *
      100
  )}%`;
}

async function loadSeasons() {
  seasons.value = (await fetchVisualizationSeasons()) || [];
  selectAvailableLeague(seasons.value);
}

async function loadPatterns() {
  if (!leagueId.value) return;
  patternController?.abort();
  const controller = new AbortController();
  patternController = controller;
  loading.value = true;
  error.value = "";
  try {
    const [manifest, patterns] = await Promise.all([
      fetchPatternManifest(leagueId.value, { signal: controller.signal }),
      fetchVisualizationPatterns({
        leagueId: leagueId.value,
        minSelections: 2,
        relation: relation.value,
        context: context.value,
        signal: controller.signal,
      }),
    ]);
    if (patternController !== controller) return;
    payload.value = { ...manifest, rows: patterns.rows || [] };
  } catch (err) {
    if (patternController !== controller) return;
    if (err.name === "AbortError") return;
    payload.value = null;
    error.value = err.message || "Could not load this season's patterns.";
  } finally {
    if (patternController === controller) {
      loading.value = false;
      if (relationScrollY != null) {
        const scrollY = relationScrollY;
        relationScrollY = null;
        window.requestAnimationFrame(() => window.scrollTo({ top: scrollY, behavior: "auto" }));
      }
    }
  }
}

async function loadMetaHistory() {
  metaLoading.value = true;
  try {
    const entries = await fetchMetaHistory();
    metaHistory.value = entries.sort(
      (a, b) =>
        Number(a.season.year || 0) - Number(b.season.year || 0) ||
        Number(a.season.season || 0) - Number(b.season.season || 0)
    );
    if (!metaHeroOptions.value.some((hero) => Number(hero.hero_id) === Number(selectedMetaHeroId.value))) {
      selectedMetaHeroId.value = String(topMetaHeroes.value[0]?.hero_id || metaHeroOptions.value[0]?.hero_id || "");
    }
  } catch {
    metaHistory.value = [];
  } finally {
    metaLoading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadPatterns();
    await loadMetaHistory();
  } catch (err) {
    error.value = err.message || "Could not load visualization data.";
  } finally {
    finishStartupLoading();
  }
});

watch(leagueId, loadPatterns);
watch([relation, context], loadPatterns);
watch(search, (value) => {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => { debouncedSearch.value = value; }, 160);
});
watch(selectedMetaHeroId, () => {
  hoveredMetaPoint.value = null;
});
watch(relation, () => {
  if (relation.value !== "ban_response") responseScope.value = "all";
});
onBeforeUnmount(() => {
  patternController?.abort();
  if (searchTimer) window.clearTimeout(searchTimer);
});
</script>

<template>
  <main class="visual-page">
    <section class="explorer-heading">
      <div>
        <p class="visual-eyebrow">Personal analysis practice · Public match information</p>
        <h1>Draft Pattern Explorer</h1>
        <p>
          Filter the season data to investigate specific counters, combinations,
          and ban responses.
        </p>
      </div>
      <label class="season-control">
        <span>Competition</span>
        <select v-model="leagueId">
          <option v-if="!seasons.length" value="">No analyzed seasons</option>
          <option
            v-for="season in seasons"
            :key="season.league_id"
            :value="season.league_id"
          >
            {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
          </option>
        </select>
        <small v-if="currentSeason">Dataset {{ currentSeason.league_id }}</small>
      </label>
    </section>

    <p v-if="error" class="visual-message error">{{ error }}</p>
    <p v-else-if="loading" class="visual-message">Loading season data…</p>

    <template v-if="payload && !loading">
      <section v-if="topMetaHeroes.length" class="meta-section">
        <div class="meta-heading">
          <div>
            <p class="visual-eyebrow">Opening draft priority</p>
            <h2>Season meta heroes</h2>
            <p>
              Heroes most often removed in the first four bans or secured with
              Blue's first pick.
            </p>
          </div>
          <div class="meta-legend">
            <span><i class="ban-key"></i>Opening ban</span>
            <span><i class="pick-key"></i>Blue first pick</span>
          </div>
        </div>

        <div class="meta-grid">
          <article
            v-for="hero in topMetaHeroes"
            :key="hero.hero_id"
            class="meta-hero"
            :class="{ active: Number(selectedMetaHeroId) === Number(hero.hero_id) }"
            role="button"
            tabindex="0"
            :aria-pressed="Number(selectedMetaHeroId) === Number(hero.hero_id)"
            @mouseenter="selectedMetaHeroId = String(hero.hero_id)"
            @click="selectedMetaHeroId = String(hero.hero_id)"
            @keydown.enter.prevent="selectedMetaHeroId = String(hero.hero_id)"
            @keydown.space.prevent="selectedMetaHeroId = String(hero.hero_id)"
          >
            <span class="meta-rank">{{ hero.priority_rank }}</span>
            <div class="meta-avatar">
              <img
                v-if="heroIcon(hero.hero_id)"
                :src="heroIcon(hero.hero_id)"
                :alt="hero.hero_name"
                width="48"
                height="48"
                loading="lazy"
                decoding="async"
              />
              <span v-else>{{ initial(hero.hero_name) }}</span>
            </div>
            <div class="meta-copy">
              <strong>{{ hero.hero_name }}</strong>
              <small>
                {{ hero.opening_ban_count }} bans ·
                {{ hero.blue_first_pick_count }} Blue first picks
              </small>
              <div class="meta-track">
                <span
                  class="meta-ban"
                  :style="{ width: metaBanWidth(hero) }"
                ></span>
                <span
                  class="meta-pick"
                  :style="{ width: metaPickWidth(hero) }"
                ></span>
              </div>
            </div>
            <strong class="meta-rate">
              {{ percent(hero.early_priority_rate) }}
              <small>priority</small>
            </strong>
          </article>
        </div>
      </section>

      <section v-if="metaHeroOptions.length" class="meta-evolution">
        <div class="meta-evolution-heading">
          <div>
            <p class="visual-eyebrow">Season comparison</p>
            <h2>Meta evolution</h2>
            <p>Track how opening-draft priority rises and falls between seasons.</p>
          </div>
          <div class="meta-hero-controls">
            <label>
              <span>Hero</span>
              <select v-model="selectedMetaHeroId">
                <option v-for="hero in metaHeroOptions" :key="hero.hero_id" :value="String(hero.hero_id)">
                  {{ hero.hero_name }}
                </option>
              </select>
            </label>
            <div class="current-meta-icons" aria-label="Current season meta heroes">
              <button
                v-for="hero in currentSeasonMetaHeroes"
                :key="hero.hero_id"
                type="button"
                :class="{ active: Number(selectedMetaHeroId) === Number(hero.hero_id) }"
                :title="`#${hero.priority_rank} · ${hero.hero_name} · ${percent(hero.early_priority_rate)}`"
                @click="selectedMetaHeroId = String(hero.hero_id)"
              >
                <img
                  :src="heroIcon(hero.hero_id)"
                  :alt="hero.hero_name"
                />
                <small>#{{ hero.priority_rank }}</small>
              </button>
            </div>
          </div>
        </div>

        <div v-if="metaLoading" class="meta-evolution-message">Loading season history…</div>
        <template v-else>
          <div class="meta-selected-hero">
            <div class="meta-avatar">
              <img
                v-if="selectedMetaHero && heroIcon(selectedMetaHero.hero_id)"
                :src="heroIcon(selectedMetaHero.hero_id)"
                :alt="selectedMetaHero.hero_name"
              />
            </div>
            <div>
              <strong>{{ selectedMetaHero?.hero_name }}</strong>
              <small>Opening bans + Blue first picks, divided by eligible drafts.</small>
            </div>
          </div>
          <div class="meta-chart-wrap" :aria-label="`${selectedMetaHero?.hero_name || 'Selected hero'} priority by season`">
            <svg viewBox="0 0 620 180" preserveAspectRatio="none">
              <line x1="26" y1="152" x2="608" y2="152" class="meta-chart-axis" />
              <polyline :points="metaChartLine" class="meta-chart-line" />
              <circle
                v-for="entry in metaChartPoints"
                :key="entry.league_id"
                :cx="entry.x"
                :cy="entry.y"
                r="5"
                class="meta-chart-dot"
                tabindex="0"
                role="button"
                :aria-label="`${entry.year} season ${entry.season}: ${percent(entry.rate)}`"
                @mouseenter="hoveredMetaPoint = entry"
                @mouseleave="hoveredMetaPoint = null"
                @focus="hoveredMetaPoint = entry"
                @blur="hoveredMetaPoint = null"
                @click="hoveredMetaPoint = entry"
                @keydown.enter.prevent="hoveredMetaPoint = entry"
                @keydown.space.prevent="hoveredMetaPoint = entry"
              />
            </svg>
            <div
              v-if="hoveredMetaPoint"
              class="meta-chart-tooltip"
              :style="{
                left: `${(hoveredMetaPoint.x / 620) * 100}%`,
                top: `${(hoveredMetaPoint.y / 180) * 100}%`,
              }"
            >
              <strong>{{ hoveredMetaPoint.year }} · S{{ hoveredMetaPoint.season }}</strong>
              <span>{{ percent(hoveredMetaPoint.rate) }} {{ t("priority") }}</span>
              <small>
                {{ hoveredMetaPoint.rank ? `${t("Rank")} #${hoveredMetaPoint.rank}` : t("Not a priority hero") }}
                <template v-if="hoveredMetaPoint.hero">
                  · {{ hoveredMetaPoint.hero.opening_ban_count }} {{ t("bans") }} ·
                  {{ hoveredMetaPoint.hero.blue_first_pick_count }} {{ t("Blue first picks") }}
                </template>
              </small>
            </div>
            <div class="meta-chart-labels">
              <span v-for="entry in metaSeries" :key="entry.league_id">{{ entry.year }} S{{ entry.season }}</span>
            </div>
          </div>
          <div class="meta-season-values">
            <article v-for="entry in metaSeries" :key="entry.league_id">
              <span>{{ entry.year }} · S{{ entry.season }}</span>
              <strong>{{ percent(entry.rate) }}</strong>
              <small>{{ entry.rank ? `${t("Rank")} #${entry.rank}` : t("Not a priority hero") }}</small>
            </article>
          </div>
        </template>
      </section>

    <section class="relation-tabs" aria-label="Relationship type">
      <button
        v-for="option in relationOptions"
        :key="option.value"
        type="button"
        :class="{ active: relation === option.value }"
        @click="selectRelation(option.value)"
      >
        <span>{{ option.short }}</span>
        <small>
          {{ number(payload?.source_counts?.[option.value]) }} patterns
        </small>
      </button>
    </section>
      <section class="filter-panel">
        <label v-if="relation === 'ban_response'">
          <span>Follow-up group</span>
          <select v-model="responseScope">
            <option value="all">All follow-ups</option>
            <option value="opponent_next_ban">Opponent's next ban</option>
            <option value="banning_team_later_pick">Banning team's picks</option>
            <option value="opponent_later_pick">Opponent's picks</option>
          </select>
        </label>
        <label>
          <span>Draft context</span>
          <select v-model="context">
            <option value="overall">All sides and slots</option>
            <option value="slot_context">Specific side and slot</option>
          </select>
        </label>
        <label v-if="context === 'slot_context'">
          <span>Responding side</span>
          <select v-model="side">
            <option value="all">Blue and red</option>
            <option value="blue">Blue</option>
            <option value="red">Red</option>
          </select>
        </label>
        <label>
          <span>Rank by</span>
          <select v-model="metric">
            <option
              v-for="option in metricOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </label>
        <label>
          <span>Minimum times seen</span>
          <input v-model.number="support" type="number" min="2" />
        </label>
        <label>
          <span>Results</span>
          <select v-model="resultCount">
            <option value="10">Top 10</option>
            <option value="20">Top 20</option>
            <option value="50">Top 50</option>
            <option value="100">Top 100</option>
            <option value="all">Show all (table: first 200)</option>
          </select>
        </label>
        <label class="search-control">
          <span>Find a hero</span>
          <input v-model="search" type="search" placeholder="Search hero name…" />
        </label>
      </section>

      <section class="insight-layout">
        <article class="chart-card">
          <div class="card-heading">
            <div>
              <p class="visual-eyebrow">Ranked patterns</p>
              <h2>{{ currentRelation.label }}</h2>
            </div>
            <span>{{ metricOptions.find((item) => item.value === metric)?.label }}</span>
          </div>

          <div v-if="shownRows.length" class="pattern-bars">
            <div
              v-for="(row, index) in shownRows"
              :key="`${row.relation}-${row.source_hero_id}-${row.target_hero_id}-${row.context_description}`"
              class="pattern-bar"
            >
              <span class="bar-rank">{{ index + 1 }}</span>
              <div class="hero-pair">
                <div class="hero-avatar">
                  <img
                    v-if="heroIcon(row.source_hero_id)"
                    :src="heroIcon(row.source_hero_id)"
                    :alt="row.source_hero_name"
                  />
                  <span v-else>{{ initial(row.source_hero_name) }}</span>
                </div>
                <div class="hero-avatar target">
                  <img
                    v-if="heroIcon(row.target_hero_id)"
                    :src="heroIcon(row.target_hero_id)"
                    :alt="row.target_hero_name"
                  />
                  <span v-else>{{ initial(row.target_hero_name) }}</span>
                </div>
              </div>
              <div class="bar-copy">
                <strong>{{ row.relationship }}</strong>
                <small>
                  {{ row.context_description }} · {{ row.selections }}/{{
                    row.opportunities
                  }}
                  legal chances
                </small>
                <div class="visual-track">
                  <span
                    :class="`relation-${row.relation}`"
                    :style="{ width: barWidth(row) }"
                  ></span>
                </div>
              </div>
              <strong class="bar-metric">{{ metricText(row) }}</strong>
            </div>
          </div>
          <div v-else class="no-patterns">
            No relationships match these filters.
          </div>
        </article>

        <aside class="method-card">
          <p class="visual-eyebrow">How to read this</p>
          <h2>Availability-adjusted</h2>
          <p>
            A hero enters the denominator only when it was legal at that exact
            draft decision. This prevents already-used or banned heroes from
            making selection rates look artificially low.
          </p>
          <dl>
            <div>
              <dt>Chance</dt>
              <dd>How often the pattern happened when the target was legal.</dd>
            </div>
            <div>
              <dt>Lift</dt>
              <dd>Pattern chance divided by the hero's usual legal chance.</dd>
            </div>
            <div>
              <dt>Likely range</dt>
              <dd>95% Wilson interval; wider means less certainty.</dd>
            </div>
          </dl>
          <p class="method-note">
            These are descriptive associations from past drafts, not proof that
            one hero caused another choice or a win.
          </p>
        </aside>
      </section>

      <section class="details-card">
        <div class="card-heading">
          <div>
            <p class="visual-eyebrow">Full detail</p>
            <h2>Pattern evidence</h2>
          </div>
          <span>Generated {{ new Date(payload.generated_at).toLocaleDateString() }}</span>
        </div>
        <div class="visual-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pattern</th>
                <th>Context</th>
                <th>Chosen / legal</th>
                <th>Chance</th>
                <th>Usual</th>
                <th>Lift</th>
                <th>Win rate</th>
                <th>Likely range</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in tableRows"
                :key="`table-${row.relation}-${row.source_hero_id}-${row.target_hero_id}-${row.context_description}`"
              >
                <td><strong>{{ row.relationship }}</strong></td>
                <td>{{ row.context_description }}</td>
                <td>{{ row.selections }} / {{ row.opportunities }}</td>
                <td>{{ percent(row.smoothed_probability) }}</td>
                <td>{{ percent(row.baseline_probability) }}</td>
                <td :class="{ positive: row.smoothed_lift > 1 }">
                  {{ row.smoothed_lift == null ? "—" : `${row.smoothed_lift.toFixed(2)}×` }}
                </td>
                <td>{{ percent(row.win_rate) }}</td>
                <td>{{ percent(row.ci_low) }}–{{ percent(row.ci_high) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="resultCount === 'all' && filteredRows.length > tableRows.length" class="table-limit-note">
            Showing the first {{ number(tableRows.length) }} matching rows. Narrow the filters to inspect the rest.
          </p>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.visual-page {
  width: min(1440px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 2.25rem 0 5rem;
}

.visual-eyebrow {
  margin: 0 0 0.5rem;
  color: var(--accent-deep);
  font-size: 0.68rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.explorer-heading h1 {
  margin: 0;
  font: 700 clamp(2rem, 4vw, 3rem)/1 var(--display);
  letter-spacing: -0.045em;
}

.explorer-heading p:last-child {
  max-width: 660px;
  margin: 0.65rem 0 0;
  color: var(--ink-soft);
  font-size: 0.76rem;
}

.season-control {
  display: grid;
  min-width: 330px;
  gap: 0.4rem;
}

.season-control span,
.filter-panel label > span {
  color: var(--ink-soft);
  font-size: 0.66rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.season-control select,
.filter-panel select,
.filter-panel input {
  min-height: 44px;
  padding: 0.65rem 0.75rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.9);
  color: var(--ink);
  font: inherit;
}

.season-control small {
  color: var(--ink-soft);
  font-size: 0.68rem;
}

.meta-section {
  margin-top: 1.5rem;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.explorer-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 2rem;
}

.meta-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 2rem;
}

.meta-heading h2 {
  margin: 0;
  font: 700 1.8rem/1 var(--display);
  letter-spacing: -0.04em;
}

.meta-heading p:last-child {
  margin: 0.55rem 0 0;
  color: var(--ink-soft);
  font-size: 0.75rem;
}

.meta-legend {
  display: flex;
  gap: 1rem;
  color: var(--ink-soft);
  font-size: 0.68rem;
  white-space: nowrap;
}

.meta-legend span {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.meta-legend i {
  width: 18px;
  height: 5px;
}

.ban-key,
.meta-ban {
  background: #c45c26;
}

.pick-key,
.meta-pick {
  background: var(--accent);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin-top: 1rem;
  border: 1px solid var(--line);
  background: var(--line);
}

.meta-hero {
  display: grid;
  grid-template-columns: 20px 42px minmax(0, 1fr) auto;
  gap: 0.65rem;
  align-items: center;
  min-width: 0;
  padding: 0.8rem;
  background: #fff;
}

.meta-hero:hover,
.meta-hero.active {
  background: rgba(15, 138, 107, 0.08);
}

.meta-hero:hover {
  cursor: pointer;
}

.meta-rank {
  color: var(--ink-soft);
  font-size: 0.7rem;
}

.meta-avatar {
  display: grid;
  width: 42px;
  height: 42px;
  aspect-ratio: 1;
  place-items: center;
  overflow: hidden;
  border-radius: 50%;
  background: #dbe7e1;
  color: var(--accent-deep);
  font-weight: 700;
}

.meta-avatar img {
  width: 100%;
  height: 100%;
  border-radius: inherit;
  object-fit: cover;
}

.meta-copy {
  min-width: 0;
}

.meta-copy strong,
.meta-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-copy strong {
  font-family: var(--display);
}

.meta-copy small {
  margin-top: 0.12rem;
  color: var(--ink-soft);
  font-size: 0.62rem;
}

.meta-track {
  display: flex;
  height: 5px;
  margin-top: 0.45rem;
  overflow: hidden;
  background: rgba(16, 42, 46, 0.07);
}

.meta-track span {
  display: block;
  height: 100%;
}

.meta-rate {
  color: var(--accent-deep);
  font-family: var(--display);
  text-align: right;
}

.meta-rate small {
  display: block;
  color: var(--ink-soft);
  font: 0.55rem var(--mono);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.meta-evolution {
  margin-top: 1rem;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.meta-evolution-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 1.5rem;
}

.meta-evolution-heading h2 {
  margin: 0;
  font: 700 1.8rem/1 var(--display);
  letter-spacing: -0.04em;
}

.meta-evolution-heading p:last-child,
.meta-selected-hero small {
  margin: 0.5rem 0 0;
  color: var(--ink-soft);
  font-size: 0.72rem;
}

.meta-evolution-heading label {
  display: grid;
  min-width: 180px;
  gap: 0.4rem;
}

.meta-hero-controls {
  display: flex;
  align-items: end;
  gap: 0.7rem;
}

.meta-evolution-heading label > span {
  color: var(--ink-soft);
  font-size: 0.66rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.meta-evolution-heading select {
  min-height: 42px;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  font: inherit;
}

.current-meta-icons {
  display: flex;
  max-width: 344px;
  gap: 0.25rem;
  overflow-x: auto;
  padding: 0.2rem;
}

.current-meta-icons button {
  position: relative;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  overflow: hidden;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;
  background: #dbe7e1;
}

.current-meta-icons button.active {
  border-color: var(--accent-deep);
  box-shadow: 0 0 0 2px rgba(15, 138, 107, 0.18);
}

.current-meta-icons img {
  width: 100%;
  height: 100%;
  border-radius: inherit;
  object-fit: cover;
}

.current-meta-icons small {
  position: absolute;
  right: -1px;
  bottom: -1px;
  min-width: 14px;
  padding: 0 2px;
  border-radius: 3px 0 0 0;
  background: var(--ink);
  color: #fff;
  font-size: 0.52rem;
  line-height: 1.2;
}

.meta-selected-hero {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 1.25rem;
}

.meta-selected-hero strong,
.meta-selected-hero small {
  display: block;
}

.meta-selected-hero strong {
  font: 700 1.1rem var(--display);
}

.meta-chart-wrap {
  position: relative;
  margin-top: 1rem;
  padding: 0.5rem 0.75rem 0;
  border: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(15, 138, 107, 0.08), transparent);
}

.meta-chart-wrap svg {
  display: block;
  width: 100%;
  height: 180px;
  overflow: visible;
}

.meta-chart-axis {
  stroke: rgba(16, 42, 46, 0.22);
  stroke-width: 1;
}

.meta-chart-line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.meta-chart-dot {
  fill: #fff;
  stroke: var(--accent-deep);
  stroke-width: 3;
  cursor: pointer;
}

.meta-chart-dot:hover {
  fill: var(--accent);
  stroke: #fff;
  stroke-width: 4;
}

.meta-chart-tooltip {
  position: absolute;
  z-index: 2;
  display: grid;
  min-width: 152px;
  gap: 0.18rem;
  padding: 0.55rem 0.65rem;
  border: 1px solid rgba(16, 42, 46, 0.2);
  background: var(--ink);
  color: #fff;
  font-size: 0.64rem;
  line-height: 1.35;
  pointer-events: none;
  transform: translate(-50%, calc(-100% - 8px));
}

.meta-chart-tooltip strong {
  font-family: var(--display);
  font-size: 0.8rem;
}

.meta-chart-tooltip span {
  color: #91e0c8;
}

.meta-chart-tooltip small {
  color: rgba(255, 255, 255, 0.72);
  font-size: 0.58rem;
}

.meta-chart-labels,
.meta-season-values {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
  gap: 0.5rem;
}

.meta-chart-labels {
  margin: -0.5rem 0 0.65rem;
  color: var(--ink-soft);
  font-size: 0.62rem;
  text-align: center;
}

.meta-season-values {
  margin-top: 0.75rem;
}

.meta-season-values article {
  display: grid;
  gap: 0.12rem;
  padding: 0.7rem;
  border: 1px solid var(--line);
  background: #fff;
}

.meta-season-values span,
.meta-season-values small {
  color: var(--ink-soft);
  font-size: 0.62rem;
}

.meta-season-values strong {
  color: var(--accent-deep);
  font: 700 1.05rem var(--display);
}

.meta-evolution-message {
  margin-top: 1rem;
  color: var(--ink-soft);
  font-size: 0.72rem;
}

.relation-tabs {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  margin-top: 1rem;
  border: 1px solid var(--line);
  background: var(--line);
}

.relation-tabs button {
  display: grid;
  gap: 0.25rem;
  padding: 1rem;
  border: 0;
  background: rgba(255, 255, 255, 0.9);
  color: var(--ink);
  text-align: left;
}

.relation-tabs button.active {
  background: var(--ink);
  color: #fff;
}

.relation-tabs span {
  font-family: var(--display);
  font-size: 1rem;
  font-weight: 700;
}

.relation-tabs small {
  color: inherit;
  opacity: 0.62;
}

.visual-message {
  margin: 1rem 0 0;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.75);
}

.visual-message.error {
  color: var(--warn);
}

.filter-panel {
  display: grid;
  grid-template-columns: repeat(6, minmax(130px, 1fr));
  gap: 0.65rem;
  margin-top: 1rem;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.76);
}

.filter-panel label {
  display: grid;
  gap: 0.35rem;
}

.filter-panel .search-control {
  grid-column: span 2;
}

.insight-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.65fr);
  gap: 0.7rem;
  margin-top: 0.7rem;
}

.chart-card,
.method-card,
.details-card {
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.chart-card,
.method-card,
.details-card {
  padding: 1.2rem;
}

.card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.card-heading h2,
.method-card h2 {
  margin: 0;
  font: 700 1.65rem/1.05 var(--display);
  letter-spacing: -0.035em;
}

.card-heading > span {
  color: var(--ink-soft);
  font-size: 0.68rem;
}

.pattern-bars {
  margin-top: 1rem;
}

.pattern-bar {
  display: grid;
  grid-template-columns: 26px 64px minmax(0, 1fr) 70px;
  gap: 0.8rem;
  align-items: center;
  padding: 0.68rem 0;
  border-top: 1px solid var(--line);
}

.bar-rank {
  color: var(--ink-soft);
  font-size: 0.7rem;
}

.hero-pair {
  display: flex;
  align-items: center;
}

.hero-avatar {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  overflow: hidden;
  border: 2px solid #fff;
  border-radius: 50%;
  background: #dbe7e1;
  color: var(--accent-deep);
  font-weight: 700;
}

.hero-avatar.target {
  margin-left: -12px;
}

.hero-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.bar-copy {
  min-width: 0;
}

.bar-copy strong,
.bar-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-copy strong {
  font-size: 0.82rem;
}

.bar-copy small {
  margin-top: 0.1rem;
  color: var(--ink-soft);
  font-size: 0.66rem;
}

.visual-track {
  height: 5px;
  margin-top: 0.45rem;
  overflow: hidden;
  background: rgba(16, 42, 46, 0.07);
}

.visual-track span {
  display: block;
  height: 100%;
  background: var(--accent);
}

.visual-track .relation-counter-ban {
  background: #c45c26;
}

.visual-track .relation-pick-synergy {
  background: #6b61b6;
}

.visual-track .relation-ban-response {
  background: #bc8a26;
}

.bar-metric {
  text-align: right;
  font-family: var(--display);
  font-size: 1rem;
}

.method-card > p:not(.visual-eyebrow, .method-note) {
  color: var(--ink-soft);
  line-height: 1.65;
}

.method-card dl {
  margin: 1.25rem 0 0;
}

.method-card dl div {
  padding: 0.75rem 0;
  border-top: 1px solid var(--line);
}

.method-card dt {
  font-weight: 700;
}

.method-card dd {
  margin: 0.25rem 0 0;
  color: var(--ink-soft);
  font-size: 0.72rem;
}

.method-note {
  margin: 1rem 0 0;
  padding-left: 0.75rem;
  border-left: 2px solid var(--accent);
  color: var(--ink-soft);
  font-size: 0.7rem;
  line-height: 1.6;
}

.details-card {
  margin-top: 0.7rem;
}

.visual-table-wrap {
  max-height: 680px;
  margin-top: 1rem;
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.72rem;
}

th,
td {
  padding: 0.72rem 0.65rem;
  border-bottom: 1px solid var(--line);
  text-align: left;
  white-space: nowrap;
}

th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #eef3f0;
  color: var(--ink-soft);
  font-size: 0.64rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.positive {
  color: var(--accent-deep);
  font-weight: 700;
}

.no-patterns {
  padding: 3rem 1rem;
  color: var(--ink-soft);
  text-align: center;
}

@media (max-width: 1050px) {
  .meta-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-panel {
    grid-template-columns: repeat(3, 1fr);
  }

  .insight-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 740px) {
  .visual-page {
    width: calc(100% - 1rem);
    padding-top: 0.75rem;
  }

  .explorer-heading {
    align-items: stretch;
    flex-direction: column;
    gap: 1.5rem;
    padding: 1.4rem;
  }

  .season-control {
    min-width: 0;
  }

  .relation-tabs {
    grid-template-columns: repeat(2, 1fr);
  }

  .meta-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 0.75rem;
  }

  .meta-evolution-heading,
  .meta-hero-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .meta-evolution-heading label {
    min-width: 0;
  }

  .current-meta-icons {
    max-width: 100%;
  }

  .meta-grid {
    grid-template-columns: 1fr;
  }

  .filter-panel {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-panel label,
  .filter-panel select,
  .filter-panel input {
    min-width: 0;
  }

  .filter-panel .search-control {
    grid-column: span 2;
  }

  .pattern-bar {
    grid-template-columns: 22px 52px minmax(0, 1fr) 58px;
    gap: 0.45rem;
  }

  .hero-avatar {
    width: 32px;
    height: 32px;
  }
}
</style>
