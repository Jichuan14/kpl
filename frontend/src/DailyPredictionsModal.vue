<script setup>
import { computed, onMounted, ref } from "vue";
import {
  fetchLiveWinnerPredictions,
  saveLiveWinnerPrediction,
} from "./api";

const props = defineProps({
  date: { type: String, required: true },
  matches: { type: Array, default: () => [] },
  visitorId: { type: String, required: true },
});
const emit = defineEmits(["close"]);
const selections = ref({});
const totals = ref({});
const saving = ref({});
const sharing = ref("");
const shareNotice = ref("");
const storageKey = "kpl-series-winner-predictions";
const predictedMatches = computed(() =>
  props.matches.filter((match) => selections.value[match.match_id])
);

function savedSelections() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
    const legacy = {};
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith("kpl-daily-series-predictions:")) continue;
      Object.assign(legacy, JSON.parse(window.localStorage.getItem(key) || "{}"));
    }
    return {
      ...(legacy && typeof legacy === "object" ? legacy : {}),
      ...(saved && typeof saved === "object" ? saved : {}),
    };
  } catch {
    return {};
  }
}

function saveSelection(matchId, teamId) {
  selections.value = { ...selections.value, [matchId]: String(teamId) };
  window.localStorage.setItem(storageKey, JSON.stringify(selections.value));
}

function voteCount(match, teamId) {
  return totals.value[match.match_id]?.votes_by_team?.[String(teamId)] || 0;
}

function totalVotes(match) {
  return totals.value[match.match_id]?.total_votes || 0;
}

async function loadTotals(match) {
  try {
    totals.value = {
      ...totals.value,
      [match.match_id]: await fetchLiveWinnerPredictions({
        leagueId: match.league_id,
        matchId: match.match_id,
        gameNumber: 0,
      }),
    };
  } catch {
    // A missing optional prediction endpoint must not block the welcome popup.
  }
}

async function predict(match, teamId) {
  if (selections.value[match.match_id] || saving.value[match.match_id]) return;
  saving.value = { ...saving.value, [match.match_id]: true };
  try {
    const [teamA, teamB] = match.teams;
    const response = await saveLiveWinnerPrediction({
      leagueId: match.league_id,
      visitorId: props.visitorId,
      matchId: match.match_id,
      gameNumber: 0,
      teamAId: String(teamA.team_id),
      teamBId: String(teamB.team_id),
      winnerTeamId: String(teamId),
    });
    totals.value = {
      ...totals.value,
      [match.match_id]: response,
    };
    saveSelection(match.match_id, response.your_winner_team_id || teamId);
  } finally {
    saving.value = { ...saving.value, [match.match_id]: false };
  }
}

function drawText(context, text, x, y, size, color = "#102a2e") {
  context.fillStyle = color;
  context.font = `700 ${size}px system-ui, sans-serif`;
  context.fillText(text, x, y);
}

function roundedRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
  context.fill();
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = url;
  });
}

async function createPredictionImageBlob() {
    const canvas = document.createElement("canvas");
    canvas.width = 1080;
    canvas.height = 1350;
    const context = canvas.getContext("2d");
    context.fillStyle = "#f7f3e9";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#e8bf6c";
    context.fillRect(0, 0, canvas.width, 18);
    drawText(context, "KPL LAB", 72, 110, 42);
    drawText(context, "我的今日赛事预测", 72, 170, 52);
    drawText(context, props.date, 72, 215, 24, "#526467");
    let rowY = 280;
    for (const match of predictedMatches.value) {
      const winner = match.teams.find(
        (team) => String(team.team_id) === String(selections.value[match.match_id])
      );
      context.fillStyle = "#ffffff";
      roundedRect(context, 56, rowY - 38, 968, 104, 18);
      drawText(context, `${match.teams[0].team_name}  vs  ${match.teams[1].team_name}`, 82, rowY, 28);
      drawText(context, `BO${match.bo || "?"}`, 82, rowY + 42, 20, "#526467");
      context.fillStyle = "#e8bf6c";
      roundedRect(context, 520, rowY + 8, 470, 42, 12);
      drawText(context, `预测赢家  ${winner?.team_name || ""}`, 546, rowY + 38, 25, "#102a2e");
      rowY += 116;
    }
    context.fillStyle = "#102a2e";
    context.fillRect(0, 1050, canvas.width, 300);
    drawText(context, "扫描二维码填写你的预测", 72, 1130, 38, "#f7f3e9");
    drawText(context, "kpllab.xyz", 72, 1180, 26, "#e8bf6c");
    const qrX = 760;
    const qrY = 1085;
    try {
      const qr = await loadImage("https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=https%3A%2F%2Fkpllab.xyz");
      context.drawImage(qr, qrX, qrY, 200, 200);
    } catch {
      context.strokeStyle = "#f7f3e9";
      context.lineWidth = 5;
      context.strokeRect(qrX, qrY, 200, 200);
      drawText(context, "kpllab.xyz", qrX + 14, qrY + 108, 20, "#f7f3e9");
    }
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("Could not create share image");
    return blob;
}

