<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import VisualizationPage from "./VisualizationPage.vue";
import {
  fetchDataStatus,
  fetchLeagues,
  runAnalysisStep,
  syncLeagueBp,
  syncLeagues,
} from "./api";

const leagues = ref([]);
const leagueId = ref("");
const selectedYear = ref("");
const dataStatus = ref(null);
const loading = ref(false);
const syncing = ref(false);
const syncingCatalog = ref(false);
const syncMode = ref("");
const syncElapsed = ref(0);
const processingStep = ref("");
const processingElapsed = ref(0);
const error = ref("");
const notice = ref("");
let syncTimer = null;
let processingTimer = null;
const routePath = ref(window.location.pathname);

const isManagement = computed(() => routePath.value.startsWith("/management"));

const selectedLeague = computed(() =>
  leagues.value.find((league) => league.league_id === leagueId.value)
);

const years = computed(() =>
  [...new Set(leagues.value.map((league) => league.year).filter(Boolean))].sort(
    (a, b) => b - a
  )
);

const seasonLeagues = computed(() =>
  leagues.value.filter(
    (league) => !selectedYear.value || String(league.year) === selectedYear.value
  )
);

const readyStages = computed(
  () => dataStatus.value?.pipeline?.filter((stage) => stage.ready).length || 0
);

const totalStages = computed(() => dataStatus.value?.pipeline?.length || 0);

const artifacts = computed(() => {
  if (!dataStatus.value?.artifacts) return [];
  const { exports = [], statistics = [], report } = dataStatus.value.artifacts;
  return [...exports, ...statistics, ...(report ? [report] : [])];
});

async function loadLeagues() {
  const rows = await fetchLeagues();
  leagues.value = rows || [];
  if (!leagueId.value && leagues.value.length) {
    const preferred = leagues.value.find(
      (league) => league.league_id === "20260002"
    );
    const initial = preferred || leagues.value[0];
    selectedYear.value = String(initial.year || "");
    leagueId.value = initial.league_id;
  }
}

async function loadStatus() {
  if (!leagueId.value) {
    dataStatus.value = null;
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    dataStatus.value = await fetchDataStatus(leagueId.value);
  } catch (err) {
    dataStatus.value = null;
    error.value = err.message || "Could not load local data status.";
  } finally {
    loading.value = false;
  }
}

async function refreshLeagueCatalog() {
  if (syncingCatalog.value) return;
  syncingCatalog.value = true;
  error.value = "";
  notice.value = "Downloading the latest league catalog from the KPL API…";
  try {
    const result = await syncLeagues();
    await loadLeagues();
    await loadStatus();
    notice.value =
      `League catalog updated · ${result.inserted || 0} added · ` +
      `${result.updated || 0} refreshed`;
  } catch (err) {
    notice.value = "";
    error.value = err.message || "League catalog download failed.";
  } finally {
    syncingCatalog.value = false;
  }
}

async function runDownload({ matchLimit = null, mode = "all" } = {}) {
  if (!leagueId.value || syncing.value) return;
  syncing.value = true;
  syncMode.value = mode;
  syncElapsed.value = 0;
  error.value = "";
  notice.value =
    mode === "all"
      ? "Downloading every finished match and its battle BP data…"
      : "Downloading a five-match sample from the KPL API…";
  syncTimer = window.setInterval(() => {
    syncElapsed.value += 1;
  }, 1000);

  try {
    const result = await syncLeagueBp({
      leagueId: leagueId.value,
      matchLimit,
    });
    const downloadSummary =
      `Download complete · ${result.finished_matches_processed || 0} matches · ` +
      `${result.battles_upserted || 0} battles · ` +
      `${result.bp_rows_written || 0} BP actions · ` +
      `${result.battle_player_rows_written || 0} player mappings · ` +
      `${result.heroes_upserted || 0} heroes`;
    notice.value = result.analysis_error
      ? `${downloadSummary} · analysis failed: ${result.analysis_error}`
      : `${downloadSummary} · season JSONL and report generated`;
    await loadStatus();
  } catch (err) {
    notice.value = "";
    error.value = err.message || "KPL data download failed.";
  } finally {
    if (syncTimer) window.clearInterval(syncTimer);
    syncTimer = null;
    syncing.value = false;
    syncMode.value = "";
  }
}

