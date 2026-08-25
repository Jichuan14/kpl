<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fetchDailyMatches } from "./api";

const emit = defineEmits(["predict"]);
const props = defineProps({
  predictionRefresh: { type: Number, default: 0 },
});

const minimized = ref(true);
const selectedDate = ref("");
const matches = ref([]);
const loading = ref(false);
const error = ref("");
const widgetRoot = ref(null);
const hasAnyPrediction = computed(() => matches.value.some(hasPrediction));

function browserDate(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shiftDate(value, days) {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + days);
  return browserDate(date);
}

function browserTime(match) {
  const start = matchStart(match);
  return Number.isNaN(start.getTime())
    ? "待定"
    : new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(start);
}

function browserMatchDate(match) {
  const start = matchStart(match);
  return Number.isNaN(start.getTime()) ? "" : browserDate(start);
}

function matchStart(match) {
  return new Date(`${match.start_time?.replace(" ", "T")}+08:00`);
}

function hasPrediction(match) {
  // Make browser-storage updates from the prediction modal reactive here.
  void props.predictionRefresh;
  try {
    const saved = JSON.parse(
      window.localStorage.getItem("kpl-series-winner-predictions") || "{}"
    );
    if (saved?.[match.match_id]) return true;
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith("kpl-daily-series-predictions:")) continue;
      const legacy = JSON.parse(window.localStorage.getItem(key) || "{}");
      if (legacy?.[match.match_id]) return true;
    }
    return false;
  } catch {
    return false;
  }
}

function openPredictions() {
  emit("predict", {
    date: selectedDate.value,
    matches: matches.value,
  });
}

async function matchesForLocalDate(date) {
  // A local calendar date can span two adjacent China dates. Request all
  // three candidates, then filter after converting timestamps in-browser.
  const chinaDates = [shiftDate(date, -1), date, shiftDate(date, 1)];
  const payloads = await Promise.all(
    chinaDates.map((chinaDate) => fetchDailyMatches({ date: chinaDate }))
  );
  const uniqueMatches = new Map();
  payloads.flatMap((payload) => payload?.matches || []).forEach((match) => {
    if (browserMatchDate(match) === date) uniqueMatches.set(match.match_id, match);
  });
  return [...uniqueMatches.values()].sort((a, b) =>
    String(a.start_time).localeCompare(String(b.start_time))
  );
}

async function loadMatches(date = selectedDate.value || browserDate()) {
  loading.value = true;
  error.value = "";
  try {
    matches.value = await matchesForLocalDate(date);
    selectedDate.value = date;
  } catch {
    matches.value = [];
    error.value = "赛事暂时无法加载。";
  } finally {
    loading.value = false;
  }
}

async function loadFirstAvailableDay(startDate = browserDate()) {
  const today = startDate;
  loading.value = true;
  error.value = "";
  try {
    // Prefer the next scheduled local day. If the local catalogue has not
    // been refreshed yet, check the nearby completed days instead of empty UI.
    for (let offset = 0; offset <= 7; offset += 1) {
      const date = shiftDate(today, offset);
      const rows = await matchesForLocalDate(date);
      const upcomingRows = offset === 0
        ? rows.filter((match) => matchStart(match).getTime() >= Date.now())
        : rows;
      if (upcomingRows.length) {
        selectedDate.value = date;
        matches.value = upcomingRows;
        return;
      }
    }
    for (let offset = 1; offset <= 7; offset += 1) {
      const date = shiftDate(today, -offset);
      const rows = await matchesForLocalDate(date);
      if (!rows.length) continue;
      selectedDate.value = date;
      matches.value = rows;
      return;
    }
    selectedDate.value = today;
    matches.value = [];
  } catch {
    matches.value = [];
    error.value = "赛事暂时无法加载。";
  } finally {
    loading.value = false;
  }
}

function moveDay(days) {
  loadMatches(shiftDate(selectedDate.value || browserDate(), days));
}

function toggleWidget() {
  minimized.value = !minimized.value;
  if (!minimized.value && !selectedDate.value && !loading.value) loadFirstAvailableDay();
}

function closeWhenClickingOutside(event) {
  if (!minimized.value && widgetRoot.value && !widgetRoot.value.contains(event.target)) {
    minimized.value = true;
  }
}

onMounted(() => {
  loadFirstAvailableDay();
  window.addEventListener("pointerdown", closeWhenClickingOutside);
});

onBeforeUnmount(() => {
  window.removeEventListener("pointerdown", closeWhenClickingOutside);
});
</script>

