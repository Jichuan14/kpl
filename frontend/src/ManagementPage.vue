<script setup>
import { onBeforeUnmount, onMounted } from "vue";
import { useManagement } from "./composables/useManagement";
import { language } from "./i18n";
const management = useManagement();
const { leagueId, selectedYear, dataStatus, coachUsage, visitorAnalytics, coachLimits, loading, syncing, syncingCatalog, savingCoachLimits, processingStep, processingElapsed, syncMode, syncElapsed, error, notice, apiConnected, years, seasonLeagues, selectedLeague, analysisPipeline, readyStages, totalStages, frontendAssets, frontendAssetsReady, artifacts, loadStatus, loadCoachUsage, loadVisitorAnalytics, refreshLeagueCatalog, runDownload, runPipeline, runFullUpdate, publishAssets, saveCoachLimits, pipelineReady, number, bytes, dateTime, initialize, startMonitoring, stopMonitoring } = management;
onMounted(async () => { await initialize(); startMonitoring(); });
onBeforeUnmount(stopMonitoring);
</script>
<template>
  <main class="page">
    <header class="masthead">
      <div>
        <p class="eyebrow">{{ $t("Private · Local data operations") }}</p>
        <h1>{{ $t("Data Management") }}</h1>
        <p class="lede">{{ $t("Download official match data, inspect local coverage, and track every JSONL analysis stage from one place.") }}</p>
      </div>
      <div class="api-state">
        <span class="pulse" :class="{ active: apiConnected }"></span>
        {{ apiConnected ? "Local API connected" : "Waiting for local API" }}
      </div>
    </header>

    <section class="control-panel" :aria-label="$t('Data download controls')">
      <div class="selectors">
        <label class="year-picker">
          <span>{{ $t("Year") }}</span>
          <select v-model="selectedYear" :disabled="syncing">
            <option v-if="!years.length" value="">{{ $t("No years available") }}</option>
            <option v-for="year in years" :key="year" :value="String(year)">
              {{ year }}
            </option>
          </select>
        </label>
        <label class="league-picker">
          <span>{{ $t("Season / tournament") }}</span>
          <select v-model="leagueId" :disabled="syncing">
            <option v-if="!seasonLeagues.length" value="">{{ $t("No seasons available") }}</option>
            <option
              v-for="league in seasonLeagues"
              :key="league.league_id"
              :value="league.league_id"
            >{{ $t("S") }}{{ league.season ?? "—" }} ·
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
        <button
          class="button primary"
          type="button"
          :disabled="syncing || Boolean(processingStep) || !leagueId"
          @click="runFullUpdate"
        >
          {{
            processingStep === "full_update"
              ? `Updating everything… ${processingElapsed}s`
              : "Full update: data, models & site"
          }}
        </button>
      </div>
    </section>

    <p v-if="error" class="banner error">{{ error }}</p>
    <p v-else-if="notice" class="banner notice">{{ notice }}</p>

    <section v-if="coachUsage" class="panel coach-monitor-panel">
        <div class="panel-title">
          <div>
            <p class="kicker">{{ $t("AI Coach monitoring") }}</p>
            <h2>{{ $t("Usage & rate limits") }}</h2>
          </div>
          <button class="button ghost compact" type="button" @click="loadCoachUsage">{{ $t("Refresh") }}</button>
        </div>
        <p class="panel-intro">{{ $t("Live process-level counters. No visitor IP addresses or questions are stored here.") }}</p>
        <div class="monitor-grid">
          <div><span>{{ $t("Server requests · last minute") }}</span><strong>{{ number(coachUsage.server.last_minute) }} / {{ number(coachUsage.server.per_minute_limit) }}</strong></div>
          <div><span>{{ $t("Server requests · last 24 hours") }}</span><strong>{{ number(coachUsage.server.last_24_hours) }} / {{ number(coachUsage.server.per_24_hours_limit) }}</strong></div>
          <div><span>{{ $t("Active AI requests") }}</span><strong>{{ number(coachUsage.active_requests) }} / {{ number(coachUsage.server.max_active_requests) }}</strong></div>
          <div><span>{{ $t("Blocked requests since start") }}</span><strong>{{ number(coachUsage.blocked_since_start) }}</strong></div>
        </div>
        <p class="monitor-note">
          {{ language === "zh-CN"
            ? `单个 IP：每分钟 ${number(coachUsage.per_ip.per_minute_limit)} 次 · 24 小时 ${number(coachUsage.per_ip.per_24_hours_limit)} 次 · 同时 ${number(coachUsage.per_ip.max_active_requests)} 个请求。服务器进程重启后，计数器会重置。`
            : `Per IP: ${number(coachUsage.per_ip.per_minute_limit)}/minute · ${number(coachUsage.per_ip.per_24_hours_limit)}/24 hours · ${number(coachUsage.per_ip.max_active_requests)} active request. Counters reset when this server process restarts.` }}
        </p>
        <form class="coach-limit-form" @submit.prevent="saveCoachLimits">
          <label><span>{{ $t("Per-IP requests / minute") }}</span><input v-model.number="coachLimits.ip_requests_per_minute" type="number" min="1" required></label>
          <label><span>{{ $t("Per-IP requests / 24 hours") }}</span><input v-model.number="coachLimits.ip_requests_per_day" type="number" min="1" required></label>
          <label><span>{{ $t("Server requests / minute") }}</span><input v-model.number="coachLimits.server_requests_per_minute" type="number" min="1" required></label>
          <label><span>{{ $t("Server requests / 24 hours") }}</span><input v-model.number="coachLimits.server_requests_per_day" type="number" min="1" required></label>
          <label><span>{{ $t("Per-IP active requests") }}</span><input v-model.number="coachLimits.ip_max_active_requests" type="number" min="1" required></label>
          <label><span>{{ $t("Server active requests") }}</span><input v-model.number="coachLimits.server_max_active_requests" type="number" min="1" required></label>
          <div class="coach-limit-submit"><small>{{ $t("Changes apply immediately and reset after a server restart.") }}</small><button class="button primary" type="submit" :disabled="savingCoachLimits">{{ savingCoachLimits ? "Saving…" : "Save AI Coach limits" }}</button></div>
        </form>
    </section>

    <section v-if="visitorAnalytics" class="panel visitor-analytics-panel">
      <div class="panel-title">
        <div>
          <p class="kicker">{{ $t("Site analytics") }}</p>
          <h2>{{ $t("Visitor activity") }}</h2>
        </div>
        <button class="button ghost compact" type="button" @click="loadVisitorAnalytics">{{ $t("Refresh") }}</button>
      </div>
      <p class="panel-intro">{{ $t("Unique visitors use an anonymous browser ID hashed before storage. The analytics database keeps no IP addresses or raw IDs.") }}</p>
      <div class="monitor-grid">
        <div><span>{{ $t("Visitors · today") }}</span><strong>{{ number(visitorAnalytics.today.unique_visitors) }}</strong><small>{{ number(visitorAnalytics.today.page_views) }}{{ $t("page views") }}</small></div>
        <div><span>{{ $t("Visitors · last 7 days") }}</span><strong>{{ number(visitorAnalytics.last_7_days.unique_visitors) }}</strong><small>{{ number(visitorAnalytics.last_7_days.page_views) }}{{ $t("page views") }}</small></div>
        <div><span>{{ $t("Visitors · last 30 days") }}</span><strong>{{ number(visitorAnalytics.last_30_days.unique_visitors) }}</strong><small>{{ number(visitorAnalytics.last_30_days.page_views) }}{{ $t("page views") }}</small></div>
        <div><span>{{ $t("Visitors · all time") }}</span><strong>{{ number(visitorAnalytics.all_time.unique_visitors) }}</strong><small>{{ number(visitorAnalytics.all_time.page_views) }}{{ $t("page views") }}</small></div>
      </div>
    </section>

    <template v-if="dataStatus">

      <section class="section-heading">
        <div>
          <p class="kicker">{{ $t("Downloaded into SQLite") }}</p>
          <h2>{{ selectedLeague?.league_name || leagueId }}</h2>
        </div>
        <div class="league-code">{{ leagueId }}</div>
      </section>

      <section class="metric-grid">
        <article class="metric-card">
          <span>{{ $t("Matches") }}</span>
          <strong>{{ number(dataStatus.counts.matches) }}</strong>
          <small>
            {{ number(dataStatus.counts.finished_matches) }}{{ $t("finished") }}</small>
        </article>
        <article class="metric-card">
          <span>{{ $t("Battles") }}</span>
          <strong>{{ number(dataStatus.counts.battles) }}</strong>
          <small>{{ dateTime(dataStatus.freshness.battles) }}</small>
        </article>
        <article class="metric-card accent">
          <span>{{ $t("BP actions") }}</span>
          <strong>{{ number(dataStatus.counts.bp_actions) }}</strong>
          <small>{{ $t("raw bans and picks") }}</small>
        </article>
        <article class="metric-card">
          <span>{{ $t("Player mappings") }}</span>
          <strong>{{ number(dataStatus.counts.battle_players) }}</strong>
          <small>
            {{ number(dataStatus.counts.teams) }}{{ $t("teams ·") }}{{ number(dataStatus.counts.players) }}{{ $t("players") }}</small>
        </article>
        <article class="metric-card">
          <span>{{ $t("Heroes used") }}</span>
          <strong>{{ number(dataStatus.counts.heroes_used) }}</strong>
          <small>
            {{ number(dataStatus.counts.heroes_missing) }}{{ $t("missing from the") }}{{ number(dataStatus.counts.heroes) }}{{ $t("-hero reference") }}</small>
        </article>
      </section>

      <section class="dashboard-grid">
        <article class="panel pipeline-panel">
          <div class="panel-title">
            <div>
              <p class="kicker">{{ $t("Step 2 · local analysis") }}</p>
              <h2>{{ $t("Entire analysis pipeline") }}</h2>
            </div>
            <strong class="stage-count">{{ readyStages }}/{{ totalStages }}</strong>
          </div>

          <p class="panel-intro">{{ $t("This checks the JSONL and analysis files already on this machine. Run it only when the source download changed or a stage is missing.") }}</p>

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
              class="button"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('download')"
              @click="runPipeline('display')"
            >
              {{
                processingStep === "display"
                  ? `Building display data… ${processingElapsed}s`
                  : "Build display data (no model training)"
              }}
            </button>
            <button
              class="button primary"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !pipelineReady('download')"
              @click="runPipeline('all')"
            >
              {{
                processingStep === "all"
                  ? `Analyzing… ${processingElapsed}s`
                  : "Run / rebuild entire analysis"
              }}
            </button>
          </div>

          <ol class="pipeline">
            <li
              v-for="(stage, index) in analysisPipeline"
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

        <article class="panel publish-panel">
          <div class="panel-title">
            <div>
              <p class="kicker">{{ $t("Step 3 · public files") }}</p>
              <h2>{{ $t("Populate frontend assets") }}</h2>
            </div>
            <strong class="stage-count">{{ frontendAssetsReady }}/{{ frontendAssets.length }}</strong>
          </div>
          <p class="panel-intro">{{ $t("Copies the selected season’s completed local analysis into the browser folder. This does not download KPL data or rerun analysis.") }}</p>
          <div class="pipeline-actions">
            <button
              class="button primary"
              type="button"
              :disabled="Boolean(processingStep) || syncing || !dataStatus.display_ready"
              @click="publishAssets"
            >
              {{ processingStep === "publish" ? `Publishing… ${processingElapsed}s` : "Populate frontend assets" }}
            </button>
          </div>
          <ul class="public-assets">
            <li v-for="item in frontendAssets" :key="item.key">
              <span class="file-state" :class="{ ready: item.ready }">
                {{ item.ready ? "Ready" : item.exists ? "Stale" : "Missing" }}
              </span>
              <div>
                <strong>{{ item.label }}</strong>
                <small>{{ item.path }} · {{ dateTime(item.updated_at) }}</small>
              </div>
            </li>
          </ul>
          <p v-if="!dataStatus.display_ready" class="terminal-note">{{ $t("Build the display data first; this prevents publishing a partial frontend dataset. Draft-model training is optional for season history.") }}</p>
        </article>
      </section>

      <section class="panel freshness-panel">
        <div class="panel-title">
          <div>
            <p class="kicker">{{ $t("Local freshness") }}</p>
            <h2>{{ $t("Last database updates") }}</h2>
          </div>
        </div>
        <dl class="freshness-list">
          <div><dt>{{ $t("Matches") }}</dt><dd>{{ dateTime(dataStatus.freshness.matches) }}</dd></div>
          <div><dt>{{ $t("Battles") }}</dt><dd>{{ dateTime(dataStatus.freshness.battles) }}</dd></div>
          <div><dt>{{ $t("Hero aggregates") }}</dt><dd>{{ dateTime(dataStatus.freshness.hero_stats) }}</dd></div>
        </dl>
      </section>

      <section class="panel artifacts-panel">
        <div class="panel-title">
          <div>
            <p class="kicker">{{ $t("Processed locally") }}</p>
            <h2>{{ $t("JSONL analysis artifacts") }}</h2>
          </div>
          <span>{{ artifacts.filter((item) => item.ready).length }}{{ $t("files ready") }}</span>
        </div>

        <div class="artifact-table">
          <div class="artifact-row artifact-head">
            <span>{{ $t("Artifact") }}</span>
            <span>{{ $t("Records") }}</span>
            <span>{{ $t("Size") }}</span>
            <span>{{ $t("Updated") }}</span>
            <span>{{ $t("Status") }}</span>
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

    <section v-else-if="loading" class="empty-state">{{ $t("Reading the local database and analysis files…") }}</section>
    <section v-else-if="!error" class="empty-state">{{ $t("Refresh the KPL league catalog to begin downloading data.") }}</section>
  </main>

</template>