function pipelineReady(key) {
  return Boolean(
    dataStatus.value?.pipeline?.find((stage) => stage.key === key)?.ready
  );
}

function openReport() {
  if (!leagueId.value || !pipelineReady("report")) return;
  window.open(
    `/api/pipeline/report/${encodeURIComponent(leagueId.value)}`,
    "_blank",
    "noopener,noreferrer"
  );
}

async function runPipeline(step) {
  if (!leagueId.value || processingStep.value || syncing.value) return;
  processingStep.value = step;
  processingElapsed.value = 0;
  error.value = "";
  notice.value =
    step === "all"
      ? "Running the complete season analysis pipeline…"
      : `Running ${step} for the selected season…`;
  processingTimer = window.setInterval(() => {
    processingElapsed.value += 1;
  }, 1000);
  try {
    const result = await runAnalysisStep({
      leagueId: leagueId.value,
      step,
    });
    const duration = (result.steps || []).reduce(
      (sum, item) => sum + Number(item.duration_seconds || 0),
      0
    );
    notice.value =
      `${step === "all" ? "Complete pipeline" : step} finished for ` +
      `${leagueId.value} in ${duration.toFixed(1)}s`;
    await loadStatus();
  } catch (err) {
    notice.value = "";
    error.value = err.message || `${step} failed.`;
  } finally {
    if (processingTimer) window.clearInterval(processingTimer);
    processingTimer = null;
    processingStep.value = "";
  }
}

function number(value) {
  return Number(value || 0).toLocaleString();
}

