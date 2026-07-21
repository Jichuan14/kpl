<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { fetchHeroBp, fetchLeagues, syncLeagueBp } from "./api";

const leagues = ref([]);
const leagueId = ref("");
const sort = ref("presence");
const heroes = ref([]);
const loading = ref(false);
const syncing = ref(false);
const syncMode = ref(""); // "sample" | "all"
const syncElapsed = ref(0);
const error = ref("");
const notice = ref("");
const meta = ref({ league_id: "", battleHint: "" });
const leaguesWithStats = ref(new Set());
let syncTimer = null;

const sortOptions = [
  { value: "presence", label: "Presence" },
  { value: "ban", label: "Ban rate" },
  { value: "pick", label: "Pick rate" },
  { value: "win", label: "Win rate" },
];

const selectedLeagueName = computed(() => {
  const hit = leagues.value.find((l) => l.league_id === leagueId.value);
  return hit?.league_name || meta.value.league_id || "—";
});

async function loadLeagues() {
  const rows = await fetchLeagues();
  leagues.value = rows || [];
  if (!leagueId.value && leagues.value.length) {
    leagueId.value = leagues.value[0].league_id;
  }
}

async function loadHeroes() {
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchHeroBp({
      leagueId: leagueId.value || undefined,
      sort: sort.value,
      limit: 40,
    });
    heroes.value = data?.heroes || [];
    meta.value.league_id = data?.league_id || leagueId.value;
    if (!leagueId.value && data?.league_id) {
      leagueId.value = data.league_id;
    }
    if (heroes.value.length && meta.value.league_id) {
      const next = new Set(leaguesWithStats.value);
      next.add(meta.value.league_id);
      leaguesWithStats.value = next;
    }
  } catch (e) {
    error.value = e.message || "Failed to load hero BP stats";
    heroes.value = [];
  } finally {
    loading.value = false;
  }
}

async function runSync({ matchLimit = null, mode = "all" } = {}) {
  if (!leagueId.value) {
    error.value = "Pick a league first, then sync.";
    return;
  }
  if (syncing.value) return;

  syncing.value = true;
  syncMode.value = mode;
  syncElapsed.value = 0;
  error.value = "";
  notice.value =
    mode === "all"
      ? `Downloading entire season BP for ${selectedLeagueName.value}… this can take several minutes. Keep the tab open.`
      : `Downloading sample BP for ${selectedLeagueName.value}… usually 5–20s.`;
  syncTimer = window.setInterval(() => {
    syncElapsed.value += 1;
  }, 1000);

  try {
    const result = await syncLeagueBp({
      leagueId: leagueId.value,
      matchLimit,
    });
    meta.value.battleHint = `${result.battles_upserted} battles · ${result.bp_rows_written} BP rows`;
    if ((result.bp_rows_written || 0) === 0) {
      notice.value =
        `Sync finished for ${result.league_id}, but no BP rows came back ` +
        `(${result.finished_matches_processed || 0} finished matches processed).`;
    } else {
      notice.value =
        `Synced ${result.finished_matches_processed} matches → ` +
        `${result.battles_upserted} battles → ${result.bp_rows_written} BP rows ` +
        `(${result.hero_stats?.heroes ?? 0} heroes).`;
    }
    const next = new Set(leaguesWithStats.value);
    next.add(result.league_id);
    leaguesWithStats.value = next;
    await loadLeagues();
    await loadHeroes();
  } catch (e) {
    notice.value = "";
    error.value = e.message || "Sync failed — is the backend running on :8000?";
  } finally {
    if (syncTimer) {
      clearInterval(syncTimer);
      syncTimer = null;
    }
    syncing.value = false;
    syncMode.value = "";
  }
}

function runSampleSync() {
  return runSync({ matchLimit: 5, mode: "sample" });
}

function runFullSync() {
  return runSync({ matchLimit: null, mode: "all" });
}