function downloadPredictionImage(blob) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `kpl-lab-${props.date}-predictions.png`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function shareAllPredictions() {
  if (!predictedMatches.value.length) return;
  sharing.value = "all";
  shareNotice.value = "";
  const blobPromise = createPredictionImageBlob();
  try {
    const blob = await blobPromise;
    const file = new File([blob], `kpl-lab-${props.date}-predictions.png`, { type: "image/png" });
    // A file-only payload keeps the macOS Share sheet while avoiding a second
    // rich-preview item derived from title, text, or URL metadata.
    const shareData = { files: [file] };
    if (navigator.canShare?.(shareData) && navigator.share) {
      await navigator.share(shareData);
    } else {
      downloadPredictionImage(blob);
      shareNotice.value = "图片已下载。";
    }
  } finally {
    sharing.value = "";
  }
}

onMounted(() => {
  selections.value = savedSelections();
  props.matches.forEach(loadTotals);
});
</script>

<template>
  <div class="daily-prediction-scrim" @click.self="emit('close')">
    <section class="daily-prediction-modal" role="dialog" aria-modal="true" aria-labelledby="daily-prediction-title">
      <header>
        <div>
          <p>今日 KPL 赛事 · {{ date }}</p>
          <h2 id="daily-prediction-title">选出你看好的队伍</h2>
          <small>每场比赛只能提交一次预测。</small>
        </div>
        <button type="button" aria-label="关闭今日赛事预测" @click="emit('close')">×</button>
      </header>
      <div class="daily-match-list">
        <article v-for="match in matches" :key="match.match_id" class="daily-match-card">
          <small>{{ match.league_name }} · {{ match.start_time }} · BO{{ match.bo || '?' }}</small>
          <h3>{{ match.teams[0].team_name }} <span>vs</span> {{ match.teams[1].team_name }}</h3>
          <div class="daily-team-choices">
            <button
              v-for="team in match.teams"
              :key="team.team_id"
              type="button"
              :class="{ active: selections[match.match_id] === String(team.team_id) }"
              :disabled="Boolean(selections[match.match_id]) || saving[match.match_id]"
              @click="predict(match, team.team_id)"
            >
              {{ team.team_name }} 胜 <small>{{ voteCount(match, team.team_id) }} 票</small>
            </button>
          </div>
          <footer v-if="selections[match.match_id]">
            已预测 · {{ totalVotes(match) }} 人参与
          </footer>
        </article>
      </div>
      <div v-if="predictedMatches.length" class="combined-share">
        <span>{{ shareNotice || `已选 ${predictedMatches.length} 场比赛` }}</span>
        <button type="button" :disabled="sharing === 'all'" @click="shareAllPredictions">
          {{ sharing === 'all' ? '正在生成…' : '生成分享图片' }}
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.daily-prediction-scrim { position:fixed; z-index:80; inset:0; display:grid; place-items:center; padding:1rem; background:rgba(16,42,46,.58); }
.daily-prediction-modal { width:min(100%, 42rem); max-height:min(88vh, 42rem); overflow:auto; padding:1rem; border:1px solid var(--line); background:#fdfbf5; box-shadow:0 22px 60px rgba(0,0,0,.28); }
header { display:flex; justify-content:space-between; gap:.75rem; padding-bottom:.7rem; border-bottom:1px solid var(--line); } header p, header small, .daily-match-card > small { margin:0; color:var(--ink-soft); font-size:.61rem; } h2 { margin:.1rem 0; font:700 1.4rem var(--display); } header > button { width:1.8rem; height:1.8rem; border:1px solid var(--line); background:#fff; font-size:1.2rem; cursor:pointer; }
.daily-match-list { display:grid; gap:.45rem; margin-top:.65rem; }.daily-match-card { padding:.7rem .75rem; border:1px solid var(--line); background:#fff; }.daily-match-card h3 { margin:.2rem 0 .45rem; font:700 .9rem var(--display); }.daily-match-card h3 span { color:var(--ink-soft); font-size:.65rem; }.daily-team-choices { display:flex; flex-wrap:wrap; gap:.3rem; }.daily-team-choices button { min-height:30px; padding:.3rem .45rem; border:1px solid #9ab9cd; background:#f3f9fd; color:var(--ink); font:700 .61rem var(--mono); cursor:pointer; }.daily-team-choices button.active { border-color:var(--accent-deep); background:var(--ink); color:#fff; }.daily-team-choices button:disabled { cursor:not-allowed; opacity:.6; }.daily-team-choices small { color:inherit; opacity:.7; } footer { margin-top:.45rem; color:var(--ink-soft); font-size:.61rem; }
.combined-share { display:flex; align-items:center; justify-content:space-between; gap:.6rem; margin-top:.65rem; padding:.5rem .6rem; border:1px solid #d9b663; background:#fff8e7; color:var(--ink-soft); font-size:.62rem; }.combined-share button { min-height:32px; padding:.35rem .5rem; border:1px solid var(--accent-deep); background:var(--accent-deep); color:#fff; font:700 .61rem var(--mono); cursor:pointer; }.combined-share button:disabled { cursor:not-allowed; opacity:.6; }
@media (max-width: 520px) { .daily-prediction-modal { padding:.8rem; }.daily-team-choices button { flex:1; }.combined-share { align-items:stretch; flex-direction:column; }.combined-share button { width:100%; } }
</style>