function bytes(value) {
  const size = Number(value || 0);
  if (!size) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 ** 2).toFixed(1)} MB`;
}

function dateTime(value) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

async function loadManagement() {
  try {
    await loadLeagues();
    await loadStatus();
  } catch (err) {
    error.value = err.message || "Could not connect to the local API.";
  }
}

function handlePopState() {
  routePath.value = window.location.pathname;
  if (isManagement.value && !leagues.value.length) loadManagement();
}

function navigate(path) {
  if (window.location.pathname === path) return;
  window.history.pushState({}, "", path);
  routePath.value = path;
  if (isManagement.value && !leagues.value.length) loadManagement();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

onMounted(() => {
  window.addEventListener("popstate", handlePopState);
  if (isManagement.value) loadManagement();
});

onBeforeUnmount(() => {
  window.removeEventListener("popstate", handlePopState);
});

watch(leagueId, loadStatus);
watch(selectedYear, () => {
  if (
    !seasonLeagues.value.some((league) => league.league_id === leagueId.value)
  ) {
    leagueId.value = seasonLeagues.value[0]?.league_id || "";
  }
});
</script>

<template>
  <nav class="site-navigation">
    <a class="site-brand" href="/" @click.prevent="navigate('/')">
      KPL<span>LAB</span>
    </a>
    <div>
      <a
        href="/"
        :class="{ active: !isManagement }"
        @click.prevent="navigate('/')"
      >
        Draft Atlas
      </a>
      <a
        href="/management"
        :class="{ active: isManagement }"
        @click.prevent="navigate('/management')"
      >
        Management
      </a>
    </div>
  </nav>

  <main v-if="isManagement" class="page">
    <header class="masthead">
      <div>
        <p class="eyebrow">KPL · Local data operations</p>
        <h1>Data Management</h1>
        <p class="lede">
          Download official match data, inspect local coverage, and track every
          JSONL analysis stage from one place.
        </p>
      </div>
      <div class="api-state">
        <span class="pulse" :class="{ active: dataStatus }"></span>
        {{ dataStatus ? "Local API connected" : "Waiting for local API" }}
      </div>
    </header>

    <section class="control-panel" aria-label="Data download controls">
      <div class="selectors">
        <label class="year-picker">
          <span>Year</span>
          <select v-model="selectedYear" :disabled="syncing">
            <option v-if="!years.length" value="">No years available</option>
            <option v-for="year in years" :key="year" :value="String(year)">
              {{ year }}
            </option>
          </select>
        </label>
        <label class="league-picker">
          <span>Season / tournament</span>
          <select v-model="leagueId" :disabled="syncing">
            <option v-if="!seasonLeagues.length" value="">
              No seasons available
            </option>
            <option
              v-for="league in seasonLeagues"
              :key="league.league_id"
              :value="league.league_id"
            >
              S{{ league.season ?? "—" }} ·
              {{ league.league_name || league.league_id }}
            </option>
          </select>
        </label>
      </div>

      <div class="control-actions">
        <button
          class="button ghost"
          type="button"
          :disabled="syncingCatalog || syncing"
          @click="refreshLeagueCatalog"
        >
          {{ syncingCatalog ? "Refreshing…" : "Refresh league catalog" }}
        </button>
        <button
          class="button ghost"
          type="button"
          :disabled="loading || syncing || !leagueId"
          @click="loadStatus"
        >
          {{ loading ? "Checking…" : "Refresh status" }}
        </button>
        <button
          class="button sample"
          type="button"
          :disabled="syncing || !leagueId"
          @click="runDownload({ matchLimit: 5, mode: 'sample' })"
        >
          {{
            syncing && syncMode === "sample"
              ? `Downloading… ${syncElapsed}s`
              : "Download 5-match sample"
          }}
        </button>
        <button
          class="button primary"
          type="button"
          :disabled="syncing || !leagueId"
          @click="runDownload({ matchLimit: null, mode: 'all' })"
        >
          {{
            syncing && syncMode === "all"
              ? `Downloading… ${syncElapsed}s`
              : "Download full league"
          }}
        </button>
      </div>
    </section>

    <p v-if="error" class="banner error">{{ error }}</p>
    <p v-else-if="notice" class="banner notice">{{ notice }}</p>

    <template v-if="dataStatus">
      <section class="section-heading">
        <div>
          <p class="kicker">Downloaded into SQLite</p>
          <h2>{{ selectedLeague?.league_name || leagueId }}</h2>
        </div>
        <div class="league-code">{{ leagueId }}</div>
      </section>

      <section class="metric-grid">
        <article class="metric-card">
          <span>Matches</span>
          <strong>{{ number(dataStatus.counts.matches) }}</strong>
          <small>
            {{ number(dataStatus.counts.finished_matches) }} finished
          </small>
        </article>
        <article class="metric-card">
          <span>Battles</span>
          <strong>{{ number(dataStatus.counts.battles) }}</strong>
          <small>{{ dateTime(dataStatus.freshness.battles) }}</small>
        </article>
        <article class="metric-card accent">
          <span>BP actions</span>
          <strong>{{ number(dataStatus.counts.bp_actions) }}</strong>
          <small>raw bans and picks</small>
        </article>
        <article class="metric-card">
          <span>Player mappings</span>
          <strong>{{ number(dataStatus.counts.battle_players) }}</strong>
          <small>
            {{ number(dataStatus.counts.teams) }} teams ·
            {{ number(dataStatus.counts.players) }} players
          </small>
        </article>
        <article class="metric-card">
          <span>Heroes used</span>
          <strong>{{ number(dataStatus.counts.heroes_used) }}</strong>
          <small>
            {{ number(dataStatus.counts.heroes_missing) }} missing from the
            {{ number(dataStatus.counts.heroes) }}-hero reference
          </small>
        </article>
      </section>

      <section class="dashboard-grid">
        <article class="panel pipeline-panel">
          <div class="panel-title">
            <div>
              <p class="kicker">Processing coverage</p>
              <h2>Download → JSONL → report</h2>
            </div>
            <strong class="stage-count">{{ readyStages }}/{{ totalStages }}</strong>
          </div>

          <div class="progress-track" aria-hidden="true">
            <span
              :style="{
                width: totalStages
                  ? `${(readyStages / totalStages) * 100}%`
                  : '0%',
              }"
            ></span>
          </div>

          <div class="pipeline-actions">
            <button
              class="button primary"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('download')"
              @click="runPipeline('all')"
            >
              {{
                processingStep === "all"
                  ? `Running all… ${processingElapsed}s`
                  : "Run all analysis"
              }}
            </button>
            <button
              class="button ghost"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('download')"
              @click="runPipeline('export')"
            >
              {{ processingStep === "export" ? `Exporting… ${processingElapsed}s` : "1 · Export JSONL" }}
            </button>
            <button
              class="button ghost"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('matches_jsonl')"
              @click="runPipeline('decisions')"
            >
              {{ processingStep === "decisions" ? `Building… ${processingElapsed}s` : "2 · Build decisions" }}
            </button>
            <button
              class="button ghost"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('decisions_jsonl')"
              @click="runPipeline('statistics')"
            >
              {{ processingStep === "statistics" ? `Computing… ${processingElapsed}s` : "3 · Compute statistics" }}
            </button>
            <button
              class="button ghost"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('statistics')"
              @click="runPipeline('report')"
            >
              {{ processingStep === "report" ? `Rendering… ${processingElapsed}s` : "4 · Build report" }}
            </button>
            <button
              class="button report-button"
              type="button"
              :disabled="!pipelineReady('report')"
              @click="openReport"
            >
              Open HTML report ↗
            </button>
          </div>

          <ol class="pipeline">
            <li
              v-for="(stage, index) in dataStatus.pipeline"
              :key="stage.key"
              :class="{ ready: stage.ready }"
            >
              <span class="stage-index">
                {{ stage.ready ? "✓" : index + 1 }}
              </span>
              <div>
                <strong>{{ stage.label }}</strong>
                <small>{{ stage.detail }}</small>
              </div>
              <span class="status-chip">
                {{ stage.ready ? "Ready" : "Pending" }}
              </span>
            </li>
          </ol>

          <p class="quality-note">{{ dataStatus.processing_note }}</p>
        </article>

        <article class="panel freshness-panel">
          <div class="panel-title">
            <div>
              <p class="kicker">Local freshness</p>
              <h2>Last database updates</h2>
            </div>
          </div>
          <dl class="freshness-list">
            <div>
              <dt>Matches</dt>
              <dd>{{ dateTime(dataStatus.freshness.matches) }}</dd>
            </div>
            <div>
              <dt>Battles</dt>
              <dd>{{ dateTime(dataStatus.freshness.battles) }}</dd>
            </div>
            <div>
              <dt>Hero aggregates</dt>
              <dd>{{ dateTime(dataStatus.freshness.hero_stats) }}</dd>
            </div>
          </dl>
          <p class="terminal-note">
            JSONL processing remains an explicit local pipeline. The dashboard
            reports generated files without treating flagged source rows as
            deleted or corrected.
          </p>
        </article>
      </section>

      <section class="panel artifacts-panel">
        <div class="panel-title">
          <div>
            <p class="kicker">Processed locally</p>
            <h2>JSONL and report artifacts</h2>
          </div>
          <span>{{ artifacts.filter((item) => item.ready).length }} files ready</span>
        </div>

        <div class="artifact-table">
          <div class="artifact-row artifact-head">
            <span>Artifact</span>
            <span>Records</span>
            <span>Size</span>
            <span>Updated</span>
            <span>Status</span>
          </div>
          <div
            v-for="item in artifacts"
            :key="item.key"
            class="artifact-row"
          >
            <div>
              <strong>{{ item.label }}</strong>
              <small>{{ item.path }}</small>
            </div>
            <span>{{ item.records ? number(item.records) : "—" }}</span>
            <span>{{ bytes(item.bytes) }}</span>
            <span>{{ dateTime(item.updated_at) }}</span>
            <span>
              <span class="file-state" :class="{ ready: item.ready }">
                {{ item.ready ? "Ready" : item.exists ? "Stale" : "Missing" }}
              </span>
            </span>
          </div>
        </div>
      </section>
    </template>

    <section v-else-if="loading" class="empty-state">
      Reading the local database and analysis files…
    </section>
    <section v-else-if="!error" class="empty-state">
      Refresh the KPL league catalog to begin downloading data.
    </section>
  </main>
  <VisualizationPage v-else />
</template>

<style scoped>
.site-navigation {
  display: flex;
  width: min(1440px, calc(100% - 2rem));
  min-height: 62px;
  margin: 0 auto;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line);
}

.site-navigation a {
  color: var(--ink-soft);
  text-decoration: none;
}

.site-brand {
  font: 800 1rem var(--display);
  letter-spacing: -0.02em;
}

.site-brand span {
  margin-left: 0.18rem;
  color: var(--accent);
}

.site-navigation > div {
  display: flex;
  align-self: stretch;
  gap: 1.4rem;
}

.site-navigation > div a {
  display: flex;
  align-items: center;
  border-bottom: 2px solid transparent;
  font-size: 0.73rem;
}

.site-navigation > div a.active {
  border-color: var(--accent);
  color: var(--ink);
}

.page {
  width: min(1240px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 2.5rem 0 5rem;
}

.masthead {
  display: flex;
  justify-content: space-between;
  gap: 2rem;
  align-items: flex-start;
  margin-bottom: 1.75rem;
}

.eyebrow,
.kicker {
  margin: 0 0 0.45rem;
  color: var(--accent-deep);
  font-size: 0.72rem;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}

h1,
h2 {
  margin: 0;
  font-family: var(--display);
  letter-spacing: -0.035em;
}

h1 {
  font-size: clamp(2.7rem, 7vw, 4.6rem);
  line-height: 0.95;
}

h2 {
  font-size: clamp(1.25rem, 3vw, 1.75rem);
}

.lede {
  max-width: 48rem;
  margin: 0.9rem 0 0;
  color: var(--ink-soft);
}

.api-state {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-top: 0.35rem;
  color: var(--ink-soft);
  white-space: nowrap;
  font-size: 0.78rem;
}

.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #b8c0bc;
  box-shadow: 0 0 0 4px rgba(184, 192, 188, 0.18);
}

.pulse.active {
  background: var(--accent);
  box-shadow: 0 0 0 4px rgba(15, 138, 107, 0.14);
}

.control-panel {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.76);
  box-shadow: 0 16px 50px rgba(16, 42, 46, 0.06);
}

.selectors {
  display: flex;
  flex: 1;
  gap: 0.5rem;
  max-width: 500px;
}

.league-picker,
.year-picker {
  display: grid;
  gap: 0.35rem;
}

.league-picker {
  flex: 1;
}

.year-picker {
  width: 105px;
}

.league-picker span,
.year-picker span {
  color: var(--ink-soft);
  font-size: 0.7rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
}

select,
.button {
  min-height: 42px;
  border: 1px solid var(--line);
  font: inherit;
}

select {
  width: 100%;
  padding: 0.6rem 0.75rem;
  background: #fff;
  color: var(--ink);
}

.control-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.5rem;
}

.button {
  padding: 0.6rem 0.8rem;
  background: #fff;
  color: var(--ink);
}

.button.primary {
  border-color: var(--accent-deep);
  background: var(--accent);
  color: #fff;
  font-weight: 500;
}

.button.sample {
  border-color: rgba(15, 138, 107, 0.35);
  color: var(--accent-deep);
}

.button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.banner {
  margin: 0.8rem 0 0;
  padding: 0.75rem 0.9rem;
  border: 1px solid;
}

.banner.notice {
  border-color: rgba(15, 138, 107, 0.28);
  background: rgba(15, 138, 107, 0.08);
  color: var(--accent-deep);
}

.banner.error {
  border-color: rgba(196, 92, 38, 0.32);
  background: rgba(196, 92, 38, 0.08);
  color: var(--warn);
}

.section-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  margin: 2.4rem 0 0.85rem;
}

.league-code {
  color: var(--ink-soft);
  font-size: 0.78rem;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.75rem;
}

.metric-card {
  min-width: 0;
  padding: 1rem;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.72);
}

.metric-card.accent {
  border-color: rgba(15, 138, 107, 0.3);
  background: rgba(15, 138, 107, 0.08);
}

.metric-card span,
.metric-card small {
  display: block;
  color: var(--ink-soft);
}

.metric-card span {
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.metric-card strong {
  display: block;
  margin: 0.45rem 0 0.2rem;
  font-family: var(--display);
  font-size: clamp(1.7rem, 4vw, 2.4rem);
  line-height: 1;
}

.metric-card small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.72rem;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(280px, 0.75fr);
  gap: 0.75rem;
  margin-top: 0.75rem;
}

.panel {
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.78);
}

.pipeline-panel,
.freshness-panel,
.artifacts-panel {
  padding: 1.15rem;
}

.panel-title {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
}

.stage-count {
  color: var(--accent-deep);
  font-family: var(--display);
  font-size: 1.35rem;
}

.progress-track {
  height: 6px;
  margin: 1rem 0 0.65rem;
  overflow: hidden;
  background: rgba(16, 42, 46, 0.08);
}

.progress-track span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #25b58f);
  transition: width 240ms ease;
}

.pipeline-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  padding: 0.25rem 0 0.8rem;
  border-bottom: 1px solid var(--line);
}

.pipeline-actions .button {
  min-height: 36px;
  padding: 0.45rem 0.65rem;
  font-size: 0.72rem;
}

.pipeline-actions .report-button {
  margin-left: auto;
  border-color: var(--accent-deep);
  color: var(--accent-deep);
  font-weight: 500;
}

.pipeline {
  margin: 0;
  padding: 0;
  list-style: none;
}

.pipeline li {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 0.75rem;
  align-items: center;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--line);
}

.stage-index {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 50%;
  color: var(--ink-soft);
  font-size: 0.72rem;
}

.pipeline li.ready .stage-index {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.pipeline strong,
.pipeline small {
  display: block;
}

.pipeline small {
  margin-top: 0.12rem;
  color: var(--ink-soft);
  font-size: 0.72rem;
}

.status-chip,
.file-state {
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--line);
  color: var(--ink-soft);
  font-size: 0.66rem;
  text-transform: uppercase;
}

.pipeline li.ready .status-chip,
.file-state.ready {
  border-color: rgba(15, 138, 107, 0.25);
  background: rgba(15, 138, 107, 0.08);
  color: var(--accent-deep);
}

.quality-note,
.terminal-note {
  margin: 1rem 0 0;
  color: var(--ink-soft);
  font-size: 0.75rem;
  line-height: 1.6;
}

.quality-note {
  padding-left: 0.75rem;
  border-left: 2px solid var(--accent);
}

.freshness-list {
  margin: 1rem 0 0;
}

.freshness-list div {
  padding: 0.8rem 0;
  border-bottom: 1px solid var(--line);
}

.freshness-list dt {
  color: var(--ink-soft);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.freshness-list dd {
  margin: 0.25rem 0 0;
  font-weight: 500;
}

.artifacts-panel {
  margin-top: 0.75rem;
}

.artifact-table {
  margin-top: 0.9rem;
  overflow-x: auto;
}

.artifact-row {
  display: grid;
  grid-template-columns: minmax(250px, 2fr) 0.6fr 0.6fr minmax(150px, 1fr) 0.55fr;
  gap: 0.75rem;
  align-items: center;
  min-width: 820px;
  padding: 0.72rem 0;
  border-bottom: 1px solid var(--line);
}

.artifact-head {
  padding-top: 0;
  color: var(--ink-soft);
  font-size: 0.67rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.artifact-row strong,
.artifact-row small {
  display: block;
}

.artifact-row small {
  margin-top: 0.16rem;
  overflow: hidden;
  color: var(--ink-soft);
  font-size: 0.69rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  margin-top: 1rem;
  padding: 3rem 1rem;
  border: 1px dashed var(--line);
  color: var(--ink-soft);
  text-align: center;
}

@media (max-width: 980px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .page {
    width: min(100% - 1rem, 1240px);
    padding-top: 1.25rem;
  }

  .masthead,
  .control-panel {
    align-items: stretch;
    flex-direction: column;
  }

  .api-state {
    padding: 0;
  }

  .selectors,
  .league-picker {
    max-width: none;
  }

  .selectors {
    width: 100%;
  }

  .year-picker {
    width: 110px;
  }

  .control-actions,
  .button {
    width: 100%;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>