function pct(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`;
}

onMounted(async () => {
  try {
    await loadLeagues();
  } catch {
    // empty DB is fine; user can sync
  }
  await loadHeroes();
});

watch([leagueId, sort], () => {
  loadHeroes();
});
</script>

<template>
  <div class="page">
    <header class="hero">
      <p class="eyebrow">KPL · Ban / Pick</p>
      <h1>KPL BP</h1>
      <p class="lede">
        Hero presence, bans, picks, and win rates from your local SQL store.
      </p>
    </header>

    <section class="toolbar" aria-label="Filters">
      <label>
        <span>League</span>
        <select v-model="leagueId">
          <option v-if="!leagues.length" value="">No leagues yet</option>
          <option
            v-for="league in leagues"
            :key="league.league_id"
            :value="league.league_id"
          >
            {{ league.league_name || league.league_id }}
            {{ leaguesWithStats.has(league.league_id) ? "· synced" : "" }}
          </option>
        </select>
      </label>

      <label>
        <span>Sort</span>
        <select v-model="sort">
          <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </label>

      <div class="actions">
        <button
          class="secondary"
          type="button"
          :disabled="syncing || !leagueId"
          @click="runSampleSync"
        >
          {{
            syncing && syncMode === "sample"
              ? `Syncing sample… ${syncElapsed}s`
              : "Sync sample (5 matches)"
          }}
        </button>
        <button
          class="primary"
          type="button"
          :disabled="syncing || !leagueId"
          @click="runFullSync"
        >
          {{
            syncing && syncMode === "all"
              ? `Syncing all… ${syncElapsed}s`
              : "Sync all (full season)"
          }}
        </button>
      </div>
    </section>

    <p v-if="syncing" class="banner busy">
      {{ notice || "Syncing…" }} Keep this tab open — the button waits for the API.
    </p>
    <p v-else-if="error" class="banner error">{{ error }}</p>
    <p v-else-if="notice" class="banner ok">{{ notice }}</p>

    <p v-if="loading && !syncing" class="status">Loading…</p>
    <p v-else class="status">
      {{ selectedLeagueName }}
      <template v-if="meta.battleHint"> · {{ meta.battleHint }}</template>
      <template v-else-if="heroes.length"> · {{ heroes.length }} heroes</template>
      <template v-else> · no local BP data yet</template>
    </p>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Hero</th>
            <th>Presence</th>
            <th>Ban</th>
            <th>Pick</th>
            <th>Win</th>
            <th>B/P</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !heroes.length">
            <td colspan="7" class="empty">
              No BP data for this league yet. League names come from the full
              KPL list, but ban/pick rows are only downloaded when you sync.
              Select a league and hit “Sync this league”.
            </td>
          </tr>
          <tr v-for="(hero, index) in heroes" :key="hero.hero_id">
            <td class="rank">{{ index + 1 }}</td>
            <td class="hero-cell">
              <img
                v-if="hero.hero_icon"
                :src="hero.hero_icon"
                :alt="hero.hero_name"
                width="36"
                height="36"
              />
              <div>
                <strong>{{ hero.hero_name }}</strong>
                <small>{{ hero.hero_id }}</small>
              </div>
            </td>
            <td>
              <div class="meter">
                <span class="bar" :style="{ width: pct(hero.presence_rate) }" />
              </div>
              {{ pct(hero.presence_rate) }}
            </td>
            <td>{{ pct(hero.ban_rate) }}</td>
            <td>{{ pct(hero.pick_rate) }}</td>
            <td :class="{ hot: hero.win_rate >= 0.55 }">{{ pct(hero.win_rate) }}</td>
            <td>{{ hero.ban_count }}/{{ hero.pick_count }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.page {
  width: min(1080px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 2.5rem 0 4rem;
}

.hero {
  margin-bottom: 1.75rem;
}

.eyebrow {
  margin: 0 0 0.4rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-size: 0.72rem;
  color: var(--accent-deep);
}

h1 {
  margin: 0;
  font-family: var(--display);
  font-size: clamp(2.8rem, 8vw, 4.4rem);
  font-weight: 800;
  letter-spacing: -0.04em;
  line-height: 0.95;
}

.lede {
  margin: 0.85rem 0 0;
  max-width: 36rem;
  color: var(--ink-soft);
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
  padding: 1rem;
  border: 1px solid var(--line);
  background: var(--panel);
  backdrop-filter: blur(8px);
}

label {
  display: grid;
  gap: 0.35rem;
  min-width: 180px;
  flex: 1;
}

label span {
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-soft);
}

select,
.primary,
.secondary {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  padding: 0.65rem 0.75rem;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.primary,
.secondary {
  white-space: nowrap;
}

.primary {
  background: var(--accent);
  border-color: var(--accent-deep);
  color: #f5fffb;
  font-weight: 500;
}

.secondary {
  background: #fff;
}

.primary:disabled,
.secondary:disabled {
  opacity: 0.6;
  cursor: wait;
}

.status {
  margin: 0.9rem 0 0.75rem;
  color: var(--ink-soft);
  font-size: 0.85rem;
}

.banner {
  margin: 0.9rem 0 0;
  padding: 0.75rem 0.9rem;
  border: 1px solid var(--line);
  font-size: 0.9rem;
}

.banner.busy {
  background: rgba(15, 138, 107, 0.1);
  border-color: rgba(15, 138, 107, 0.35);
  color: var(--accent-deep);
}

.banner.ok {
  background: rgba(15, 138, 107, 0.08);
  color: var(--accent-deep);
}

.banner.error {
  background: rgba(196, 92, 38, 0.1);
  border-color: rgba(196, 92, 38, 0.35);
  color: var(--warn);
}

.table-wrap {
  overflow: auto;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.78);
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: 0.75rem 0.85rem;
  text-align: left;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}

th {
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-soft);
  background: rgba(243, 246, 244, 0.9);
  position: sticky;
  top: 0;
}

.rank {
  color: var(--ink-soft);
  width: 2.5rem;
}

.hero-cell {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}

.hero-cell img {
  width: 36px;
  height: 36px;
  object-fit: cover;
  background: #d9e4df;
}

.hero-cell strong {
  display: block;
  font-family: var(--display);
  font-size: 1rem;
  letter-spacing: -0.02em;
}

.hero-cell small {
  color: var(--ink-soft);
  font-size: 0.75rem;
}

.meter {
  width: 72px;
  height: 6px;
  background: rgba(16, 42, 46, 0.08);
  margin-bottom: 0.25rem;
  overflow: hidden;
}

.meter .bar {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #1fb890);
}

.hot {
  color: var(--accent-deep);
  font-weight: 500;
}

.empty {
  text-align: center;
  color: var(--ink-soft);
  padding: 2.5rem 1rem;
}

@media (max-width: 720px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .actions,
  .primary,
  .secondary {
    width: 100%;
  }
}
</style>