<template>
  <aside ref="widgetRoot" class="daily-matches-widget" :class="{ minimized }" aria-label="比赛日历">
    <button type="button" class="widget-toggle" :aria-expanded="String(!minimized)" @click="toggleWidget">
      <span aria-hidden="true">▣</span>
      <span>比赛日历</span>
      <small v-if="minimized">{{ matches.length || '' }}</small>
      <span v-else aria-hidden="true">−</span>
    </button>
    <div v-if="!minimized" class="widget-content">
      <label>
        <span>查看日期</span>
        <div class="date-control">
          <button type="button" aria-label="前一天" @click="moveDay(-1)">‹</button>
          <input v-model="selectedDate" type="date" @change="loadMatches()" />
          <button type="button" aria-label="后一天" @click="moveDay(1)">›</button>
        </div>
      </label>
      <p v-if="loading" class="widget-note">正在加载赛事…</p>
      <p v-else-if="error" class="widget-note error">{{ error }}</p>
      <p v-else-if="!matches.length" class="widget-note">当天暂无已排定赛事。</p>
      <ol v-else class="widget-match-list">
        <li v-for="match in matches" :key="match.match_id">
          <small>{{ browserTime(match) }} · BO{{ match.bo || '?' }}</small>
          <strong>{{ match.teams[0].team_name }} <i>vs</i> {{ match.teams[1].team_name }}</strong>
          <footer>
            <span>{{ match.league_name }}</span>
          </footer>
        </li>
      </ol>
      <button v-if="matches.length" type="button" class="widget-prediction-action" @click="openPredictions">
        {{ hasAnyPrediction ? '查看全部预测' : '预测今日比赛' }}
      </button>
    </div>
  </aside>
</template>

<style scoped>
.daily-matches-widget { position:fixed; z-index:65; right:1rem; bottom:1rem; width:min(21rem, calc(100vw - 2rem)); border:1px solid var(--line); background:rgba(253,251,245,.97); box-shadow:0 10px 28px rgba(16,42,46,.2); }.widget-toggle { display:flex; align-items:center; width:100%; min-height:38px; gap:.45rem; padding:.5rem .65rem; border:0; background:#102a2e; color:#fff; font:700 .65rem var(--mono); cursor:pointer; text-align:left; }.widget-toggle > span:last-child { margin-left:auto; font-size:1rem; }.widget-toggle small { display:grid; min-width:1rem; height:1rem; place-items:center; margin-left:auto; border-radius:50%; background:#e8bf6c; color:#102a2e; font-size:.55rem; }.widget-content { padding:.7rem; }.widget-content label { display:flex; align-items:center; justify-content:space-between; gap:.5rem; color:var(--ink-soft); font-size:.6rem; }.date-control { display:flex; align-items:center; gap:.25rem; }.widget-content input { min-height:30px; border:1px solid var(--line); background:#fff; color:var(--ink); font:inherit; }.date-control button { display:grid; width:30px; min-height:30px; place-items:center; padding:0; border:1px solid var(--line); background:#fff; color:var(--ink); font-size:1.15rem; cursor:pointer; }.widget-note { margin:.65rem 0 0; color:var(--ink-soft); font-size:.65rem; }.widget-note.error { color:#a84b4b; }.widget-match-list { display:grid; gap:.45rem; margin:.65rem 0 0; padding:0; list-style:none; }.widget-match-list li { display:grid; gap:.12rem; padding:.5rem; border:1px solid var(--line); background:#fff; }.widget-match-list small, .widget-match-list span { color:var(--ink-soft); font-size:.57rem; }.widget-match-list strong { font:700 .78rem var(--display); }.widget-match-list i { display:inline-block; margin:0 .55rem; color:var(--ink-soft); font:inherit; font-size:.64rem; }.widget-match-list footer { display:flex; align-items:center; justify-content:space-between; gap:.4rem; }.widget-prediction-action { width:100%; min-height:34px; margin-top:.65rem; border:1px solid var(--accent-deep); background:var(--accent-deep); color:#fff; font:700 .64rem var(--mono); cursor:pointer; }
@media (min-width: 700px) { .daily-matches-widget { right:1.5rem; bottom:1.5rem; width:min(29rem, calc(100vw - 3rem)); }.widget-toggle { min-height:48px; padding:.7rem .85rem; font-size:.78rem; }.widget-content { padding:1rem; }.widget-content label { font-size:.72rem; }.widget-content input, .date-control button { min-height:36px; }.date-control button { width:36px; }.widget-note { font-size:.75rem; }.widget-match-list { gap:.65rem; margin-top:.85rem; }.widget-match-list li { gap:.22rem; padding:.85rem; }.widget-match-list small, .widget-match-list span { font-size:.68rem; }.widget-match-list strong { font-size:1.08rem; }.widget-match-list i { margin:0 .85rem; font-size:.78rem; } }
@media (max-width: 640px) { .daily-matches-widget { position:absolute; z-index:62; top:.75rem; right:4.6rem; bottom:auto; width:auto; border:0; background:transparent; box-shadow:none; }.daily-matches-widget .widget-toggle { min-height:44px; justify-content:flex-start; gap:.35rem; padding:.45rem .55rem; border:1px solid var(--line); border-radius:.35rem; background:#102a2e; font-size:.55rem; white-space:nowrap; }.daily-matches-widget .widget-toggle > span:last-child, .daily-matches-widget .widget-toggle small { margin-left:0; }.daily-matches-widget .widget-content { position:absolute; top:calc(100% + .45rem); right:-4.6rem; width:min(20rem, calc(100vw - 1rem)); border:1px solid var(--line); border-radius:.35rem; background:rgba(253,251,245,.98); box-shadow:0 10px 28px rgba(16,42,46,.2); } }
@media (max-width: 520px) { .widget-content label { align-items:flex-start; flex-direction:column; }.date-control { width:100%; }.widget-content input { min-width:0; flex:1; } }
</style>
