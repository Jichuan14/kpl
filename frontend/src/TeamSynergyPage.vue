<script setup>
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchTeamSynergies,
  fetchVisualizationSeasons,
} from "./api";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { language } from "./i18n";

const seasons = ref([]);
const leagueId = selectedLeagueId;
const teamId = ref("");
const payload = ref(null);
const loading = ref(false);
const error = ref("");

const metric = ref("selection_count");
const support = ref(3);
const resultCount = ref("20");
const search = ref("");

const metricOptions = [
  { value: "selection_count", label: "Most used together" },
  { value: "smoothed_lift", label: "More common than team baseline" },
  {
    value: "smoothed_completion_probability",
    label: "Most likely pair completion",
  },
  { value: "battle_win_rate_when_paired", label: "Best battle win rate" },
];

const teams = computed(() => payload.value?.teams || []);
const allRows = computed(() => payload.value?.rows || []);
const selectedTeam = computed(() =>
  teams.value.find((team) => team.team_id === teamId.value)
);

const filteredRows = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase();
  return allRows.value
    .filter(
      (row) =>
        row.team_id === teamId.value &&
        row.selection_count >= Number(support.value || 1) &&
        (!needle ||
          row.hero_a_name.toLocaleLowerCase().includes(needle) ||
          row.hero_b_name.toLocaleLowerCase().includes(needle))
    )
    .sort(
      (a, b) =>
        metricValue(b) - metricValue(a) ||
        b.selection_count - a.selection_count ||
        a.pair_name.localeCompare(b.pair_name)
    );
});

const shownRows = computed(() =>
  resultCount.value === "all"
    ? filteredRows.value
    : filteredRows.value.slice(0, Number(resultCount.value))
);

const maximumMetric = computed(() =>
  Math.max(...shownRows.value.map((row) => Math.max(0, metricValue(row))), 0.001)
);

const totalPairUses = computed(() =>
  filteredRows.value.reduce((sum, row) => sum + row.selection_count, 0)
);

const averageWinRate = computed(() => {
  const selections = filteredRows.value.reduce(
    (sum, row) => sum + row.selection_count,
    0
  );
  const wins = filteredRows.value.reduce(
    (sum, row) => sum + row.battle_win_count_when_paired,
    0
  );
  return selections ? wins / selections : null;
});

function metricValue(row) {
  const value = row?.[metric.value];
  return value == null ? Number.NEGATIVE_INFINITY : Number(value);
}

