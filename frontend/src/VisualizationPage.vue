<script setup>
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchVisualizationPatterns,
  fetchVisualizationSeasons,
} from "./api";

const seasons = ref([]);
const leagueId = ref("");
const payload = ref(null);
const loading = ref(false);
const error = ref("");

const relation = ref("counter_pick");
const responseScope = ref("all");
const context = ref("overall");
const side = ref("all");
const metric = ref("smoothed_lift");
const support = ref(3);
const resultCount = ref("20");
const search = ref("");

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

const overallRows = computed(() =>
  rows.value.filter((row) => row.context_level === "overall" && !row.is_peak_battle)
);

const totalPatternRecords = computed(() =>
  Object.values(payload.value?.source_counts || {}).reduce(
    (sum, count) => sum + Number(count || 0),
    0
  )
);

const uniqueHeroCount = computed(() => {
  const heroIds = new Set();
  overallRows.value.forEach((row) => {
    if (row.source_hero_id) heroIds.add(row.source_hero_id);
    if (row.target_hero_id) heroIds.add(row.target_hero_id);
  });
  (payload.value?.meta_heroes || []).forEach((hero) => {
    if (hero.hero_id) heroIds.add(hero.hero_id);
  });
  return heroIds.size;
});

const observedSelections = computed(() =>
  overallRows.value.reduce((sum, row) => sum + Number(row.selections || 0), 0)
);

const topPriorityHero = computed(() => topMetaHeroes.value[0] || null);

function strongestOverallPattern(relationType) {
  return overallRows.value
    .filter(
      (row) =>
        row.relation === relationType &&
        row.selections >= 3 &&
        row.smoothed_lift != null
    )
    .sort(
      (a, b) =>
        Number(b.smoothed_lift) - Number(a.smoothed_lift) ||
        b.selections - a.selections
    )[0];
}

const strongestCounter = computed(() => strongestOverallPattern("counter_pick"));
const strongestSynergy = computed(() => strongestOverallPattern("pick_synergy"));

const filteredRows = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase();
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
  if (resultCount.value === "all") return filteredRows.value;
  return filteredRows.value.slice(0, Number(resultCount.value));
});

const maximumMetric = computed(() =>
  Math.max(...shownRows.value.map((row) => Math.max(0, metricValue(row))), 0.001)
);

const totalSelections = computed(() =>
  shownRows.value.reduce((sum, row) => sum + row.selections, 0)
);

const medianLift = computed(() => {
  const values = shownRows.value
    .map((row) => row.smoothed_lift)
    .filter((value) => value != null)
    .sort((a, b) => a - b);
  if (!values.length) return null;
  const middle = Math.floor(values.length / 2);
  return values.length % 2
    ? values[middle]
    : (values[middle - 1] + values[middle]) / 2;
});

const strongestPattern = computed(() => shownRows.value[0] || null);

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
  return Math.round(value).toLocaleString();
}

function percent(value) {
  return value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
}

function number(value) {
  return Number(value || 0).toLocaleString();
}

function initial(name) {
  return String(name || "?").slice(0, 1);
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
  if (!leagueId.value && seasons.value.length) {
    const preferred = seasons.value.find(
      (season) => season.league_id === "20260001"
    );
    leagueId.value = (preferred || seasons.value[0]).league_id;
  }
}

async function loadPatterns() {
  if (!leagueId.value) return;
  loading.value = true;
  error.value = "";
  try {
    payload.value = await fetchVisualizationPatterns({
      leagueId: leagueId.value,
      minSelections: 2,
    });
  } catch (err) {
    payload.value = null;
    error.value = err.message || "Could not load this season's patterns.";
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadPatterns();
  } catch (err) {
    error.value = err.message || "Could not load visualization data.";
  }
});

watch(leagueId, loadPatterns);
watch(relation, () => {
  if (relation.value !== "ban_response") responseScope.value = "all";
});

function openTeamsPage() {
  window.history.pushState({}, "", "/teams");
  window.dispatchEvent(new PopStateEvent("popstate"));
}
</script>

<template>
  <main class="visual-page">
    <header class="visual-hero">
      <div class="hero-copy">
        <p class="visual-eyebrow">Honor of Kings · Season intelligence</p>
        <h1>Overall Stats</h1>
        <p>
          A season-wide view of the heroes, priorities, counters, and draft
          patterns shaping professional play.
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
    </header>

    <p v-if="error" class="visual-message error">{{ error }}</p>
    <p v-else-if="loading" class="visual-message">Loading season overview…</p>

    <template v-if="payload && !loading">
      <section class="overview-summary" aria-label="Season summary">
        <article>
          <span>Heroes analyzed</span>
          <strong>{{ number(uniqueHeroCount) }}</strong>
          <small>in season draft data</small>
        </article>
        <article>
          <span>Pattern records</span>
          <strong>{{ number(totalPatternRecords) }}</strong>
          <small>across picks, bans, and responses</small>
        </article>
        <article>
          <span>Observed selections</span>
          <strong>{{ number(observedSelections) }}</strong>
          <small>in overall relationship stats</small>
        </article>
        <article class="overview-highlight">
          <span>Highest draft priority</span>
          <strong>{{ topPriorityHero?.hero_name || "—" }}</strong>
          <small>
            {{ topPriorityHero ? `${percent(topPriorityHero.early_priority_rate)} early priority` : "No priority data" }}
          </small>
        </article>
      </section>

      <section class="overview-feature-grid">
        <article class="season-signal">
          <p class="visual-eyebrow">Strongest counter signal</p>
          <h2>{{ strongestCounter?.relationship || "Not enough data" }}</h2>
          <p v-if="strongestCounter">
            {{ strongestCounter.smoothed_lift.toFixed(2) }}× the usual selection
            rate across {{ number(strongestCounter.selections) }} picks.
          </p>
        </article>
        <article class="season-signal synergy-signal">
          <p class="visual-eyebrow">Strongest synergy signal</p>
          <h2>{{ strongestSynergy?.relationship || "Not enough data" }}</h2>
          <p v-if="strongestSynergy">
            {{ strongestSynergy.smoothed_lift.toFixed(2) }}× the usual selection
            rate across {{ number(strongestSynergy.selections) }} pairings.
          </p>
        </article>
        <article class="teams-cta">
          <div>
            <p class="visual-eyebrow">Team analysis</p>
            <h2>Compare team identities</h2>
            <p>Browse preferred hero pairs and draft tendencies team by team.</p>
          </div>
          <button class="teams-cta-button" type="button" @click="openTeamsPage">
            Open teams →
          </button>
        </article>
      </section>

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
        >
          <span class="meta-rank">{{ hero.priority_rank }}</span>
          <div class="meta-avatar">
            <img
              v-if="hero.hero_icon"
              :src="hero.hero_icon"
              :alt="hero.hero_name"
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

    <section class="explorer-heading">
      <div>
        <p class="visual-eyebrow">Deep dive</p>
        <h2>Draft Pattern Explorer</h2>
        <p>
          Filter the season data to investigate specific counters, combinations,
          and ban responses.
        </p>
      </div>
    </section>

    <section class="relation-tabs" aria-label="Relationship type">
      <button
        v-for="option in relationOptions"
        :key="option.value"
        type="button"
        :class="{ active: relation === option.value }"
        @click="relation = option.value"
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
            <option value="all">Show all</option>
          </select>
        </label>
        <label class="search-control">
          <span>Find a hero</span>
          <input v-model="search" type="search" placeholder="Search hero name…" />
        </label>
      </section>

      <section class="visual-summary">
        <article>
          <span>Matching patterns</span>
          <strong>{{ number(filteredRows.length) }}</strong>
          <small>after current filters</small>
        </article>
        <article>
          <span>Typical lift</span>
          <strong>{{ medianLift == null ? "—" : `${medianLift.toFixed(2)}×` }}</strong>
          <small>versus normal selection rate</small>
        </article>
        <article>
          <span>Shown selections</span>
          <strong>{{ number(totalSelections) }}</strong>
          <small>observed choices across results</small>
        </article>
        <article class="summary-highlight">
          <span>Strongest shown</span>
          <strong>{{ strongestPattern ? metricText(strongestPattern) : "—" }}</strong>
          <small>{{ strongestPattern?.relationship || "No matching pattern" }}</small>
        </article>
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
                    v-if="row.source_hero_icon"
                    :src="row.source_hero_icon"
                    :alt="row.source_hero_name"
                  />
                  <span v-else>{{ initial(row.source_hero_name) }}</span>
                </div>
                <div class="hero-avatar target">
                  <img
                    v-if="row.target_hero_icon"
                    :src="row.target_hero_icon"
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
                v-for="row in shownRows"
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

.visual-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 3rem;
  padding: 2.6rem;
  border: 1px solid rgba(16, 42, 46, 0.11);
  background:
    radial-gradient(circle at 82% 20%, rgba(31, 184, 144, 0.2), transparent 28%),
    linear-gradient(135deg, #f8fbf9 0%, #e7f1ec 100%);
  box-shadow: 0 24px 70px rgba(16, 42, 46, 0.08);
}