function metricText(row) {
  const value = metricValue(row);
  if (!Number.isFinite(value)) return "—";
  if (
    metric.value === "smoothed_completion_probability" ||
    metric.value === "battle_win_rate_when_paired"
  ) {
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

function initial(name) {
  return String(name || "?").slice(0, 1);
}

function barWidth(row) {
  const value = Math.max(0, metricValue(row));
  return `${Math.max(1.5, (value / maximumMetric.value) * 100)}%`;
}

function selectTeam(nextTeamId) {
  teamId.value = nextTeamId;
}

async function loadSeasons() {
  const allSeasons = (await fetchVisualizationSeasons()) || [];
  seasons.value = allSeasons.filter((season) => season.team_synergy_ready);
  selectAvailableLeague(seasons.value);
}

async function loadTeamSynergies() {
  if (!leagueId.value) return;
  loading.value = true;
  error.value = "";
  try {
    payload.value = await fetchTeamSynergies({
      leagueId: leagueId.value,
      minSelections: 2,
    });
    if (!teams.value.some((team) => team.team_id === teamId.value)) {
      teamId.value = teams.value[0]?.team_id || "";
    }
  } catch (err) {
    payload.value = null;
    teamId.value = "";
    error.value = err.message || "Could not load team synergies.";
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadTeamSynergies();
  } catch (err) {
    error.value = err.message || "Could not load team synergy data.";
  }
});

watch(leagueId, loadTeamSynergies);
</script>

<template>
  <main class="teams-page">
    <header class="teams-hero">
      <div>
        <p class="teams-eyebrow">Team identity · Draft combinations</p>
        <h1>Team Synergy Lab</h1>
        <p>
          Browse each team’s preferred hero pairs—ranked by how often they
          complete a combination when the second hero is still legal.
        </p>
      </div>
      <label class="season-control">
        <span>Competition</span>
        <select v-model="leagueId">
          <option v-if="!seasons.length" value="">No team data yet</option>
          <option
            v-for="season in seasons"
            :key="season.league_id"
            :value="season.league_id"
          >
            {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
          </option>
        </select>
      </label>
    </header>

    <p v-if="error" class="teams-message error">{{ error }}</p>
    <p v-else-if="loading" class="teams-message">Loading team combinations…</p>

    <template v-if="payload && selectedTeam && !loading">
      <section class="teams-workspace">
        <aside class="team-directory">
          <p class="teams-eyebrow">Browse by team</p>
          <h2>{{ teams.length }} teams</h2>
          <button
            v-for="team in teams"
            :key="team.team_id"
            type="button"
            class="team-directory-item"
            :class="{ active: team.team_id === teamId }"
            @click="selectTeam(team.team_id)"
          >
            <strong>{{ team.team_name }}</strong>
            <small>
              {{ team.battle_count }} battles · {{ team.pair_count }} pairs
            </small>
          </button>
        </aside>

        <div class="team-detail">
          <section class="team-banner">
            <div>
              <p class="teams-eyebrow">Selected team</p>
              <h2>{{ selectedTeam.team_name }}</h2>
              <span>
                {{ payload.league.league_name }} ·
                {{ selectedTeam.battle_count }} battles
              </span>
            </div>
            <div class="team-banner-stats">
              <div>
                <strong>{{ number(selectedTeam.pair_count) }}</strong>
                <span>eligible pairs</span>
              </div>
              <div>
                <strong>{{ number(selectedTeam.total_pair_selections) }}</strong>
                <span>pair uses</span>
              </div>
            </div>
          </section>

          <section class="teams-filters">
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
              <span>Used at least</span>
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
            <label class="team-search">
              <span>Find a hero</span>
              <input
                v-model="search"
                type="search"
                placeholder="Search hero name…"
              />
            </label>
          </section>

          <section class="team-summary">
            <article>
              <span>Matching pairs</span>
              <strong>{{ number(filteredRows.length) }}</strong>
              <small>after current filters</small>
            </article>
            <article>
              <span>Total pair uses</span>
              <strong>{{ number(totalPairUses) }}</strong>
              <small>across matching combinations</small>
            </article>
            <article>
              <span>Weighted win rate</span>
              <strong>{{ percent(averageWinRate) }}</strong>
              <small>when these pairs were completed</small>
            </article>
            <article class="team-highlight">
              <span>Top shown pair</span>
              <strong>{{ shownRows[0]?.pair_name || "—" }}</strong>
              <small>
                {{ shownRows[0] ? metricText(shownRows[0]) : "No result" }}
              </small>
            </article>
          </section>

          <section class="teams-layout">
            <article class="pair-rankings">
              <div class="teams-heading">
                <div>
                  <p class="teams-eyebrow">Pair rankings</p>
                  <h2>{{ selectedTeam.team_name }} combinations</h2>
                </div>
                <span>
                  {{
                    metricOptions.find((item) => item.value === metric)?.label
                  }}
                </span>
              </div>

              <div v-if="shownRows.length" class="pair-list">
                <article
                  v-for="(row, index) in shownRows"
                  :key="`${row.team_id}-${row.hero_a_id}-${row.hero_b_id}`"
                  class="pair-row"
                >
                  <span class="pair-rank">{{ index + 1 }}</span>
                  <div class="pair-portraits">
                    <div class="pair-avatar">
                      <img
                        v-if="row.hero_a_icon"
                        :src="row.hero_a_icon"
                        :alt="row.hero_a_name"
                      />
                      <span v-else>{{ initial(row.hero_a_name) }}</span>
                    </div>
                    <div class="pair-avatar second">
                      <img
                        v-if="row.hero_b_icon"
                        :src="row.hero_b_icon"
                        :alt="row.hero_b_name"
                      />
                      <span v-else>{{ initial(row.hero_b_name) }}</span>
                    </div>
                  </div>
                  <div class="pair-copy">
                    <strong>{{ row.pair_name }}</strong>
                    <small>
                      Used {{ row.selection_count }} times ·
                      {{ row.legal_completion_opportunity_count }} legal
                      completion chances
                    </small>
                    <div class="pair-track">
                      <span :style="{ width: barWidth(row) }"></span>
                    </div>
                  </div>
                  <strong class="pair-metric">{{ metricText(row) }}</strong>
                </article>
              </div>
              <div v-else class="pairs-empty">No pairs match these filters.</div>
            </article>

            <aside class="team-method">
              <p class="teams-eyebrow">Reading the results</p>
              <h2>What does “team synergy” mean?</h2>
              <p>
                Once one member of a pair is visible, each later pick decision is
                a completion opportunity only when the other hero remains legal.
              </p>
              <dl>
                <div>
                  <dt>Pair uses</dt>
                  <dd>How many drafts included both heroes for this team.</dd>
                </div>
                <div>
                  <dt>Completion chance</dt>
                  <dd>
                    How often the team completed the pair when it legally could.
                  </dd>
                </div>
                <div>
                  <dt>Lift</dt>
                  <dd>
                    Completion chance compared with this team’s normal candidate
                    pick rate.
                  </dd>
                </div>
              </dl>
              <p class="method-warning">
                A→B and B→A are merged into one pair. Results describe
                preference, not whether the pairing caused a win.
              </p>
            </aside>
          </section>

          <section class="team-table-card">
            <div class="teams-heading">
              <div>
                <p class="teams-eyebrow">Evidence</p>
                <h2>All shown pairs</h2>
              </div>
              <span>
                Generated
                {{ new Date(payload.generated_at).toLocaleDateString() }}
              </span>
            </div>
            <div class="team-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Hero pair</th>
                    <th>Used</th>
                    <th>Legal chances</th>
                    <th>Completion chance</th>
                    <th>Team baseline</th>
                    <th>Lift</th>
                    <th>Win rate</th>
                    <th>Likely range</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in shownRows"
                    :key="`table-${row.team_id}-${row.hero_a_id}-${row.hero_b_id}`"
                  >
                    <td><strong>{{ row.pair_name }}</strong></td>
                    <td>{{ row.selection_count }}</td>
                    <td>{{ row.legal_completion_opportunity_count }}</td>
                    <td>{{ percent(row.smoothed_completion_probability) }}</td>
                    <td>{{ percent(row.team_baseline_completion_probability) }}</td>
                    <td :class="{ positive: row.smoothed_lift > 1 }">
                      {{
                        row.smoothed_lift == null
                          ? "—"
                          : `${row.smoothed_lift.toFixed(2)}×`
                      }}
                    </td>
                    <td>{{ percent(row.battle_win_rate_when_paired) }}</td>
                    <td>
                      {{ percent(row.probability_ci95_low) }}–{{
                        percent(row.probability_ci95_high)
                      }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.teams-page {
  width: min(1440px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 2.25rem 0 5rem;
}

.teams-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 3rem;
  padding: clamp(2rem, 5vw, 3.5rem);
  border: 1px solid var(--line);
  background:
    radial-gradient(circle at 78% 18%, rgba(196, 92, 38, 0.16), transparent 25%),
    linear-gradient(135deg, #fbfaf7, #e9f1ed);
}

.teams-eyebrow {
  margin: 0 0 0.5rem;
  color: var(--accent-deep);
  font-size: 0.67rem;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

.teams-hero h1 {
  margin: 0;
  font: 800 clamp(3rem, 8vw, 6rem)/0.88 var(--display);
  letter-spacing: -0.06em;
}

.teams-hero > div:first-child > p:last-child {
  max-width: 650px;
  margin: 1.2rem 0 0;
  color: var(--ink-soft);
}

.season-control {
  display: grid;
  min-width: 320px;
  gap: 0.35rem;
}

.season-control span,
.teams-filters label > span {
  color: var(--ink-soft);
  font-size: 0.65rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

select,
input {
  min-height: 43px;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.92);
  color: var(--ink);
  font: inherit;
}

.teams-message {
  margin-top: 0.8rem;
  padding: 1rem;
  border: 1px solid var(--line);
  background: #fff;
}

.teams-message.error {
  color: var(--warn);
}

.teams-workspace {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  gap: 0.75rem;
  margin-top: 0.8rem;
  align-items: start;
}

.team-directory {
  position: sticky;
  top: 0.75rem;
  max-height: calc(100vh - 1.5rem);
  overflow: auto;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.88);
}

.team-directory h2 {
  margin: 0 0 0.85rem;
  font: 700 1.4rem/1 var(--display);
}

.team-directory-item {
  display: grid;
  gap: 0.2rem;
  width: 100%;
  margin-bottom: 0.35rem;
  padding: 0.7rem 0.75rem;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ink);
  text-align: left;
}

.team-directory-item.active {
  border-color: rgba(15, 138, 107, 0.28);
  background: rgba(15, 138, 107, 0.08);
}

.team-directory-item strong {
  font-family: var(--display);
}

.team-directory-item small {
  color: var(--ink-soft);
  font-size: 0.62rem;
}

.team-banner {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 2rem;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.team-banner h2 {
  margin: 0;
  font: 800 2.4rem/1 var(--display);
  letter-spacing: -0.045em;
}

.team-banner > div > span {
  display: block;
  margin-top: 0.4rem;
  color: var(--ink-soft);
  font-size: 0.7rem;
}

.team-banner-stats {
  display: flex;
  gap: 2rem;
}

.team-banner-stats strong,
.team-banner-stats span {
  display: block;
  text-align: right;
}

.team-banner-stats strong {
  font: 700 1.5rem var(--display);
}

.team-banner-stats span {
  color: var(--ink-soft);
  font-size: 0.62rem;
  text-transform: uppercase;
}

.teams-filters {
  display: grid;
  grid-template-columns: 1.2fr 0.7fr 0.7fr 1.4fr;
  gap: 0.65rem;
  margin-top: 0.7rem;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.76);
}

.teams-filters label {
  display: grid;
  gap: 0.35rem;
}

.team-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.7rem;
  margin-top: 0.7rem;
}

.team-summary article {
  min-width: 0;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.78);
}

.team-summary .team-highlight {
  background: rgba(196, 92, 38, 0.07);
  border-color: rgba(196, 92, 38, 0.25);
}

.team-summary span,
.team-summary small {
  display: block;
  color: var(--ink-soft);
}

.team-summary span {
  font-size: 0.64rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.team-summary strong {
  display: block;
  margin: 0.4rem 0 0.2rem;
  overflow: hidden;
  font: 700 1.75rem/1 var(--display);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.team-summary small {
  overflow: hidden;
  font-size: 0.67rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.teams-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.65fr);
  gap: 0.7rem;
  margin-top: 0.7rem;
}

.pair-rankings,
.team-method,
.team-table-card {
  padding: 1.2rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.82);
}

.teams-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.teams-heading h2,
.team-method h2 {
  margin: 0;
  font: 700 1.65rem/1 var(--display);
  letter-spacing: -0.035em;
}

.teams-heading > span {
  color: var(--ink-soft);
  font-size: 0.67rem;
}

.pair-list {
  margin-top: 1rem;
}

.pair-row {
  display: grid;
  grid-template-columns: 25px 65px minmax(0, 1fr) 70px;
  gap: 0.75rem;
  align-items: center;
  padding: 0.7rem 0;
  border-top: 1px solid var(--line);
}

.pair-rank {
  color: var(--ink-soft);
  font-size: 0.68rem;
}

.pair-portraits {
  display: flex;
}

.pair-avatar {
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

.pair-avatar.second {
  margin-left: -12px;
}

.pair-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.pair-copy {
  min-width: 0;
}

.pair-copy strong,
.pair-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pair-copy small {
  margin-top: 0.12rem;
  color: var(--ink-soft);
  font-size: 0.64rem;
}

.pair-track {
  height: 5px;
  margin-top: 0.45rem;
  overflow: hidden;
  background: rgba(16, 42, 46, 0.07);
}

.pair-track span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #c45c26);
}

.pair-metric {
  text-align: right;
  font-family: var(--display);
}

.team-method > p:not(.teams-eyebrow, .method-warning) {
  color: var(--ink-soft);
  line-height: 1.65;
}

.team-method dl {
  margin: 1rem 0 0;
}

.team-method dl div {
  padding: 0.75rem 0;
  border-top: 1px solid var(--line);
}

.team-method dt {
  font-weight: 700;
}

.team-method dd {
  margin: 0.25rem 0 0;
  color: var(--ink-soft);
  font-size: 0.7rem;
}

.method-warning {
  margin: 1rem 0 0;
  padding-left: 0.75rem;
  border-left: 2px solid #c45c26;
  color: var(--ink-soft);
  font-size: 0.68rem;
  line-height: 1.6;
}

.team-table-card {
  margin-top: 0.7rem;
}

.team-table-wrap {
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
  padding: 0.7rem 0.65rem;
  border-bottom: 1px solid var(--line);
  text-align: left;
  white-space: nowrap;
}

th {
  position: sticky;
  top: 0;
  background: #eef3f0;
  color: var(--ink-soft);
  font-size: 0.62rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.positive {
  color: var(--accent-deep);
  font-weight: 700;
}

.pairs-empty {
  padding: 3rem 1rem;
  color: var(--ink-soft);
  text-align: center;
}

@media (max-width: 1100px) {
  .teams-workspace {
    grid-template-columns: 1fr;
  }

  .team-directory {
    position: static;
    max-height: none;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.4rem;
  }

  .team-directory h2,
  .team-directory .teams-eyebrow {
    grid-column: 1 / -1;
  }
}

@media (max-width: 980px) {
  .teams-layout {
    grid-template-columns: 1fr;
  }

  .team-summary {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 720px) {
  .teams-page {
    width: calc(100% - 1rem);
    padding-top: 0.75rem;
  }

  .teams-hero,
  .team-banner {
    align-items: stretch;
    flex-direction: column;
  }

  .season-control {
    min-width: 0;
  }

  .teams-filters {
    grid-template-columns: 1fr 1fr;
  }

  .team-summary {
    grid-template-columns: 1fr;
  }

  .team-banner-stats {
    justify-content: flex-start;
  }

  .team-banner-stats strong,
  .team-banner-stats span {
    text-align: left;
  }
}
</style>