.visual-eyebrow {
  margin: 0 0 0.5rem;
  color: var(--accent-deep);
  font-size: 0.68rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.hero-copy h1 {
  margin: 0;
  font: 800 clamp(3.2rem, 9vw, 6.8rem)/0.85 var(--display);
  letter-spacing: -0.065em;
}

.hero-copy > p:last-child {
  max-width: 660px;
  margin: 1.25rem 0 0;
  color: var(--ink-soft);
  font-size: 0.95rem;
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

.overview-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin-top: 1rem;
  border: 1px solid var(--line);
  background: var(--line);
}

.overview-summary article {
  min-width: 0;
  padding: 1.25rem;
  background: rgba(255, 255, 255, 0.9);
}

.overview-summary .overview-highlight {
  background: var(--ink);
  color: #fff;
}

.overview-summary span,
.overview-summary small {
  display: block;
  color: var(--ink-soft);
}

.overview-summary .overview-highlight span,
.overview-summary .overview-highlight small {
  color: rgba(255, 255, 255, 0.65);
}

.overview-summary span {
  font-size: 0.65rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.overview-summary strong {
  display: block;
  margin: 0.55rem 0 0.25rem;
  overflow: hidden;
  font: 700 clamp(1.8rem, 4vw, 2.7rem)/1 var(--display);
  letter-spacing: -0.04em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-summary small {
  overflow: hidden;
  font-size: 0.68rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
  margin-top: 0.75rem;
}

.season-signal,
.overview-feature-grid .teams-cta {
  min-height: 190px;
  margin: 0;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.season-signal {
  position: relative;
  overflow: hidden;
}

.season-signal::after {
  position: absolute;
  right: -35px;
  bottom: -55px;
  width: 150px;
  height: 150px;
  border: 24px solid rgba(15, 138, 107, 0.08);
  border-radius: 50%;
  content: "";
}

.season-signal.synergy-signal::after {
  border-color: rgba(107, 97, 182, 0.09);
}

.season-signal h2 {
  position: relative;
  z-index: 1;
  max-width: 22rem;
  margin: 1.6rem 0 0;
  font: 700 clamp(1.35rem, 2.5vw, 2rem)/1.05 var(--display);
  letter-spacing: -0.045em;
}

.season-signal > p:last-child {
  position: relative;
  z-index: 1;
  max-width: 28rem;
  margin: 0.7rem 0 0;
  color: var(--ink-soft);
  font-size: 0.72rem;
}

.teams-cta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  margin-top: 0.85rem;
  padding: 1.15rem 1.25rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.8);
}

.teams-cta h2 {
  margin: 0;
  font: 700 1.45rem/1 var(--display);
  letter-spacing: -0.03em;
}

.teams-cta p:last-child {
  margin: 0.4rem 0 0;
  color: var(--ink-soft);
  font-size: 0.74rem;
}

.teams-cta-button {
  min-height: 42px;
  padding: 0.55rem 0.9rem;
  border: 1px solid var(--accent-deep);
  background: var(--accent);
  color: #fff;
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
}

.overview-feature-grid .teams-cta {
  align-items: flex-start;
  flex-direction: column;
}

.overview-feature-grid .teams-cta-button {
  margin-top: auto;
}

.meta-section {
  margin-top: 1rem;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.explorer-heading {
  display: flex;
  justify-content: space-between;
  margin-top: 3rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--line);
}

.explorer-heading h2 {
  margin: 0;
  font: 700 clamp(1.8rem, 4vw, 2.6rem)/1 var(--display);
  letter-spacing: -0.045em;
}

.explorer-heading p:last-child {
  max-width: 620px;
  margin: 0.65rem 0 0;
  color: var(--ink-soft);
  font-size: 0.76rem;
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

.meta-rank {
  color: var(--ink-soft);
  font-size: 0.7rem;
}

.meta-avatar {
  display: grid;
  width: 42px;
  height: 42px;
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

.visual-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.7rem;
  margin-top: 0.7rem;
}

.visual-summary article {
  min-width: 0;
  padding: 1rem 1.1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.75);
}

.visual-summary .summary-highlight {
  border-color: rgba(15, 138, 107, 0.3);
  background: rgba(15, 138, 107, 0.08);
}

.visual-summary span,
.visual-summary small {
  display: block;
  color: var(--ink-soft);
}

.visual-summary span {
  font-size: 0.65rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

.visual-summary strong {
  display: block;
  margin: 0.45rem 0 0.2rem;
  overflow: hidden;
  font: 700 2rem/1 var(--display);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.visual-summary small {
  overflow: hidden;
  font-size: 0.7rem;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  .overview-summary,
  .overview-feature-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview-feature-grid .teams-cta {
    grid-column: span 2;
  }

  .meta-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-panel {
    grid-template-columns: repeat(3, 1fr);
  }

  .visual-summary {
    grid-template-columns: repeat(2, 1fr);
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

  .visual-hero,
  .teams-cta {
    align-items: stretch;
    flex-direction: column;
    gap: 1.5rem;
    padding: 1.4rem;
  }

  .teams-cta-button {
    width: 100%;
  }

  .season-control {
    min-width: 0;
  }

  .overview-summary,
  .overview-feature-grid {
    grid-template-columns: 1fr;
  }

  .overview-feature-grid .teams-cta {
    grid-column: auto;
  }

  .season-signal,
  .overview-feature-grid .teams-cta {
    min-height: 0;
  }

  .relation-tabs {
    grid-template-columns: repeat(2, 1fr);
  }

  .meta-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 0.75rem;
  }

  .meta-grid {
    grid-template-columns: 1fr;
  }

  .filter-panel {
    grid-template-columns: 1fr 1fr;
  }

  .filter-panel .search-control {
    grid-column: span 2;
  }

  .visual-summary {
    grid-template-columns: 1fr;
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
