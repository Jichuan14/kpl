<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  fetchDraftModel,
  fetchLiveMatch,
  fetchLiveWinnerPredictions,
  refreshLiveMatch as requestLiveMatchRefresh,
  saveLiveWinnerPrediction,
  fetchSelectionCommentary,
  fetchSeasonTeams,
  fetchUpcomingMatch,
  fetchVisualizationSeasons,
  recommendLineup,
  scoreLineup,
  simulateDraft,
} from "./api";
import DraftCoachPanel from "./DraftCoachPanel.vue";
import TeamCombobox from "./TeamCombobox.vue";
import { selectAvailableLeague, selectedLeagueId } from "./selectedLeague";
import { heroAsset } from "./heroAssets";
import { messages } from "./i18n";
import { finishStartupLoading } from "./startupLoader";

const leagueId = selectedLeagueId;
const seasons = ref([]);
const model = ref(null);
const result = ref(null);
const recommendationResult = ref(null);
const recommendationLoading = ref(false);
const recommendationError = ref("");
const lineupScore = ref(null);
const lineupScoreLoading = ref(false);
const lineupScoreError = ref("");
const expandedRecommendationIds = ref([]);
const commentary = ref(null);
const commentaryLoading = ref(false);
const commentaryEnabled = ref(false);
const settingsOpen = ref(false);
let commentaryRequestNumber = 0;
let recommendationRequestNumber = 0;
let lineupScoreRequestNumber = 0;
const loading = ref(false);
const simulating = ref(false);
const error = ref("");
const search = ref("");
const laneFilter = ref("all");
// BP forecasts always use the chronological GRU model. Keeping this fixed
// avoids presenting model choice in either the desktop or mobile interface.
const modelType = "sequence";
const bpOrder = ref(1);
const board = ref(emptyBoard());
const history = ref([]);
const globalMode = ref("match");
const seriesGame = ref(1);
const bestOf = ref(5);
const TEAM_A = "team-a";
const TEAM_B = "team-b";
const globalUsed = ref({ [TEAM_A]: [], [TEAM_B]: [] });
const seasonTeams = ref([]);
const upcomingMatch = ref(null);
const liveMatch = ref(null);
const liveMatchLoading = ref(false);
const liveFollowing = ref(false);
const liveFollowDismissed = ref(false);
const liveFollowFinished = ref(false);
const liveAppliedGameSignature = ref("");
const liveScheduleClock = ref(Date.now());
let liveMatchPollTimer = null;
let liveMatchCheckTimer = null;
let liveScheduleTimer = null;
let liveMatchRequestNumber = 0;
const selectedTeamIds = ref({ [TEAM_A]: "", [TEAM_B]: "" });
const teamsBySide = ref({ blue: TEAM_A, red: TEAM_B });
const seriesWins = ref({ [TEAM_A]: 0, [TEAM_B]: 0 });
const winnerSide = ref(null);
const nextBlueTeam = ref(null);
const pickerTarget = ref("draft");
const coachOpen = ref(false);
const usedHeroesModalSide = ref(null);
const draftBoardElement = ref(null);
const liveFollowStorageKey = "kpl-live-match-following";
const livePredictionStorageKey = "kpl-live-winner-predictions";
const liveWinnerPredictions = ref(null);
const liveWinnerPredictionLoading = ref(false);
const liveWinnerPredictionSaving = ref(false);
const liveWinnerPredictionSelections = ref({});

function bpT(key) {
  return messages["zh-CN"][key] || key;
}

function emptyBoard() {
  return {
    blue_picks: [],
    red_picks: [],
    blue_bans: [],
    red_bans: [],
  };
}

const isPeakDuel = computed(
  () =>
    globalMode.value === "match" &&
    Number(bestOf.value) === 7 &&
    Number(seriesGame.value) === 7
);

const currentStep = computed(() =>
  model.value?.draft_sequence?.find(
    (step) => Number(step.bp_order) === Number(bpOrder.value)
  ) || null
);

const lineupComplete = computed(
  () => board.value.blue_picks.length === 5 && board.value.red_picks.length === 5
);

const currentLabel = computed(() => {
  if (isPeakDuel.value) return "巅峰对决，不用BP";
  if (!currentStep.value) return bpT("Draft complete");
  const side = bpT(currentStep.value.side === "blue" ? "Blue" : "Red");
  const action = bpT(currentStep.value.action === "ban" ? "ban" : "pick");
  return `${side}${action} · 第 ${currentStep.value.bp_order} 手`;
});

const usedHeroIds = computed(
  () =>
    new Set(
      Object.values(board.value).flatMap((heroIds) => heroIds.map(Number))
    )
);

const heroes = computed(() => model.value?.heroes || []);

const laneOptions = [
  { value: "all", label: "全部英雄" },
  { value: "6", label: "对抗路" },
  { value: "2", label: "中路" },
  { value: "5", label: "打野" },
  { value: "7", label: "发育路" },
  { value: "4", label: "游走" },
];

const pickerTitle = computed(() => {
  if (pickerTarget.value === "global-blue") return addEarlierHeroLabel(teamsBySide.value.blue);
  if (pickerTarget.value === "global-red") return addEarlierHeroLabel(teamsBySide.value.red);
  return currentLabel.value;
});

const losingSide = computed(() =>
  winnerSide.value === "blue" ? "red" : winnerSide.value === "red" ? "blue" : null
);

const losingTeam = computed(() =>
  losingSide.value ? teamsBySide.value[losingSide.value] : null
);

const winsNeeded = computed(() => Math.ceil(bestOf.value / 2));

const seriesWinner = computed(() =>
  [TEAM_A, TEAM_B].find((team) => seriesWins.value[team] >= winsNeeded.value) || null
);

const canChangeCurrentSides = computed(
  () => teamsReady.value && !history.value.length && !isPeakDuel.value
);

const probabilityByHeroId = computed(
  () =>
    new Map(
      (result.value?.next_action_probabilities || []).map((row) => [
        Number(row.hero_id),
        Number(row.probability),
      ])
    )
);

const availableHeroes = computed(() => {
  const needle = search.value.trim().toLocaleLowerCase();
  const selectedLane = Number(laneFilter.value);
  const targetSide = pickerTarget.value.replace("global-", "");
  const targetTeam = teamsBySide.value[targetSide];
  const candidates =
    pickerTarget.value === "draft" && result.value
      ? result.value.next_action_probabilities
          .map((row) => heroes.value.find((hero) => Number(hero.hero_id) === Number(row.hero_id)))
          .filter(Boolean)
      : heroes.value;
  return candidates
    .filter((hero) => {
      const heroId = Number(hero.hero_id);
      const unavailableForTarget =
        pickerTarget.value === "draft"
          ? usedHeroIds.value.has(heroId)
          : globalUsed.value[targetTeam].includes(heroId) || usedHeroIds.value.has(heroId);
      const matchesLane =
        pickerTarget.value !== "draft" ||
        laneFilter.value === "all" ||
        (hero.positions || []).map(Number).includes(selectedLane);
      return (
        !unavailableForTarget &&
        matchesLane &&
        (!needle || hero.hero_name.toLocaleLowerCase().includes(needle))
      );
    })
    .sort(
      (a, b) =>
        (probabilityByHeroId.value.get(Number(b.hero_id)) || 0) -
          (probabilityByHeroId.value.get(Number(a.hero_id)) || 0) ||
        a.hero_name.localeCompare(b.hero_name)
    );
});

const selectedSeason = computed(() =>
  seasons.value.find((season) => season.league_id === leagueId.value)
);

const teamsReady = computed(
  () =>
    Boolean(selectedTeam(TEAM_A) && selectedTeam(TEAM_B)) &&
    selectedTeamIds.value[TEAM_A] !== selectedTeamIds.value[TEAM_B]
);

const scheduledMatchStarted = computed(() => {
  const delay = scheduledLiveCheckDelay(upcomingMatch.value);
  return Boolean(
    teamsReady.value &&
      selectedTeamsMatchFixture() &&
      delay !== null &&
      delay <= 5 * 60_000
  );
});
const liveApiCheckAvailable = computed(() => {
  const delay = scheduledLiveCheckDelay(upcomingMatch.value);
  return delay !== null && delay <= 0;
});
const liveHeroSelectionLocked = computed(
  () =>
    liveFollowing.value &&
    Boolean(liveMatch.value?.hero_selection_locked || liveFollowFinished.value)
);
const liveOfficialHeroContextLocked = computed(
  () => liveFollowing.value && Boolean(liveMatch.value?.is_live)
);
const liveMatchStatusLabel = computed(() => {
  if (!liveMatch.value?.match) {
    return liveApiCheckAvailable.value
      ? "正在等待官方赛况"
      : "将在开赛五分钟后开始同步官方赛况";
  }
  const teams = liveMatch.value.match.teams || [];
  const score = teams.map((team) => `${team.team_name} ${team.score}`).join(" – ");
  if (liveFollowFinished.value || liveMatch.value.is_finished) return `${score} · 系列赛已结束`;
  if (liveMatch.value.current_game_status === "in_progress") {
    return `${score} · 第 ${liveMatch.value.current_game} 局进行中`;
  }
  return `${score} · 等待第 ${liveMatch.value.current_game} 局开始`;
});
const liveRefreshNotice = computed(() => {
  const refresh = liveMatch.value?.official_refresh;
  if (!refresh) return "";
  if (refresh.performed) return "已刷新 KPL 官方数据。";
  const seconds = Number(refresh.manual_refresh_available_in_seconds || 0);
  return seconds > 0
    ? `当前展示的是官方缓存数据，${seconds} 秒后可手动刷新。`
    : "当前展示的是最新的官方缓存数据。";
});
const livePredictionGame = computed(() => {
  const game = Number(liveMatch.value?.current_game || seriesGame.value);
  return Number.isInteger(game) && game > 0 ? game : null;
});
const livePredictionMatchId = computed(() =>
  String(liveMatch.value?.match?.match_id || upcomingMatch.value?.match_id || "")
);
const livePredictionVotes = computed(() => liveWinnerPredictions.value?.votes_by_team || {});
const livePredictionTotal = computed(() => Number(liveWinnerPredictions.value?.total_votes || 0));
const selectedWinnerPrediction = computed(() => {
  const key = `${livePredictionMatchId.value}:${livePredictionGame.value}`;
  return liveWinnerPredictionSelections.value[key] || readLiveWinnerPredictions()[key] || null;
});

const upcomingMatchLabel = computed(() => {
  if (!upcomingMatch.value) return "";
  const teams = upcomingMatch.value.teams || [];
  if (teams.length !== 2) return "";
  const prefix = upcomingMatch.value.is_live ? "正在进行" : "下一场赛程";
  return `${prefix} · ${teams[0].team_name} 对阵 ${teams[1].team_name} · ${upcomingMatch.value.start_time}（中国时间）`;
});

const firstTeamLabel = computed(() => "选择战队");
const secondTeamLabel = computed(() => "选择对手");

const boardGroups = computed(() => [
  { key: "blue_bans", title: `${bpT("Blue bans")} · ${teamName(teamsBySide.value.blue)}`, mobileTitle: bpT("Blue bans"), tone: "blue" },
  { key: "blue_picks", title: `${bpT("Blue picks")} · ${teamName(teamsBySide.value.blue)}`, mobileTitle: bpT("Blue picks"), tone: "blue" },
  { key: "red_bans", title: `${bpT("Red bans")} · ${teamName(teamsBySide.value.red)}`, mobileTitle: bpT("Red bans"), tone: "red" },
  { key: "red_picks", title: `${bpT("Red picks")} · ${teamName(teamsBySide.value.red)}`, mobileTitle: bpT("Red picks"), tone: "red" },
]);

const coachDraftState = computed(() => {
  if (isPeakDuel.value || !currentStep.value || !teamsReady.value) return null;
  const blue = selectedTeam(teamsBySide.value.blue);
  const red = selectedTeam(teamsBySide.value.red);
  return {
    model_type: modelType,
    blue_team_id: String(blue.team_id),
    blue_team_name: blue.team_name,
    red_team_id: String(red.team_id),
    red_team_name: red.team_name,
    bp_order: bpOrder.value,
    blue_picks: [...board.value.blue_picks],
    red_picks: [...board.value.red_picks],
    blue_bans: [...board.value.blue_bans],
    red_bans: [...board.value.red_bans],
    blue_used_previous_battles: [
      ...globalUsed.value[teamsBySide.value.blue],
    ],
    red_used_previous_battles: [
      ...globalUsed.value[teamsBySide.value.red],
    ],
  };
});

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function signedPoints(value) {
  const points = Number(value || 0) * 100;
  return `${points >= 0 ? "+" : ""}${points.toFixed(1)} 分`;
}

function signedContribution(value) {
  const numberValue = Number(value || 0);
  return `${numberValue >= 0 ? "+" : ""}${numberValue.toFixed(3)}`;
}

function confidenceLabel(value) {
  return {
    high: "参考较可靠",
    medium: "参考一般",
    low: "谨慎参考",
  }[value] || value;
}

function recommendationMetricLabel(metric, action) {
  return {
    score: action === "ban" ? "禁用效果评分" : "阵容对比评分",
    delta: "比常规选择",
    confidence: "参考可靠度",
    policy: "职业选择倾向",
  }[metric];
}

function recommendationMetricHelp(metric, action) {
  return {
    score:
      action === "ban"
        ? "综合对手偏好、英雄克制和历史禁用结果，分数越高越值得优先禁用；它不是胜率。"
        : "模拟完整 BP 后，衡量己方最终阵容相对占优的程度；它不是比赛胜率。",
    delta: "与模型在同一局面下的常规选择相比，正数表示这手预计更有利。",
    confidence: "综合历史样本量、模拟完成情况和结果波动判断；“谨慎参考”表示不确定性较高。",
    policy: "表示职业队在类似 BP 局面选择这名英雄的模型概率，不代表选择后的胜率。",
  }[metric];
}

function recommendationDetailsExpanded(heroId) {
  return expandedRecommendationIds.value.includes(Number(heroId));
}

function toggleRecommendationDetails(heroId) {
  const id = Number(heroId);
  expandedRecommendationIds.value = recommendationDetailsExpanded(id)
    ? expandedRecommendationIds.value.filter((candidate) => candidate !== id)
    : [...expandedRecommendationIds.value, id];
}

function recommendationReasonLabel(reason) {
  const labels = {
    behavior_support: "符合战队 BP 倾向",
    denies_opponent_comfort: "限制对手高频英雄",
    protects_current_picks: "保护当前阵容免受克制",
    breaks_opponent_synergy: "拆解对手阵容协同",
    self_opportunity_cost: "也会损失己方可选英雄",
    largest_ban_component: {
      opponent_denial: "主要因素：对手英雄威胁",
      context_denial: "主要因素：当前阵容克制",
      observed_ban_outcome: "主要因素：历史禁用效果",
    }[reason.component] || "禁用模型主要因素",
    adds_frontline: "补充前排",
    adds_primary_engage: "补充主动开团",
    adds_hard_cc: "补充硬控",
    mage_redundancy: "注意多法师重叠",
    team_hero_familiarity: reason.value >= 0 ? "战队英雄历史表现较好" : "战队英雄历史表现偏弱",
    largest_value_component: {
      team_strength: "主要因素：战队实力",
      selected_hero_familiarity: "主要因素：英雄熟练度",
      hero_synergy: "主要因素：阵容协同",
      hero_counters: "主要因素：历史克制",
    }[reason.component] || "价值模型主要因素",
  };
  return labels[reason.code] || reason.code;
}

function lineupComponentLabel(component) {
  return {
    team_strength: "战队实力",
    selected_hero_familiarity: "英雄熟练度",
    hero_synergy: "阵容协同",
    hero_counters: "英雄克制",
  }[component] || component;
}

function number(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function heroName(heroId) {
  return (
    heroes.value.find((hero) => Number(hero.hero_id) === Number(heroId))
      ?.hero_name || String(heroId)
  );
}

function heroIcon(heroId) {
  return heroAsset(heroId);
}

function teamName(team) {
  return selectedTeam(team)?.team_name || bpT(team === TEAM_A ? "Blue Team" : "Red Team");
}

function selectedTeam(team) {
  const teamId = selectedTeamIds.value[team];
  return seasonTeams.value.find(
    (candidate) => String(candidate.team_id) === String(teamId)
  );
}

function selectTeamForSide(team, teamId) {
  const otherTeam = team === TEAM_A ? TEAM_B : TEAM_A;
  const currentTeamId = selectedTeamIds.value[team];
  const otherTeamId = selectedTeamIds.value[otherTeam];

  // Picking the team already assigned to the opposite side means the user is
  // swapping sides, not trying to create a same-team matchup.
  if (String(teamId) === String(otherTeamId) && currentTeamId) {
    selectedTeamIds.value = {
      ...selectedTeamIds.value,
      [team]: String(teamId),
      [otherTeam]: String(currentTeamId),
    };
    return;
  }
  selectedTeamIds.value = { ...selectedTeamIds.value, [team]: String(teamId) };
}

function sideLabel(side) {
  return bpT(side === "blue" ? "Blue" : "Red");
}

async function setTeamForDraftSide(side, team) {
  if (!canChangeCurrentSides.value || ![TEAM_A, TEAM_B].includes(team)) return;
  const otherTeam = team === TEAM_A ? TEAM_B : TEAM_A;
  teamsBySide.value =
    side === "blue"
      ? { blue: team, red: otherTeam }
      : { blue: otherTeam, red: team };
  await forecast();
}

async function swapDraftSides() {
  if (!canChangeCurrentSides.value) return;
  teamsBySide.value = {
    blue: teamsBySide.value.red,
    red: teamsBySide.value.blue,
  };
  await forecast();
}

function sideUsedLabel(side) {
  return `${sideLabel(side)} · ${teamName(teamsBySide.value[side])} ${bpT("used earlier")}`;
}

function addEarlierHeroLabel(team) {
  return bpT("Add {team}'s earlier-game hero").replace("{team}", teamName(team));
}

function earlierGamesLabel(team) {
  return bpT("{team} earlier games").replace("{team}", teamName(team));
}

function gameWinnerLabel(game) {
  return bpT("Game {game} winner").replace("{game}", game);
}

function loserColorChoiceLabel(team) {
  return bpT("{team} chooses next color").replace("{team}", teamName(team));
}

function startGameLabel(game) {
  return bpT("Start game {game}").replace("{game}", game);
}

function seriesWinnerLabel() {
  return `${teamName(seriesWinner.value)} 赢得 BO${bestOf.value}`;
}

function seriesStatusLabel() {
  return `BO${bestOf.value} · 第 ${seriesGame.value} 局 · ${bpT(
    globalMode.value === "custom" ? "custom prior usage" : "tracked from earlier games"
  )}`;
}

async function setMatchMode(mode) {
  if (mode === "single") await clearGlobalBp();
  else if (mode === "custom") await customizeGlobalBp();
  else await startGlobalBp();
  settingsOpen.value = false;
}

function forecastLabel() {
  const step = result.value?.next_step;
  return step ? `${sideLabel(step.side)}${bpT(step.action)}` : "";
}

function resetSeriesTeams() {
  globalUsed.value = { [TEAM_A]: [], [TEAM_B]: [] };
  teamsBySide.value = { blue: TEAM_A, red: TEAM_B };
  seriesWins.value = { [TEAM_A]: 0, [TEAM_B]: 0 };
  winnerSide.value = null;
  nextBlueTeam.value = null;
}

function teamsWithUpcomingFixtureFirst(teams, fixture) {
  const fixtureIds = (fixture?.teams || []).map((team) => String(team.team_id));
  if (fixtureIds.length !== 2 || fixtureIds[0] === fixtureIds[1]) return teams;
  const byId = new Map(teams.map((team) => [String(team.team_id), team]));
  const scheduled = fixtureIds.map((teamId) => byId.get(teamId)).filter(Boolean);
  if (scheduled.length !== 2) return teams;
  const scheduledIds = new Set(fixtureIds);
  return [...scheduled, ...teams.filter((team) => !scheduledIds.has(String(team.team_id)))];
}

function readLiveFollowPreference() {
  try {
    const value = JSON.parse(window.localStorage.getItem(liveFollowStorageKey) || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

function saveLiveFollowPreference(source) {
  const matchId = String(source?.match?.match_id || source?.match_id || "");
  if (!matchId || !leagueId.value) return;
  window.localStorage.setItem(
    liveFollowStorageKey,
    JSON.stringify({ leagueId: String(leagueId.value), matchId })
  );
}

function clearLiveFollowPreference() {
  window.localStorage.removeItem(liveFollowStorageKey);
}

function readLiveWinnerPredictions() {
  try {
    const value = JSON.parse(window.localStorage.getItem(livePredictionStorageKey) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function saveLocalWinnerPrediction(teamId) {
  const matchId = livePredictionMatchId.value;
  const game = livePredictionGame.value;
  if (!matchId || !game) return;
  const predictions = readLiveWinnerPredictions();
  predictions[`${matchId}:${game}`] = String(teamId);
  window.localStorage.setItem(livePredictionStorageKey, JSON.stringify(predictions));
  liveWinnerPredictionSelections.value = predictions;
}

function anonymousVisitorId() {
  const key = "draft-atlas-visitor-id";
  try {
    const stored = window.localStorage.getItem(key);
    if (stored) return stored;
    const id = window.crypto.randomUUID();
    window.localStorage.setItem(key, id);
    return id;
  } catch {
    return window.crypto.randomUUID();
  }
}

async function loadLiveWinnerPredictions() {
  const matchId = livePredictionMatchId.value;
  const game = livePredictionGame.value;
  if (!liveFollowing.value || !leagueId.value || !matchId || !game) {
    liveWinnerPredictions.value = null;
    return;
  }
  liveWinnerPredictionLoading.value = true;
  try {
    liveWinnerPredictions.value = await fetchLiveWinnerPredictions({
      leagueId: leagueId.value,
      matchId,
      gameNumber: game,
    });
  } catch {
    // Prediction totals are supplementary; keep the live simulator usable.
  } finally {
    liveWinnerPredictionLoading.value = false;
  }
}

async function chooseLiveWinnerPrediction(teamId) {
  const matchId = livePredictionMatchId.value;
  const game = livePredictionGame.value;
  if (
    !liveFollowing.value ||
    !matchId ||
    !game ||
    selectedWinnerPrediction.value ||
    liveWinnerPredictionSaving.value
  ) return;
  liveWinnerPredictionSaving.value = true;
  try {
    const response = await saveLiveWinnerPrediction({
      leagueId: leagueId.value,
      visitorId: anonymousVisitorId(),
      matchId,
      gameNumber: game,
      teamAId: String(selectedTeamIds.value[TEAM_A]),
      teamBId: String(selectedTeamIds.value[TEAM_B]),
      winnerTeamId: String(teamId),
    });
    liveWinnerPredictions.value = response;
    saveLocalWinnerPrediction(response.your_winner_team_id || teamId);
  } catch (err) {
    error.value = err.message || "Could not save your winner prediction.";
  } finally {
    liveWinnerPredictionSaving.value = false;
  }
}

function shouldRestoreLiveFollow(state) {
  const saved = readLiveFollowPreference();
  return Boolean(
    saved &&
      saved.leagueId === String(leagueId.value) &&
      saved.matchId === String(state?.match?.match_id || "") &&
      state?.is_live
  );
}

function shouldRestoreScheduledFollow() {
  const saved = readLiveFollowPreference();
  return Boolean(
    saved &&
      saved.leagueId === String(leagueId.value) &&
      saved.matchId === String(upcomingMatch.value?.match_id || "") &&
      scheduledMatchStarted.value
  );
}

function stopLiveMatchPolling() {
  if (liveMatchPollTimer !== null) {
    window.clearInterval(liveMatchPollTimer);
    liveMatchPollTimer = null;
  }
}

function stopLiveMatchCheckSchedule() {
  if (liveMatchCheckTimer !== null) {
    window.clearTimeout(liveMatchCheckTimer);
    liveMatchCheckTimer = null;
  }
}

function stopLiveScheduleClock() {
  if (liveScheduleTimer !== null) {
    window.clearTimeout(liveScheduleTimer);
    liveScheduleTimer = null;
  }
}

function scheduledLiveCheckDelay(fixture) {
  const match = String(fixture?.start_time || "").match(
    /^(\d{4})-(\d{2})-(\d{2})\s+?(\d{2}):(\d{2}):(\d{2})$/
  );
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match;
  // The catalogue stores China time. Date.UTC with an eight-hour offset avoids
  // treating that string as the visitor's local computer time.
  const scheduledAt = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour) - 8,
    Number(minute),
    Number(second)
  );
  return scheduledAt + 5 * 60_000 - liveScheduleClock.value;
}

function selectedTeamsMatchFixture() {
  const fixtureIds = new Set((upcomingMatch.value?.teams || []).map((team) => String(team.team_id)));
  const selectedIds = new Set([
    String(selectedTeamIds.value[TEAM_A] || ""),
    String(selectedTeamIds.value[TEAM_B] || ""),
  ]);
  return fixtureIds.size === 2 && fixtureIds.size === selectedIds.size && [...fixtureIds].every((id) => selectedIds.has(id));
}

function scheduleLiveScheduleClock() {
  stopLiveScheduleClock();
  if (!teamsReady.value || !selectedTeamsMatchFixture()) return;
  const checkDelay = scheduledLiveCheckDelay(upcomingMatch.value);
  if (checkDelay === null) return;
  const waits = [checkDelay - 5 * 60_000, checkDelay].filter((wait) => wait > 0);
  if (!waits.length) return;
  liveScheduleTimer = window.setTimeout(() => {
    liveScheduleTimer = null;
    liveScheduleClock.value = Date.now();
    scheduleLiveScheduleClock();
  }, Math.min(Math.min(...waits), 2_147_000_000));
}

function scheduleLiveMatchCheck() {
  stopLiveMatchCheckSchedule();
  if (!teamsReady.value || !selectedTeamsMatchFixture()) return;
  const delay = scheduledLiveCheckDelay(upcomingMatch.value);
  if (delay === null) return;
  const wait = Math.max(0, delay);
  liveMatchCheckTimer = window.setTimeout(() => {
    liveMatchCheckTimer = null;
    refreshLiveMatch();
  }, Math.min(wait, 2_147_000_000));
}

function startLiveMatchPolling() {
  stopLiveMatchPolling();
  // The browser checks in frequently enough to react to cache expiry, while
  // the backend itself limits official KPL requests to one per three minutes.
  liveMatchPollTimer = window.setInterval(refreshLiveMatch, 30_000);
}

function completedGameSignature(state) {
  return (state?.completed_games || [])
    .map((game) => `${game.battle_id}:${game.game}`)
    .join(",");
}

function isOfficialSeriesComplete(state) {
  if (state?.is_finished) return true;
  const bestOf = Number(state?.match?.bo || 0);
  if (bestOf < 1) return false;
  const winsNeeded = Math.ceil(bestOf / 2);
  return (state?.match?.teams || []).some(
    (team) => Number(team.score || 0) >= winsNeeded
  );
}

async function applyLiveMatchState(state) {
  if (!state?.match || !teamsReady.value) return;
  globalMode.value = "match";
  if (Number(state.match.bo) > 0) bestOf.value = Number(state.match.bo);
  resetSeriesTeams();
  globalUsed.value = {
    [TEAM_A]: [...new Set(state.used_hero_ids_by_team?.[selectedTeamIds.value[TEAM_A]] || [])],
    [TEAM_B]: [...new Set(state.used_hero_ids_by_team?.[selectedTeamIds.value[TEAM_B]] || [])],
  };
  const officialTeams = state.match.teams || [];
  seriesWins.value = {
    [TEAM_A]: Number(
      officialTeams.find((team) => String(team.team_id) === String(selectedTeamIds.value[TEAM_A]))?.score || 0
    ),
    [TEAM_B]: Number(
      officialTeams.find((team) => String(team.team_id) === String(selectedTeamIds.value[TEAM_B]))?.score || 0
    ),
  };
  seriesGame.value = Number(state.current_game) || 1;
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "draft";
  winnerSide.value = null;
  nextBlueTeam.value = null;
  await forecast();
}

async function moveToNextScheduledFixture() {
  let fixture;
  try {
    fixture = await fetchUpcomingMatch(leagueId.value, { nextOnly: true });
  } catch {
    return;
  }
  if (!fixture || String(fixture.match_id || "") === String(liveMatch.value?.match?.match_id || "")) {
    return;
  }
  const fixtureTeams = (fixture.teams || [])
    .map((fixtureTeam) =>
      seasonTeams.value.find(
        (team) => String(team.team_id) === String(fixtureTeam.team_id)
      )
    )
    .filter(Boolean);
  if (fixtureTeams.length !== 2 || String(fixtureTeams[0].team_id) === String(fixtureTeams[1].team_id)) {
    return;
  }
  upcomingMatch.value = fixture;
  seasonTeams.value = teamsWithUpcomingFixtureFirst(seasonTeams.value, fixture);
  globalMode.value = "match";
  seriesGame.value = 1;
  if ([5, 7].includes(Number(fixture.bo))) bestOf.value = Number(fixture.bo);
  resetSeriesTeams();
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  result.value = null;
  commentary.value = null;
  liveMatch.value = null;
  liveFollowDismissed.value = false;
  selectedTeamIds.value = {
    [TEAM_A]: String(fixtureTeams[0].team_id),
    [TEAM_B]: String(fixtureTeams[1].team_id),
  };
}

async function refreshLiveMatch(manual = false) {
  if (!leagueId.value || !teamsReady.value || !upcomingMatch.value?.match_id) return;
  const requestNumber = ++liveMatchRequestNumber;
  liveMatchLoading.value = true;
  try {
    const payload = {
      leagueId: leagueId.value,
      teamAId: String(selectedTeamIds.value[TEAM_A]),
      teamBId: String(selectedTeamIds.value[TEAM_B]),
      matchId: String(upcomingMatch.value.match_id),
    };
    const state = manual
      ? await requestLiveMatchRefresh(payload)
      : await fetchLiveMatch(payload);
    if (requestNumber !== liveMatchRequestNumber) return;
    liveMatch.value = state;
    if (!liveFollowing.value && shouldRestoreLiveFollow(state)) {
      await followLiveMatch({ persist: false });
      return;
    }
    if (isOfficialSeriesComplete(state)) {
      if (liveFollowing.value) await applyLiveMatchState(state);
      stopFollowingLiveMatch();
      stopLiveMatchPolling();
      stopLiveMatchCheckSchedule();
      await moveToNextScheduledFixture();
      return;
    }
    if (!liveFollowing.value) {
      // This lightweight monitor only advances the scheduled matchup. It does
      // not apply heroes or alter the visitor's local draft board.
      startLiveMatchPolling();
      return;
    }
    await loadLiveWinnerPredictions();
    const gameSignature = completedGameSignature(state);
    if (gameSignature !== liveAppliedGameSignature.value) {
      // Only a newly completed official battle replaces the temporary local BP
      // board. Late hero-detail updates for that battle keep the same stable
      // battle signature and therefore leave the current game's BP untouched.
      await applyLiveMatchState(state);
      liveAppliedGameSignature.value = gameSignature;
    }
    startLiveMatchPolling();
  } catch {
    // A temporary official API failure should not remove the last usable live
    // context from a visitor's simulator.
  } finally {
    if (requestNumber === liveMatchRequestNumber) liveMatchLoading.value = false;
  }
}

async function followLiveMatch({ persist = true } = {}) {
  if (!scheduledMatchStarted.value) return;
  liveFollowing.value = true;
  liveFollowDismissed.value = true;
  liveFollowFinished.value = false;
  if (persist) saveLiveFollowPreference(liveMatch.value || upcomingMatch.value);
  if (liveMatch.value?.is_live) {
    await applyLiveMatchState(liveMatch.value);
    liveAppliedGameSignature.value = completedGameSignature(liveMatch.value);
  }
  await loadLiveWinnerPredictions();
  scheduleLiveMatchCheck();
}

function stopFollowingLiveMatch({ forget = true } = {}) {
  liveFollowing.value = false;
  liveFollowFinished.value = false;
  liveAppliedGameSignature.value = "";
  liveWinnerPredictions.value = null;
  if (forget) clearLiveFollowPreference();
}

async function loadSeasons() {
  seasons.value = (await fetchVisualizationSeasons()) || [];
  selectAvailableLeague(seasons.value);
}

async function loadModel() {
  if (!leagueId.value) return;
  loading.value = true;
  error.value = "";
  result.value = null;
  lineupScoreRequestNumber += 1;
  lineupScore.value = null;
  lineupScoreLoading.value = false;
  lineupScoreError.value = "";
  commentary.value = null;
  model.value = null;
  seasonTeams.value = [];
  upcomingMatch.value = null;
  stopFollowingLiveMatch({ forget: false });
  stopLiveMatchPolling();
  stopLiveMatchCheckSchedule();
  stopLiveScheduleClock();
  liveMatch.value = null;
  liveFollowDismissed.value = false;
  liveAppliedGameSignature.value = "";
  selectedTeamIds.value = { [TEAM_A]: "", [TEAM_B]: "" };
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  globalMode.value = "match";
  seriesGame.value = 1;
  bestOf.value = 5;
  resetSeriesTeams();
  pickerTarget.value = "draft";
  try {
    const [draftModel, teams, fixture] = await Promise.all([
      fetchDraftModel(leagueId.value),
      fetchSeasonTeams(leagueId.value),
      fetchUpcomingMatch(leagueId.value),
    ]);
    model.value = draftModel;
    upcomingMatch.value = fixture;
    if ([5, 7].includes(Number(fixture?.bo))) bestOf.value = Number(fixture.bo);
    seasonTeams.value = teamsWithUpcomingFixtureFirst(teams, fixture);
    const scheduledTeams = (fixture?.teams || [])
      .map((fixtureTeam) =>
        teams.find((team) => String(team.team_id) === String(fixtureTeam.team_id))
      )
      .filter(Boolean);
    if (scheduledTeams.length === 2 && String(scheduledTeams[0].team_id) !== String(scheduledTeams[1].team_id)) {
      selectedTeamIds.value = {
        [TEAM_A]: String(scheduledTeams[0].team_id),
        [TEAM_B]: String(scheduledTeams[1].team_id),
      };
    }
  } catch (err) {
    model.value = null;
    error.value = err.message || "Could not load this season's draft model.";
  } finally {
    loading.value = false;
  }
}

async function forecast() {
  recommendationRequestNumber += 1;
  recommendationLoading.value = false;
  recommendationResult.value = null;
  recommendationError.value = "";
  lineupScoreRequestNumber += 1;
  lineupScore.value = null;
  lineupScoreLoading.value = false;
  lineupScoreError.value = "";
  if (isPeakDuel.value) {
    result.value = null;
    return;
  }
  if (!teamsReady.value) {
    result.value = null;
    return;
  }
  if (!model.value) {
    result.value = null;
    return;
  }
  if (lineupComplete.value) {
    result.value = null;
    await scoreCompletedLineup();
    return;
  }
  if (!currentStep.value) {
    result.value = null;
    return;
  }
  if (simulating.value) return;
  const blue = selectedTeam(teamsBySide.value.blue);
  const red = selectedTeam(teamsBySide.value.red);
  simulating.value = true;
  error.value = "";
  let forecastSucceeded = false;
  try {
    result.value = await simulateDraft({
      league_id: leagueId.value,
      model_type: modelType,
      blue_team_id: String(blue.team_id),
      blue_team_name: blue.team_name,
      red_team_id: String(red.team_id),
      red_team_name: red.team_name,
      bp_order: bpOrder.value,
      ...board.value,
      blue_used_previous_battles: globalUsed.value[teamsBySide.value.blue],
      red_used_previous_battles: globalUsed.value[teamsBySide.value.red],
    });
    forecastSucceeded = true;
  } catch (err) {
    result.value = null;
    error.value = err.message || "Could not simulate this draft state.";
  } finally {
    simulating.value = false;
  }
  if (forecastSucceeded) await recommendCurrentDraft();
}

async function scoreCompletedLineup() {
  if (!teamsReady.value || !lineupComplete.value) return;
  const requestNumber = ++lineupScoreRequestNumber;
  const blue = selectedTeam(teamsBySide.value.blue);
  const red = selectedTeam(teamsBySide.value.red);
  lineupScoreLoading.value = true;
  lineupScoreError.value = "";
  try {
    const score = await scoreLineup({
      league_id: leagueId.value,
      blue_team_id: String(blue.team_id),
      red_team_id: String(red.team_id),
      blue_hero_ids: [...board.value.blue_picks],
      red_hero_ids: [...board.value.red_picks],
    });
    if (requestNumber === lineupScoreRequestNumber) lineupScore.value = score;
  } catch (err) {
    if (requestNumber === lineupScoreRequestNumber) {
      lineupScore.value = null;
      lineupScoreError.value = err.message || "无法评分当前完整阵容。";
    }
  } finally {
    if (requestNumber === lineupScoreRequestNumber) lineupScoreLoading.value = false;
  }
}

async function recommendCurrentDraft() {
  if (!teamsReady.value || !model.value || !currentStep.value) return;
  const requestNumber = ++recommendationRequestNumber;
  expandedRecommendationIds.value = [];
  const blue = selectedTeam(teamsBySide.value.blue);
  const red = selectedTeam(teamsBySide.value.red);
  recommendationLoading.value = true;
  recommendationError.value = "";
  try {
    const recommendations = await recommendLineup({
      league_id: leagueId.value,
      model_type: modelType,
      blue_team_id: String(blue.team_id),
      blue_team_name: blue.team_name,
      red_team_id: String(red.team_id),
      red_team_name: red.team_name,
      bp_order: bpOrder.value,
      ...board.value,
      blue_used_previous_battles: globalUsed.value[teamsBySide.value.blue],
      red_used_previous_battles: globalUsed.value[teamsBySide.value.red],
      top_k: 3,
      risk_mode: "balanced",
    });
    if (requestNumber === recommendationRequestNumber) {
      recommendationResult.value = recommendations;
    }
  } catch (err) {
    if (requestNumber === recommendationRequestNumber) {
      recommendationResult.value = null;
      recommendationError.value = err.message || "无法生成阵容建议。";
    }
  } finally {
    if (requestNumber === recommendationRequestNumber) {
      recommendationLoading.value = false;
    }
  }
}

async function chooseHero(heroId) {
  if (!teamsReady.value || isPeakDuel.value || liveHeroSelectionLocked.value) return;
  if (pickerTarget.value !== "draft") {
    if (liveOfficialHeroContextLocked.value) return;
    const side = pickerTarget.value.replace("global-", "");
    const team = teamsBySide.value[side];
    if (globalUsed.value[team].includes(Number(heroId))) return;
    globalUsed.value[team].push(Number(heroId));
    search.value = "";
    await forecast();
    return;
  }
  if (!currentStep.value || usedHeroIds.value.has(Number(heroId))) return;
  const preSelectionState = coachDraftState.value;
  if (commentaryEnabled.value && preSelectionState) {
    const requestNumber = ++commentaryRequestNumber;
    commentaryLoading.value = true;
    fetchSelectionCommentary({
      league_id: leagueId.value,
      ...preSelectionState,
      action: currentStep.value.action,
      side: currentStep.value.side,
      selected_hero_id: Number(heroId),
    }).then((payload) => {
      if (commentaryEnabled.value && requestNumber === commentaryRequestNumber) {
        commentary.value = payload;
      }
    }).catch(() => {
      if (requestNumber === commentaryRequestNumber) commentary.value = null;
    }).finally(() => {
      if (requestNumber === commentaryRequestNumber) commentaryLoading.value = false;
    });
  }
  const field = `${currentStep.value.side}_${
    currentStep.value.action === "pick" ? "picks" : "bans"
  }`;
  board.value[field].push(Number(heroId));
  history.value.push({ field, heroId: Number(heroId), bpOrder: bpOrder.value });
  bpOrder.value += 1;
  search.value = "";
  await forecast();
}

watch(commentaryEnabled, (enabled) => {
  if (enabled) return;
  commentaryRequestNumber += 1;
  commentaryLoading.value = false;
  commentary.value = null;
});

async function undo() {
  if (isPeakDuel.value) return;
  const event = history.value.pop();
  if (!event) return;
  const index = board.value[event.field].lastIndexOf(event.heroId);
  if (index >= 0) board.value[event.field].splice(index, 1);
  bpOrder.value = event.bpOrder;
  await forecast();
}

async function reset() {
  if (isPeakDuel.value) return;
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  search.value = "";
  commentary.value = null;
  await forecast();
}

async function startGlobalBp() {
  if (!teamsReady.value) return;
  globalMode.value = "match";
  seriesGame.value = 1;
  resetSeriesTeams();
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "draft";
  await forecast();
}

async function customizeGlobalBp() {
  if (!teamsReady.value) return;
  globalMode.value = "custom";
  seriesGame.value = 2;
  resetSeriesTeams();
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  pickerTarget.value = "global-blue";
  await forecast();
}

async function clearGlobalBp() {
  globalMode.value = "single";
  seriesGame.value = 1;
  resetSeriesTeams();
  pickerTarget.value = "draft";
  await forecast();
}

async function startNextBattle() {
  if (
    currentStep.value ||
    seriesWinner.value ||
    !winnerSide.value ||
    !nextBlueTeam.value
  ) return;
  for (const side of ["blue", "red"]) {
    const team = teamsBySide.value[side];
    globalUsed.value[team] = [
      ...new Set([...globalUsed.value[team], ...board.value[`${side}_picks`]]),
    ];
  }
  const nextRedTeam = nextBlueTeam.value === TEAM_A ? TEAM_B : TEAM_A;
  teamsBySide.value = { blue: nextBlueTeam.value, red: nextRedTeam };
  board.value = emptyBoard();
  history.value = [];
  bpOrder.value = 1;
  seriesGame.value += 1;
  winnerSide.value = null;
  nextBlueTeam.value = null;
  await forecast();
}

function recordGameWinner(side) {
  if (winnerSide.value === side) return;
  if (winnerSide.value) {
    const previousWinner = teamsBySide.value[winnerSide.value];
    seriesWins.value[previousWinner] -= 1;
  }
  const winner = teamsBySide.value[side];
  seriesWins.value[winner] += 1;
  winnerSide.value = side;
  nextBlueTeam.value = null;
}

async function removeGlobalHero(side, heroId) {
  if (liveHeroSelectionLocked.value || liveOfficialHeroContextLocked.value) return;
  const team = teamsBySide.value[side];
  globalUsed.value[team] = globalUsed.value[team].filter((id) => id !== heroId);
  await forecast();
}

function removeHero(field, heroId) {
  if (isPeakDuel.value || liveHeroSelectionLocked.value) return;
  const eventIndex = history.value.findLastIndex(
    (event) => event.field === field && event.heroId === heroId
  );
  if (eventIndex === history.value.length - 1) undo();
}

const mobileBoardResistance = {
  boardTop: null,
  startScrollY: 0,
  startTouchY: 0,
  wheelRemaining: 0,
  wheelTime: 0,
};

function startMobileBoardSwipe(event) {
  if (
    event.touches.length !== 1 ||
    !window.matchMedia("(max-width: 620px)").matches ||
    !draftBoardElement.value
  ) return;

  const boardRect = draftBoardElement.value.getBoundingClientRect();
  const startedOnBoard = draftBoardElement.value.contains(event.target);
  mobileBoardResistance.startTouchY = event.touches[0].clientY;
  mobileBoardResistance.startScrollY = window.scrollY;
  mobileBoardResistance.boardTop =
    boardRect.top > 1
      ? window.scrollY + boardRect.top
      : startedOnBoard
        ? window.scrollY
        : null;
}

function resistMobileBoardSwipe(event) {
  if (mobileBoardResistance.boardTop === null || event.touches.length !== 1) return;

  const swipeDistance =
    mobileBoardResistance.startTouchY - event.touches[0].clientY;
  const intendedScrollY =
    mobileBoardResistance.startScrollY + swipeDistance;
  const distancePastBoard =
    intendedScrollY - mobileBoardResistance.boardTop;

  if (swipeDistance <= 0 || distancePastBoard <= 0) return;

  const resistanceDistance = 96;
  const resistanceRatio = 0.18;
  const resistedDistance =
    distancePastBoard <= resistanceDistance
      ? distancePastBoard * resistanceRatio
      : distancePastBoard -
        resistanceDistance +
        resistanceDistance * resistanceRatio;

  if (event.cancelable) event.preventDefault();
  window.scrollTo(0, mobileBoardResistance.boardTop + resistedDistance);
}

function endMobileBoardSwipe() {
  mobileBoardResistance.boardTop = null;
}

function resistMobileBoardWheel(event) {
  if (
    event.deltaY <= 0 ||
    !window.matchMedia("(max-width: 620px)").matches
  ) return;

  const now = performance.now();
  if (now - mobileBoardResistance.wheelTime > 180) {
    mobileBoardResistance.wheelRemaining = 96;
  }
  mobileBoardResistance.wheelTime = now;

  const resistedAmount = Math.min(
    event.deltaY,
    mobileBoardResistance.wheelRemaining
  );
  if (resistedAmount <= 0) return;

  mobileBoardResistance.wheelRemaining -= resistedAmount;
  if (event.cancelable) event.preventDefault();
  window.scrollBy(0, event.deltaY - resistedAmount + resistedAmount * 0.18);
}

onMounted(async () => {
  try {
    await loadSeasons();
    await loadModel();
  } catch (err) {
    error.value = err.message || "Could not load the draft simulator.";
  } finally {
    finishStartupLoading();
  }
});

watch(leagueId, loadModel);
watch(
  selectedTeamIds,
  async () => {
    stopFollowingLiveMatch({ forget: false });
    stopLiveMatchPolling();
    liveMatch.value = null;
    liveFollowDismissed.value = false;
    liveAppliedGameSignature.value = "";
    liveScheduleClock.value = Date.now();
    await forecast();
    if (shouldRestoreScheduledFollow()) {
      liveFollowing.value = true;
      liveFollowDismissed.value = true;
    }
    scheduleLiveScheduleClock();
    scheduleLiveMatchCheck();
  },
  { deep: true }
);

onBeforeUnmount(() => {
  stopLiveMatchPolling();
  stopLiveMatchCheckSchedule();
  stopLiveScheduleClock();
});
</script>

<template>
  <main
    class="simulator-page"
    @touchstart="startMobileBoardSwipe"
    @touchmove="resistMobileBoardSwipe"
    @touchend="endMobileBoardSwipe"
    @touchcancel="endMobileBoardSwipe"
  >
    <header class="simulator-hero">
      <div>
        <p class="simulator-eyebrow">交互式模型</p>
        <h1>BP 选禁模拟器</h1>
        <p>
          逐步构建蓝方与红方的 BP 过程。每次选择或禁用后，模型都会更新预测。
        </p>
      </div>
      <div class="simulator-header-controls">
        <label class="simulator-season">
          <span>赛事</span>
          <select v-model="leagueId" :disabled="loading">
            <option v-for="season in seasons" :key="season.league_id" :value="season.league_id">
              {{ season.year }} · {{ season.league_name }} · S{{ season.season }}
            </option>
          </select>
          <small v-if="model">{{ number(model.training_decisions) }} 条历史 BP 操作</small>
        </label>
        <div class="simulator-settings">
          <button
            type="button"
            class="settings-trigger"
            :aria-expanded="settingsOpen"
            aria-controls="simulator-settings-menu"
            @click="settingsOpen = !settingsOpen"
          >
            <span aria-hidden="true">⚙</span> 设置
          </button>
          <div v-if="settingsOpen" id="simulator-settings-menu" class="settings-menu">
            <p>比赛设置</p>
            <button type="button" :class="{ active: globalMode === 'single' }" :disabled="!teamsReady || liveFollowing" @click="setMatchMode('single')">
              <strong>单局</strong><small>重置为单局 BP</small>
            </button>
            <button type="button" :class="{ active: globalMode === 'match' }" :disabled="!teamsReady || liveFollowing" @click="setMatchMode('match')">
              <strong>完整系列赛</strong><small>跟踪系列赛中的已使用英雄</small>
            </button>
            <button type="button" :class="{ active: globalMode === 'custom' }" :disabled="!teamsReady || liveFollowing" @click="setMatchMode('custom')">
              <strong>完整系列赛 · 自定义 BP</strong><small>录入此前小局的英雄使用情况</small>
            </button>
            <label class="settings-series">
              <span>系列赛制</span>
              <select v-model.number="bestOf" :disabled="globalMode === 'single' || liveFollowing">
                <option :value="5">BO5</option>
                <option :value="7">BO7</option>
              </select>
            </label>
            <label class="settings-commentary">
              <input v-model="commentaryEnabled" type="checkbox" />
              <span><strong>AI 解说</strong><small>默认关闭 · 每次选择后调用 Kimi</small></span>
            </label>
          </div>
        </div>
      </div>
    </header>

    <p v-if="error" class="simulator-message error">{{ error }}</p>
    <p v-else-if="loading" class="simulator-message">正在加载 BP 模型…</p>

    <template v-else-if="model">
      <section class="global-bp-panel">
        <div>
          <p class="simulator-eyebrow">比赛赛制</p>
          <h2>全局 BP</h2>
          <p>
            {{ bpT("Earlier-game picks follow the team, even when it changes between Blue and Red. After each game, record the winner, then let the losing team choose its next color.") }}
          </p>
        </div>
        <div class="global-actions">
          <div class="global-team-row">
            <div class="team-side-control blue">
              <TeamCombobox
                :model-value="selectedTeamIds[TEAM_A]"
                :label="firstTeamLabel"
                :teams="seasonTeams"
                :excluded-id="selectedTeamIds[TEAM_B]"
                :opponent-team="selectedTeam(TEAM_B)"
                :disabled="loading || history.length > 0 || seriesGame > 1 || liveFollowing"
                @update:model-value="selectTeamForSide(TEAM_A, $event)"
              />
            </div>
            <div class="team-side-control red">
              <TeamCombobox
                :model-value="selectedTeamIds[TEAM_B]"
                :label="secondTeamLabel"
                :teams="seasonTeams"
                :excluded-id="selectedTeamIds[TEAM_A]"
                :opponent-team="selectedTeam(TEAM_A)"
                :disabled="loading || history.length > 0 || seriesGame > 1 || liveFollowing"
                @update:model-value="selectTeamForSide(TEAM_B, $event)"
              />
            </div>
          </div>
        </div>
        <p v-if="upcomingMatchLabel" class="upcoming-match-note" data-i18n-ignore>
          {{ upcomingMatchLabel }}
        </p>
        <aside v-if="scheduledMatchStarted && !liveFollowing && !liveFollowDismissed" class="live-match-panel">
          <div>
            <p class="simulator-eyebrow">赛程已到开始时间</p>
            <strong>是否跟随本场比赛？</strong>
            <small>跟随后会在开赛五分钟后开始同步官方赛况，并将已结束小局的英雄加入“已使用”。</small>
          </div>
          <div>
            <button type="button" :disabled="liveMatchLoading" @click="followLiveMatch">跟随当前比赛</button>
            <button type="button" class="quiet" @click="liveFollowDismissed = true">暂不跟随</button>
          </div>
        </aside>
        <aside v-else-if="liveFollowing" class="live-match-panel active">
          <div>
            <p class="simulator-eyebrow">正在跟随官方比赛</p>
            <strong data-i18n-ignore>{{ liveMatchStatusLabel }}</strong>
            <small v-if="!liveApiCheckAvailable">已启用跟随；开赛五分钟后将开始同步官方赛况。</small>
            <small v-else-if="liveMatch?.current_game_status === 'in_progress'">官方对局进行时仍可继续本地 BP。对局结束后，官方选择会自动替换“已使用”上下文。</small>
            <small v-else>官方数据最多每三分钟刷新一次。</small>
            <small v-if="liveRefreshNotice" class="live-refresh-note" data-i18n-ignore>{{ liveRefreshNotice }}</small>
          </div>
          <div>
            <button type="button" class="quiet" :disabled="liveMatchLoading || !liveApiCheckAvailable" @click="refreshLiveMatch(true)">
              {{ !liveApiCheckAvailable ? '开赛五分钟后可刷新' : liveMatchLoading ? '正在检查…' : '刷新官方数据' }}
            </button>
            <button type="button" class="quiet" @click="stopFollowingLiveMatch">停止跟随</button>
          </div>
        </aside>
        <aside v-if="liveFollowing && livePredictionGame" class="live-winner-prediction" aria-live="polite">
          <div>
            <p class="simulator-eyebrow">观众胜负预测</p>
            <strong>你认为第 {{ livePredictionGame }} 局谁会赢？</strong>
            <small v-if="liveWinnerPredictionLoading">正在加载大家的预测…</small>
            <small v-else-if="selectedWinnerPrediction">你已提交预测；已有 {{ livePredictionTotal }} 人参与。</small>
            <small v-else>已有 {{ livePredictionTotal }} 人参与；提交后无法修改。</small>
          </div>
          <div class="live-winner-choices">
            <button
              v-for="team in [selectedTeam(TEAM_A), selectedTeam(TEAM_B)]"
              :key="team.team_id"
              type="button"
              :class="{ active: selectedWinnerPrediction === String(team.team_id) }"
              :disabled="liveWinnerPredictionSaving || Boolean(selectedWinnerPrediction)"
              @click="chooseLiveWinnerPrediction(team.team_id)"
            >
              {{ team.team_name }} 胜
              <small>{{ livePredictionVotes[String(team.team_id)] || 0 }} 票</small>
            </button>
          </div>
        </aside>
        <p v-if="!teamsReady" class="team-required">
          搜索并选择本赛季两支战队，以开始模拟并为教练提供蓝红方上下文。
        </p>
        <div v-if="globalMode !== 'single'" class="global-used">
          <div class="mobile-used-hero-buttons">
            <button type="button" class="blue" @click="usedHeroesModalSide = 'blue'">
              <span>{{ sideLabel('blue') }}</span>
              <small>{{ globalUsed[teamsBySide.blue].length }} {{ bpT('used earlier') }}</small>
            </button>
            <button type="button" class="red" @click="usedHeroesModalSide = 'red'">
              <span>{{ sideLabel('red') }}</span>
              <small>{{ globalUsed[teamsBySide.red].length }} {{ bpT('used earlier') }}</small>
            </button>
          </div>
          <div v-for="side in ['blue', 'red']" :key="side" class="used-team" :class="side">
            <span data-i18n-ignore>{{ sideUsedLabel(side) }}</span>
            <button
              v-for="heroId in globalUsed[teamsBySide[side]]"
              :key="`${side}-${heroId}`"
              type="button"
              :title="`移除 ${heroName(heroId)}`"
              :disabled="liveHeroSelectionLocked || liveOfficialHeroContextLocked"
              @click="removeGlobalHero(side, heroId)"
            >
              <img :src="heroIcon(heroId)" :alt="heroName(heroId)" />
            </button>
            <small v-if="!globalUsed[teamsBySide[side]].length">暂无已选英雄</small>
          </div>
          <div v-if="globalMode === 'match'" class="next-battle series-progress">
            <small>BO{{ bestOf }} · {{ teamName(TEAM_A) }} {{ seriesWins[TEAM_A] }}–{{ seriesWins[TEAM_B] }} {{ teamName(TEAM_B) }} · 第 {{ seriesGame }} 局</small>
            <template v-if="seriesWinner">
              <strong data-i18n-ignore>{{ seriesWinnerLabel() }}</strong>
            </template>
            <template v-else-if="currentStep && !isPeakDuel">
              <strong>完成当前 BP 后继续</strong>
            </template>
            <template v-else>
              <span data-i18n-ignore>{{ gameWinnerLabel(seriesGame) }}</span>
              <div class="series-choice">
                <button type="button" :class="{ active: winnerSide === 'blue' }" @click="recordGameWinner('blue')">{{ bpT("Blue wins") }}</button>
                <button type="button" :class="{ active: winnerSide === 'red' }" @click="recordGameWinner('red')">{{ bpT("Red wins") }}</button>
              </div>
              <template v-if="losingTeam">
                <span data-i18n-ignore>{{ loserColorChoiceLabel(losingTeam) }}</span>
                <div class="series-choice">
                  <button type="button" :class="{ active: nextBlueTeam === losingTeam }" @click="nextBlueTeam = losingTeam">{{ bpT("Play Blue") }}</button>
                  <button type="button" :class="{ active: nextBlueTeam !== null && nextBlueTeam !== losingTeam }" @click="nextBlueTeam = losingTeam === TEAM_A ? TEAM_B : TEAM_A">{{ bpT("Play Red") }}</button>
                </div>
              </template>
              <button type="button" :disabled="!winnerSide || !nextBlueTeam" @click="startNextBattle" data-i18n-ignore>{{ startGameLabel(seriesGame + 1) }}</button>
            </template>
          </div>
          <small v-else data-i18n-ignore>{{ seriesStatusLabel() }}</small>
        </div>
        <button
          v-if="usedHeroesModalSide"
          class="mobile-used-scrim"
          type="button"
          aria-label="关闭已使用英雄"
          @click="usedHeroesModalSide = null"
        ></button>
        <aside v-if="usedHeroesModalSide" class="mobile-used-hero-modal" role="dialog" aria-modal="true">
          <header>
            <div>
              <p class="simulator-eyebrow">已使用</p>
              <h2 data-i18n-ignore>{{ sideUsedLabel(usedHeroesModalSide) }}</h2>
            </div>
            <button type="button" aria-label="关闭已使用英雄" @click="usedHeroesModalSide = null">×</button>
          </header>
          <div class="mobile-used-hero-list">
            <button
              v-for="heroId in globalUsed[teamsBySide[usedHeroesModalSide]]"
              :key="`modal-${usedHeroesModalSide}-${heroId}`"
              type="button"
              :title="`移除 ${heroName(heroId)}`"
              :disabled="liveHeroSelectionLocked || liveOfficialHeroContextLocked"
              @click="removeGlobalHero(usedHeroesModalSide, heroId)"
            >
              <img :src="heroIcon(heroId)" :alt="heroName(heroId)" />
              <span>{{ heroName(heroId) }}</span>
            </button>
            <p v-if="!globalUsed[teamsBySide[usedHeroesModalSide]].length">暂无已选英雄</p>
          </div>
        </aside>
      </section>

      <section class="simulator-status">
        <div>
          <span>下一步操作</span>
          <strong data-i18n-ignore>{{ currentLabel }}</strong>
          <small>{{ selectedSeason?.league_name || leagueId }}</small>
        </div>
        <div class="side-assignment" aria-label="当前 BP 边位">
          <label class="blue">
            <span>蓝方</span>
            <select
              :value="teamsBySide.blue"
              :disabled="!canChangeCurrentSides"
              @change="setTeamForDraftSide('blue', $event.target.value)"
            >
              <option :value="TEAM_A">{{ teamName(TEAM_A) }}</option>
              <option :value="TEAM_B">{{ teamName(TEAM_B) }}</option>
            </select>
          </label>
          <button
            type="button"
            class="swap-sides"
            :disabled="!canChangeCurrentSides"
            aria-label="交换蓝红方"
            title="交换蓝红方"
            @click="swapDraftSides"
          >
            ⇄
          </button>
          <label class="red">
            <span>红方</span>
            <select
              :value="teamsBySide.red"
              :disabled="!canChangeCurrentSides"
              @change="setTeamForDraftSide('red', $event.target.value)"
            >
              <option :value="TEAM_A">{{ teamName(TEAM_A) }}</option>
              <option :value="TEAM_B">{{ teamName(TEAM_B) }}</option>
            </select>
          </label>
        </div>
        <div class="simulator-actions">
          <button type="button" :disabled="isPeakDuel || !history.length || simulating || liveHeroSelectionLocked" @click="undo">撤销</button>
          <button type="button" :disabled="isPeakDuel || simulating || liveHeroSelectionLocked" @click="reset">重置</button>
        </div>
      </section>

      <div class="simulator-workspace">
        <div class="simulator-main-column">
          <section class="simulator-layout">
            <section v-if="isPeakDuel" class="peak-duel-board" aria-disabled="true">
              <p class="simulator-eyebrow">BO7 · 第 7 局</p>
              <h2>巅峰对决，不用BP</h2>
            </section>
            <div
              v-else
              ref="draftBoardElement"
              class="draft-board"
              @wheel="resistMobileBoardWheel"
            >
              <section
                v-for="group in boardGroups"
                :key="group.key"
                class="draft-group"
                :class="[group.tone, group.key]"
              >
                <p data-i18n-ignore>
                  <span class="desktop-group-title">{{ group.title }}</span>
                  <span class="mobile-group-title">{{ group.mobileTitle }}</span>
                </p>
                <div class="draft-slots">
                  <button
                    v-for="heroId in board[group.key]"
                    :key="`${group.key}-${heroId}`"
                    type="button"
                    :title="history.at(-1)?.heroId === heroId ? '移除最后一步操作' : ''"
                    :disabled="history.at(-1)?.heroId !== heroId || liveHeroSelectionLocked"
                    @click="removeHero(group.key, heroId)"
                  >
                    <img v-if="heroIcon(heroId)" :src="heroIcon(heroId)" :alt="heroName(heroId)" />
                    <span v-else>{{ heroName(heroId).slice(0, 1) }}</span>
                  </button>
                  <span v-for="slot in Math.max(0, 5 - board[group.key].length)" :key="slot">—</span>
                </div>
              </section>
            </div>

            <aside v-if="!isPeakDuel" class="forecast-panel">
              <div class="forecast-heading">
                <div>
                  <p class="simulator-eyebrow">模型预测</p>
                  <h2 data-i18n-ignore>{{ forecastLabel() }}</h2>
                </div>
                <span v-if="simulating">正在更新…</span>
              </div>
              <div class="probability-list">
                <div v-for="row in result?.next_action_probabilities?.slice(0, 10)" :key="row.hero_id">
                  <img :src="heroIcon(row.hero_id)" :alt="row.hero_name" />
                  <span class="probability-track"><i :style="{ width: percent(row.probability) }"></i></span>
                  <em>{{ percent(row.probability) }}</em>
                </div>
              </div>
              <div v-if="result?.simulation?.banned_by_end?.length" class="end-ban-list">
                <p>最可能在 BP 结束前被禁用</p>
                <span v-for="row in result.simulation.banned_by_end.slice(0, 3)" :key="row.hero_id">
                  <img :src="heroIcon(row.hero_id)" :alt="row.hero_name" />
                  {{ percent(row.probability) }}
                </span>
              </div>
            </aside>
          </section>

          <section v-if="lineupComplete || lineupScoreLoading || lineupScoreError" class="lineup-score-panel">
            <header>
              <div>
                <p class="simulator-eyebrow">完整阵容评分</p>
                <h2>5v5 阵容对比</h2>
                <p>使用当前赛季阵容价值模型直接比较双方最终五人阵容。</p>
              </div>
              <span class="recommendation-status" aria-live="polite">
                {{ lineupScoreLoading ? '正在评分…' : lineupScore ? '评分已更新' : '等待完整阵容' }}
              </span>
            </header>
            <p v-if="lineupScoreError" class="recommendation-error">{{ lineupScoreError }}</p>
            <template v-if="lineupScore">
              <div class="lineup-score-teams">
                <article class="blue">
                  <small>{{ lineupScore.blue_team.team_name }}</small>
                  <strong>{{ percent(lineupScore.blue_advantage) }}</strong>
                  <span>蓝方相对阵容优势</span>
                </article>
                <article class="red">
                  <small>{{ lineupScore.red_team.team_name }}</small>
                  <strong>{{ percent(lineupScore.red_advantage) }}</strong>
                  <span>红方相对阵容优势</span>
                </article>
              </div>
              <div class="lineup-score-breakdown">
                <div>
                  <h3>蓝方视角贡献</h3>
                  <dl>
                    <div v-for="(value, component) in lineupScore.grouped_contributions" :key="component">
                      <dt>{{ lineupComponentLabel(component) }}</dt>
                      <dd :class="{ positive: Number(value) > 0, negative: Number(value) < 0 }">{{ signedContribution(value) }}</dd>
                    </div>
                  </dl>
                </div>
                <div>
                  <h3>阵容结构</h3>
                  <dl>
                    <div><dt>蓝方前排 / 硬控</dt><dd>{{ lineupScore.blue_composition.frontline_count }} / {{ lineupScore.blue_composition.hard_cc_count }}</dd></div>
                    <div><dt>红方前排 / 硬控</dt><dd>{{ lineupScore.red_composition.frontline_count }} / {{ lineupScore.red_composition.hard_cc_count }}</dd></div>
                    <div><dt>蓝方开团点</dt><dd>{{ lineupScore.blue_composition.primary_engage_count }}</dd></div>
                    <div><dt>红方开团点</dt><dd>{{ lineupScore.red_composition.primary_engage_count }}</dd></div>
                  </dl>
                </div>
              </div>
              <small class="recommendation-warning">这是相对阵容排序分，不是比赛胜率；正贡献偏向蓝方，负贡献偏向红方。</small>
            </template>
          </section>

          <section v-if="!isPeakDuel" class="recommendation-panel">
            <header>
              <div>
                <p class="simulator-eyebrow">BP 决策</p>
                <h2>推荐下一手</h2>
                <p>每次 BP 更新后，模型会自动尝试候选英雄、模拟后续选禁，并比较最终阵容的相对优势。</p>
              </div>
              <span class="recommendation-status" aria-live="polite">
                {{ !currentStep ? '当前 BP 已完成' : recommendationLoading ? '正在自动搜索后续 BP…' : recommendationResult ? '已自动更新' : '等待 BP 状态' }}
              </span>
            </header>
            <p v-if="recommendationError" class="recommendation-error">{{ recommendationError }}</p>
            <div v-if="recommendationResult?.recommendations?.length" class="recommendation-list">
              <article v-for="row in recommendationResult.recommendations" :key="row.hero_id">
                <button
                  type="button"
                  class="recommendation-choice"
                  :disabled="liveHeroSelectionLocked"
                  :title="`采用 ${row.hero_name}`"
                  @click="chooseHero(row.hero_id)"
                >
                  <span class="recommendation-rank">#{{ row.rank }}</span>
                  <img :src="heroIcon(row.hero_id)" :alt="row.hero_name" />
                  <span>
                    <strong>{{ row.hero_name }}</strong>
                    <small>点击采用这一手</small>
                  </span>
                </button>
                <button
                  type="button"
                  class="recommendation-detail-toggle"
                  :aria-expanded="recommendationDetailsExpanded(row.hero_id)"
                  @click="toggleRecommendationDetails(row.hero_id)"
                >
                  {{ recommendationDetailsExpanded(row.hero_id) ? '收起评分详情' : '查看评分详情' }}
                  <span aria-hidden="true">{{ recommendationDetailsExpanded(row.hero_id) ? '−' : '+' }}</span>
                </button>
                <div
                  class="recommendation-details"
                  :class="{ expanded: recommendationDetailsExpanded(row.hero_id) }"
                >
                <dl>
                  <div v-for="metric in ['score', 'delta', 'confidence', 'policy']" :key="metric">
                    <dt>
                      {{ recommendationMetricLabel(metric, row.action) }}
                      <button
                        type="button"
                        class="metric-help"
                        :aria-label="`${recommendationMetricLabel(metric, row.action)}说明：${recommendationMetricHelp(metric, row.action)}`"
                        :data-help="recommendationMetricHelp(metric, row.action)"
                      >?</button>
                    </dt>
                    <dd v-if="metric === 'score'">{{ percent(row.expected_advantage) }}</dd>
                    <dd v-else-if="metric === 'delta'">{{ signedPoints(row.advantage_delta_vs_policy_baseline) }}</dd>
                    <dd v-else-if="metric === 'confidence'">{{ confidenceLabel(row.confidence) }}</dd>
                    <dd v-else>{{ percent(row.policy_probability) }}</dd>
                  </div>
                </dl>
                <div class="recommendation-reasons">
                  <span v-for="reason in row.explanations.slice(0, 4)" :key="`${row.hero_id}-${reason.code}`">
                    {{ recommendationReasonLabel(reason) }}
                  </span>
                </div>
                <p v-if="row.likely_opponent_responses?.length">
                  可能应对：{{ row.likely_opponent_responses.map((response) => response.hero_name).join('、') }}
                </p>
                </div>
              </article>
            </div>
            <small v-if="recommendationResult" class="recommendation-warning">
              {{ recommendationResult.recommender === 'ban_value' ? '禁用价值分综合对手英雄偏好、克制关系、历史禁用结果与 BP 真实性，不是因果胜率。' : '阵容优势分用于候选排序，不是承诺胜率；全局 BP 已使用英雄会在每次模拟中排除。' }}
            </small>
          </section>

          <section v-if="!isPeakDuel && (commentary || commentaryLoading)" class="commentary-panel">
            <p class="simulator-eyebrow">BP 解说</p>
            <p v-if="commentaryLoading" class="commentary-loading">正在生成解说…</p>
            <h2 v-else>{{ commentary.commentary }}</h2>
          </section>

          <section v-if="!isPeakDuel" class="hero-picker">
            <div class="picker-heading">
              <div>
                <p class="simulator-eyebrow">{{ pickerTarget === 'draft' ? '添加下一步操作' : '全局 BP 设置' }}</p>
                <h2 data-i18n-ignore>{{ pickerTitle }}</h2>
              </div>
              <div class="picker-controls">
                <label v-if="pickerTarget === 'draft'" class="hero-lane-filter">
                  <span>英雄位置</span>
                  <select
                    v-model="laneFilter"
                    :disabled="!teamsReady || (pickerTarget === 'draft' && !currentStep) || liveHeroSelectionLocked"
                  >
                    <option v-for="lane in laneOptions" :key="lane.value" :value="lane.value">
                      {{ lane.label }}概率
                    </option>
                  </select>
                </label>
                <input v-model="search" type="search" placeholder="搜索英雄…" :disabled="!teamsReady || (pickerTarget === 'draft' && !currentStep) || liveHeroSelectionLocked" />
              </div>
            </div>
            <div v-if="globalMode !== 'single'" class="picker-targets">
              <button type="button" :class="{ active: pickerTarget === 'draft' }" :disabled="liveHeroSelectionLocked" @click="pickerTarget = 'draft'">当前 BP</button>
              <button type="button" :class="{ active: pickerTarget === 'global-blue' }" :disabled="liveHeroSelectionLocked || liveOfficialHeroContextLocked" @click="pickerTarget = 'global-blue'" data-i18n-ignore>{{ earlierGamesLabel(teamsBySide.blue) }}</button>
              <button type="button" :class="{ active: pickerTarget === 'global-red' }" :disabled="liveHeroSelectionLocked || liveOfficialHeroContextLocked" @click="pickerTarget = 'global-red'" data-i18n-ignore>{{ earlierGamesLabel(teamsBySide.red) }}</button>
            </div>
            <div class="hero-options">
              <button
                v-for="hero in availableHeroes"
                :key="hero.hero_id"
                type="button"
                :disabled="!teamsReady || (pickerTarget === 'draft' && !currentStep) || simulating || liveHeroSelectionLocked"
                :title="`${hero.hero_name} · ${percent(probabilityByHeroId.get(Number(hero.hero_id)) || 0)}`"
                @click="chooseHero(hero.hero_id)"
              >
                <img v-if="heroIcon(hero.hero_id)" :src="heroIcon(hero.hero_id)" :alt="hero.hero_name" />
                <span v-else>{{ hero.hero_name.slice(0, 1) }}</span>
                <small>{{ percent(probabilityByHeroId.get(Number(hero.hero_id)) || 0) }}</small>
              </button>
            </div>
          </section>
        </div>

        <button
          v-if="coachOpen"
          class="coach-scrim"
          type="button"
          aria-label="关闭 BP 教练"
          @click="coachOpen = false"
        ></button>
        <aside class="coach-rail" :class="{ 'coach-open': coachOpen }" aria-label="BP 教练对话">
          <button class="mobile-coach-close" type="button" aria-label="关闭 BP 教练" @click="coachOpen = false">×</button>
          <DraftCoachPanel
            :league-id="leagueId"
            :season-name="selectedSeason?.league_name || leagueId"
            :draft-state="coachDraftState"
            :force-chinese="true"
          />
        </aside>
        <button
          class="mobile-coach-toggle"
          type="button"
          aria-label="打开 BP 教练"
          @click="coachOpen = true"
        >
          <span aria-hidden="true">✦</span>
          <strong>AI</strong>
        </button>
      </div>
    </template>
  </main>
</template>

<style scoped>
.simulator-page { width: min(1560px, calc(100% - 2rem)); margin: 0 auto; padding: 2.25rem 0 5rem; }
.simulator-hero, .simulator-status, .simulator-layout { display: flex; gap: 1.5rem; justify-content: space-between; }
.simulator-hero { align-items: flex-end; }
.simulator-eyebrow { margin: 0 0 .45rem; color: var(--accent-deep); font-size: .66rem; letter-spacing: .13em; text-transform: uppercase; }
.simulator-hero h1, .picker-heading h2, .forecast-heading h2 { margin: 0; font-family: var(--display); letter-spacing: -.045em; }
.simulator-hero h1 { font-size: clamp(2.4rem, 5vw, 4rem); line-height: .95; }
.simulator-hero > div > p:last-child { max-width: 40rem; margin: .8rem 0 0; color: var(--ink-soft); font-size: .8rem; }
.simulator-header-controls { display:flex; align-items:end; justify-content:end; gap:.55rem; }
.simulator-season { display: grid; min-width: 310px; gap: .4rem; }
.simulator-season span, .simulator-actions label span { color: var(--ink-soft); font-size: .64rem; letter-spacing: .1em; text-transform: uppercase; }
.simulator-season select, .simulator-actions select, .picker-heading input { min-height: 42px; padding: .55rem .7rem; border: 1px solid var(--line); background: rgba(255,255,255,.85); color: var(--ink); font: inherit; }
.simulator-season small { color: var(--ink-soft); font-size: .66rem; }
.simulator-settings { position:relative; }
.settings-trigger { display:inline-flex; min-height:42px; align-items:center; gap:.35rem; padding:.55rem .75rem; border:1px solid var(--line); background:rgba(255,255,255,.85); color:var(--ink); font:700 .68rem var(--mono); cursor:pointer; }
.settings-trigger:hover { border-color:var(--accent-deep); }
.settings-menu { position:absolute; z-index:50; top:calc(100% + .45rem); right:0; display:grid; width:min(19rem, calc(100vw - 2rem)); gap:.35rem; padding:.75rem; border:1px solid var(--line); background:#fff; box-shadow:0 .9rem 2.2rem rgba(16,42,46,.2); }
.settings-menu > p { margin:0 0 .1rem; color:var(--ink-soft); font-size:.58rem; letter-spacing:.1em; text-transform:uppercase; }
.settings-menu > button { display:grid; gap:.08rem; padding:.5rem .6rem; border:1px solid var(--line); background:#fff; color:var(--ink); text-align:left; font:inherit; cursor:pointer; }
.settings-menu > button strong { font-size:.7rem; }
.settings-menu > button small, .settings-commentary small { color:var(--ink-soft); font-size:.58rem; line-height:1.35; }
.settings-menu > button:hover, .settings-menu > button.active { border-color:var(--accent-deep); background:#fff7e7; }
.settings-menu > button.active { box-shadow:inset 3px 0 var(--accent-deep); }
.settings-menu > button:disabled { cursor:not-allowed; opacity:.48; }
.settings-series { display:flex; align-items:center; justify-content:space-between; gap:.75rem; padding:.45rem .05rem .1rem; color:var(--ink-soft); font-size:.61rem; letter-spacing:.07em; text-transform:uppercase; }
.settings-series select { min-height:30px; padding:0 .35rem; border:1px solid var(--line); background:#fff; color:var(--ink); font:inherit; font-size:.68rem; }
.settings-commentary { display:flex; align-items:flex-start; gap:.5rem; padding:.55rem .05rem .05rem; border-top:1px solid var(--line); color:var(--ink); cursor:pointer; }
.settings-commentary input { width:1rem; height:1rem; margin:.08rem 0 0; accent-color:var(--accent-deep); }
.settings-commentary span { display:grid; gap:.08rem; }
.settings-commentary strong { font-size:.68rem; }
.simulator-message { margin: 1.5rem 0; color: var(--ink-soft); }.simulator-message.error { color: var(--warn); }
.simulator-status { position:relative; align-items: center; margin-top: 1.5rem; padding: 1rem 1.15rem; border: 1px solid var(--line); background: rgba(255,255,255,.72); }
.simulator-status > div:first-child span, .simulator-status small { display: block; color: var(--ink-soft); font-size: .65rem; letter-spacing: .08em; text-transform: uppercase; }
.simulator-status strong { display: block; margin: .18rem 0; font: 700 1.25rem var(--display); }
.simulator-actions { display: flex; align-items: end; gap: .5rem; }.simulator-actions label { display: grid; gap: .3rem; }
.simulator-actions button, .hero-options button, .draft-slots button { border: 1px solid var(--line); background: rgba(255,255,255,.86); color: var(--ink); font: inherit; cursor: pointer; }
.simulator-actions button { min-height: 42px; padding: .55rem .75rem; }.simulator-actions button:disabled, .hero-options button:disabled, .draft-slots button:disabled { cursor: default; opacity: .45; }
.side-assignment { position:absolute; left:50%; display:flex; align-items:end; gap:.45rem; transform:translateX(-50%); }.side-assignment label { display:grid; gap:.25rem; min-width:8.5rem; }.side-assignment label > span { font-size:.56rem; letter-spacing:.08em; text-transform:uppercase; }.side-assignment label.blue > span { color:#286999; }.side-assignment label.red > span { color:#a84b4b; }.side-assignment select { width:100%; min-height:42px; padding:.45rem .5rem; border:1px solid var(--line); background:#fff; color:var(--ink); font:inherit; font-size:.67rem; }.swap-sides { display:grid; width:44px; min-width:44px; height:42px; place-items:center; padding:0; border:1px solid #9ab9cd; border-radius:10px; background:linear-gradient(135deg, #e8f4fd 0 46%, #fff 46% 54%, #fbeeee 54%); color:var(--ink); box-shadow:0 2px 7px rgba(16,42,46,.12); font:700 1.3rem/1 var(--display); cursor:pointer; transition:transform .16s ease, box-shadow .16s ease; }.swap-sides:hover:not(:disabled) { box-shadow:0 4px 11px rgba(16,42,46,.2); transform:translateY(-1px) rotate(180deg); }.swap-sides:disabled { cursor:not-allowed; opacity:.4; }
.global-bp-panel { display:grid; grid-template-columns:minmax(14rem, 1fr) auto; gap:1rem 1.5rem; margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.72); }.global-bp-panel h2 { margin:0; font:700 1.35rem var(--display); letter-spacing:-.04em; }.global-bp-panel > div:first-child > p:last-child { max-width:38rem; margin:.4rem 0 0; color:var(--ink-soft); font-size:.72rem; }.global-actions, .picker-targets { display:flex; flex-wrap:wrap; gap:.45rem; align-items:center; }.global-actions button, .picker-targets button, .next-battle { min-height:36px; padding:.45rem .6rem; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink-soft); font:inherit; font-size:.67rem; cursor:pointer; }.global-actions button.active, .picker-targets button.active, .series-choice button.active { border-color:var(--accent-deep); background:var(--ink); color:#fff; }.series-format, .team-name { display:grid; gap:.12rem; color:var(--ink-soft); font-size:.58rem; letter-spacing:.08em; text-transform:uppercase; }.series-format select, .team-name input { min-height:30px; border:1px solid var(--line); background:rgba(255,255,255,.86); color:var(--ink); font:inherit; font-size:.67rem; }.team-name input { width:9rem; padding:0 .45rem; text-transform:none; letter-spacing:normal; }.global-used { display:grid; grid-template-columns:1fr 1fr auto; gap:.8rem; grid-column:1 / -1; padding-top:.8rem; border-top:1px solid var(--line); }.global-used > .used-team { display:flex; align-items:center; flex-wrap:wrap; gap:.35rem; }.global-used > .used-team > span { width:100%; color:var(--ink-soft); font-size:.62rem; letter-spacing:.08em; text-transform:uppercase; }.global-used > .used-team button { width:2rem; height:2rem; padding:0; border:1px solid var(--line); background:#fff; cursor:pointer; }.global-used img { width:100%; height:100%; object-fit:cover; }.global-used small { align-self:center; color:var(--ink-soft); font-size:.66rem; }.global-used > .next-battle { align-self:stretch; display:grid; gap:.45rem; min-width:13rem; padding:.65rem .7rem; border:1px solid var(--line); background:rgba(255,255,255,.9); color:var(--ink); white-space:normal; }.series-progress { display:grid; gap:.45rem; min-width:13rem; }.series-progress > small { color:var(--ink-soft); font-size:.58rem; line-height:1.4; }.series-progress > strong { padding:.42rem .5rem; border-left:3px solid var(--accent); background:rgba(232,191,108,.18); color:var(--ink); font:700 .7rem var(--mono); }.series-progress > span { font-size:.67rem; }.series-choice { display:flex; gap:.35rem; }.series-choice button { min-height:30px; padding:.35rem .5rem; border:1px solid var(--line); background:#fff; color:var(--ink-soft); font:inherit; font-size:.67rem; cursor:pointer; }.series-progress > button { min-height:32px; padding:.4rem .55rem; border:1px solid var(--accent-deep); background:var(--accent-deep); color:#fff; font:700 .65rem var(--mono); cursor:pointer; }.series-progress > button:disabled { cursor:not-allowed; opacity:.55; }.global-used > .next-battle:disabled { cursor:not-allowed; opacity:.5; }
.global-team-row { display:grid; grid-template-columns:repeat(2, minmax(15rem, 1fr)); gap:.55rem; }
.team-side-control { display:grid; grid-template-columns:minmax(0, 1fr); min-width:0; }
.mobile-used-hero-buttons, .mobile-used-scrim, .mobile-used-hero-modal { display:none; }
.global-actions button:disabled { cursor:not-allowed; opacity:.45; }
.team-required { grid-column:1 / -1; margin:0; padding:.65rem .75rem; border:1px solid #d9b663; background:#fff8e7; color:var(--ink-soft); font-size:.68rem; }
.upcoming-match-note { grid-column:1 / -1; margin:0; padding:.65rem .75rem; border-left:3px solid var(--accent); background:#edf8f3; color:var(--accent-deep); font-size:.68rem; line-height:1.45; }
.live-match-panel { grid-column:1 / -1; display:flex; align-items:center; justify-content:space-between; gap:1rem; margin:0; padding:.75rem; border:1px solid #d9b663; background:#fff8e7; }.live-match-panel.active { border-color:var(--accent-deep); background:#edf8f3; }.live-match-panel strong { display:block; margin:.1rem 0; font:700 .8rem var(--mono); }.live-match-panel small { display:block; max-width:48rem; color:var(--ink-soft); font-size:.62rem; line-height:1.45; }.live-match-panel .live-refresh-note { margin-top:.25rem; color:var(--accent-deep); }.live-match-panel > div:last-child { display:flex; flex-wrap:wrap; gap:.35rem; }.live-match-panel button { min-height:32px; padding:.4rem .55rem; border:1px solid var(--accent-deep); background:var(--accent-deep); color:#fff; font:700 .61rem var(--mono); cursor:pointer; white-space:nowrap; }.live-match-panel button.quiet { border-color:var(--line); background:#fff; color:var(--ink-soft); }.live-match-panel button:disabled { cursor:not-allowed; opacity:.55; }
.live-winner-prediction { grid-column:1 / -1; display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:.75rem; border:1px solid #9ab9cd; background:#f3f9fd; }.live-winner-prediction strong { display:block; margin:.1rem 0; font:700 .8rem var(--mono); }.live-winner-prediction small { display:block; color:var(--ink-soft); font-size:.62rem; line-height:1.45; }.live-winner-choices { display:flex; flex-wrap:wrap; gap:.4rem; }.live-winner-choices button { display:grid; gap:.1rem; min-width:7.5rem; min-height:38px; padding:.4rem .6rem; border:1px solid #9ab9cd; background:#fff; color:var(--ink); font:700 .64rem var(--mono); cursor:pointer; }.live-winner-choices button.active { border-color:var(--accent-deep); background:var(--ink); color:#fff; }.live-winner-choices button.active small { color:#fff; }.live-winner-choices button:disabled { cursor:not-allowed; opacity:.6; }
.simulator-workspace { display:grid; grid-template-columns:minmax(0, 1fr) minmax(340px, 390px); gap:.85rem; align-items:start; margin-top:.75rem; }.simulator-main-column { min-width:0; }.coach-rail { position:sticky; top:1rem; min-width:0; }.simulator-layout { align-items: stretch; margin-top:0; gap:.75rem; }.draft-board { display: grid; flex: 1; min-width:0; grid-template-columns: repeat(2, minmax(0,1fr)); gap: .75rem; }
.peak-duel-board { display:grid; min-height:20rem; flex:1; place-content:center; padding:2rem; border:1px dashed var(--line); background:rgba(255,255,255,.6); color:var(--ink-soft); text-align:center; }
.peak-duel-board h2 { margin:0; color:var(--ink); font:700 clamp(1.8rem, 4vw, 3rem) var(--display); letter-spacing:-.04em; }
.mobile-group-title { display:none; }
.mobile-coach-toggle,.mobile-coach-close,.coach-scrim{display:none}
.draft-group, .forecast-panel, .hero-picker { border: 1px solid var(--line); background: rgba(255,255,255,.76); }.draft-group { min-height: 160px; padding: 1rem; }.draft-group > p { margin: 0 0 .8rem; font-size: .67rem; letter-spacing: .1em; text-transform: uppercase; }.draft-group.blue > p { color: #286999; }.draft-group.red > p { color: #a84b4b; }
.draft-slots { display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:.35rem; }.draft-slots button, .draft-slots span { display:grid; place-items:center; width:100%; max-width:4rem; aspect-ratio:1; padding:0; font-size:.7rem; text-align:left; }.draft-slots button img { width:100%; height:100%; object-fit:cover; }.draft-slots span { border: 1px dashed var(--line); color: var(--ink-soft); }
.forecast-panel { width: min(31%, 320px); min-width:250px; padding: 1rem; }.forecast-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }.forecast-heading h2 { font-size: 1.5rem; }.forecast-heading > span, .forecast-heading small { color: var(--ink-soft); font-size: .68rem; }
.probability-list { margin-top: 1rem; }.probability-list > div { display: grid; grid-template-columns:2rem minmax(4rem,1.8fr) 3rem; gap: .55rem; align-items: center; margin-top: .55rem; font-size: .7rem; }.probability-list img { width:2rem; height:2rem; object-fit:cover; }.probability-list em { color: var(--ink-soft); font-style: normal; text-align: right; }.probability-track { height: .42rem; overflow: hidden; background: rgba(16,42,46,.1); }.probability-track i { display:block; height:100%; background: var(--accent); }
.end-ban-list { margin-top: 1.2rem; padding-top: .85rem; border-top: 1px solid var(--line); }.end-ban-list p { margin:0 0 .5rem; color: var(--ink-soft); font-size:.65rem; }.end-ban-list span { display:inline-flex; align-items:center; gap:.25rem; margin:.25rem .6rem 0 0; font-size:.7rem; }.end-ban-list img { width:1.6rem; height:1.6rem; object-fit:cover; }
.recommendation-panel { margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.8); }.recommendation-panel > header { display:flex; align-items:center; justify-content:space-between; gap:1rem; }.recommendation-panel h2 { margin:0; font:700 1.25rem var(--display); letter-spacing:-.035em; }.recommendation-panel header p:last-child { max-width:48rem; margin:.3rem 0 0; color:var(--ink-soft); font-size:.68rem; }.recommendation-status { padding:.38rem .55rem; border:1px solid var(--line); background:#edf8f3; color:var(--accent-deep); font:700 .6rem var(--mono); white-space:nowrap; }.recommendation-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.6rem; margin-top:.9rem; }.recommendation-list article { min-width:0; padding:.7rem; border:1px solid var(--line); background:#fff; }.recommendation-choice { display:grid; width:100%; grid-template-columns:auto 2.6rem minmax(0,1fr); gap:.45rem; align-items:center; padding:0 0 .6rem; border:0; border-bottom:1px solid var(--line); background:none; color:var(--ink); text-align:left; cursor:pointer; }.recommendation-choice:disabled { cursor:not-allowed; opacity:.55; }.recommendation-choice img { width:2.6rem; height:2.6rem; object-fit:cover; }.recommendation-choice span:last-child { display:grid; min-width:0; }.recommendation-choice strong { overflow:hidden; font:700 .78rem var(--mono); text-overflow:ellipsis; white-space:nowrap; }.recommendation-choice small { color:var(--ink-soft); font-size:.56rem; }.recommendation-rank { color:var(--accent-deep); font:700 .65rem var(--mono); }.recommendation-list dl { display:grid; grid-template-columns:1fr 1fr; gap:.35rem .55rem; margin:.65rem 0; }.recommendation-list dl div { min-width:0; }.recommendation-list dt { display:flex; align-items:center; gap:.2rem; color:var(--ink-soft); font-size:.52rem; letter-spacing:.04em; }.recommendation-list dd { margin:.08rem 0 0; font:700 .66rem var(--mono); }.metric-help { position:relative; display:inline-grid; width:.85rem; height:.85rem; flex:0 0 .85rem; place-items:center; padding:0; border:1px solid currentColor; border-radius:50%; background:#fff; color:var(--ink-soft); font:700 .5rem/1 var(--mono); cursor:help; }.metric-help::after { position:absolute; z-index:20; bottom:calc(100% + .4rem); left:50%; width:12rem; padding:.45rem .5rem; border:1px solid var(--line); background:var(--ink); color:#fff; box-shadow:0 .35rem .9rem rgba(16,42,46,.2); content:attr(data-help); font:.56rem/1.45 var(--mono); letter-spacing:0; opacity:0; pointer-events:none; text-align:left; transform:translate(-50%, .2rem); transition:opacity .15s ease, transform .15s ease; }.metric-help:hover::after,.metric-help:focus-visible::after { opacity:1; transform:translate(-50%, 0); }.recommendation-reasons { display:flex; flex-wrap:wrap; gap:.25rem; }.recommendation-reasons span { padding:.2rem .3rem; background:#edf8f3; color:var(--accent-deep); font-size:.52rem; }.recommendation-list article > p { margin:.55rem 0 0; color:var(--ink-soft); font-size:.57rem; line-height:1.4; }.recommendation-warning { display:block; margin-top:.7rem; color:var(--ink-soft); font-size:.56rem; }.recommendation-error { margin:.7rem 0 0; color:var(--warn); font-size:.65rem; }
.lineup-score-panel { margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--line); background:linear-gradient(135deg,rgba(237,248,243,.95),rgba(255,255,255,.92)); }.lineup-score-panel > header { display:flex; align-items:center; justify-content:space-between; gap:1rem; }.lineup-score-panel h2 { margin:0; font:700 1.25rem var(--display); letter-spacing:-.035em; }.lineup-score-panel header p:last-child { margin:.3rem 0 0; color:var(--ink-soft); font-size:.68rem; }.lineup-score-teams { display:grid; grid-template-columns:1fr 1fr; gap:.7rem; margin-top:.9rem; }.lineup-score-teams article { display:grid; gap:.18rem; padding:.85rem; border:1px solid var(--line); background:#fff; }.lineup-score-teams article.red { text-align:right; }.lineup-score-teams small,.lineup-score-teams span { color:var(--ink-soft); font-size:.58rem; }.lineup-score-teams strong { font:700 1.8rem var(--display); }.lineup-score-teams .blue strong { color:#247aa5; }.lineup-score-teams .red strong { color:#b74942; }.lineup-score-breakdown { display:grid; grid-template-columns:1fr 1fr; gap:.7rem; margin-top:.7rem; }.lineup-score-breakdown > div { padding:.75rem; border:1px solid var(--line); background:rgba(255,255,255,.8); }.lineup-score-breakdown h3 { margin:0 0 .55rem; font:700 .72rem var(--mono); }.lineup-score-breakdown dl { display:grid; grid-template-columns:1fr 1fr; gap:.45rem .7rem; margin:0; }.lineup-score-breakdown dl div { min-width:0; }.lineup-score-breakdown dt { color:var(--ink-soft); font-size:.54rem; }.lineup-score-breakdown dd { margin:.08rem 0 0; font:700 .67rem var(--mono); }.lineup-score-breakdown dd.positive { color:#167451; }.lineup-score-breakdown dd.negative { color:#a8463f; }
.metric-help { box-sizing:border-box; min-width:.85rem; max-width:.85rem; min-height:.85rem; max-height:.85rem; aspect-ratio:1; appearance:none; border-radius:999px; }
.recommendation-detail-toggle { display:none; }
.recommendation-details > p { margin:.55rem 0 0; color:var(--ink-soft); font-size:.57rem; line-height:1.4; }
.commentary-panel { margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--accent-deep); background:linear-gradient(120deg, rgba(232,191,108,.18), rgba(255,255,255,.84)); }.commentary-panel h2 { max-width:70rem; margin:.25rem 0 0; font:700 1rem/1.55 var(--display); letter-spacing:-.015em; }.commentary-loading { margin:0; color:var(--ink-soft); font-size:.75rem; }
.hero-picker { margin-top: .75rem; padding: 1rem; }.picker-heading { display:flex; align-items:end; justify-content:space-between; gap:1rem; }.picker-heading h2 { font-size:1.4rem; }.picker-controls { display:flex; align-items:end; gap:.55rem; }.picker-controls input { width:min(100%, 260px); }.hero-lane-filter { display:grid; gap:.22rem; color:var(--ink-soft); font-size:.67rem; font-weight:700; letter-spacing:.04em; }.hero-lane-filter select { min-width:9.2rem; }.picker-targets { margin-top:.85rem; }.hero-options { display:grid; grid-template-columns:repeat(auto-fill, minmax(3.6rem, 1fr)); gap:.45rem; margin-top:1rem; max-height:360px; overflow:auto; }.hero-options button { position:relative; display:grid; place-items:center; aspect-ratio:1; padding:0; overflow:hidden; }.hero-options button img { width:100%; height:100%; object-fit:cover; }.hero-options button small { position:absolute; right:0; bottom:0; padding:.14rem .2rem; background:rgba(16,42,46,.84); color:#fff; font-size:.56rem; }.hero-options button:hover:not(:disabled), .draft-slots button:not(:disabled):hover { border-color: var(--accent); color: var(--accent-deep); }
@media (max-width: 1000px) { .simulator-workspace { grid-template-columns:1fr; }.coach-rail { position:static; }.coach-rail { grid-row:1; }.simulator-main-column { grid-row:2; } }
@media (max-width: 860px) { .simulator-hero, .simulator-status, .simulator-layout { flex-direction:column; align-items:stretch; }.simulator-header-controls { justify-content:stretch; }.simulator-season, .forecast-panel { width:100%; }.simulator-season { min-width:0; }.simulator-settings { align-self:flex-end; }.forecast-panel { min-width:0; }.simulator-actions { justify-content:space-between; }.side-assignment { position:static; width:100%; transform:none; }.side-assignment label { flex:1; }.draft-board { grid-template-columns:1fr; }.global-bp-panel { grid-template-columns:1fr; }.global-used { grid-template-columns:1fr; }.next-battle { justify-self:start; }.recommendation-list,.lineup-score-breakdown { grid-template-columns:1fr; } }
@media (max-width:620px) {
  .settings-menu { left:0; right:auto; width:min(19rem, calc(100vw - 1rem)); }
  .forecast-panel { display:none; }
  .simulator-layout { display:contents; }
  .recommendation-panel > header { align-items:stretch; flex-direction:column; }.recommendation-status { align-self:flex-start; }.recommendation-list dl { grid-template-columns:1fr 1fr; }
  .recommendation-list article { position:relative; }
  .recommendation-choice { min-height:2.6rem; padding-right:7.2rem; padding-bottom:0; border-bottom:0; }
  .recommendation-detail-toggle { position:absolute; top:1.05rem; right:.7rem; display:flex; width:auto; min-height:1.75rem; align-items:center; gap:.35rem; margin:0; padding:.3rem .45rem; border:1px solid var(--line); border-radius:.3rem; background:#fff; color:var(--accent-deep); font:700 .58rem var(--mono); }
  .recommendation-detail-toggle span { font-size:.78rem; line-height:1; }
  .recommendation-details:not(.expanded) { display:none; }
  .metric-help::after { right:-.4rem; left:auto; width:min(12rem, calc(100vw - 3rem)); transform:translateY(.2rem); }
  .metric-help:hover::after,.metric-help:focus-visible::after { transform:translateY(0); }
  .global-used > .used-team { display:none; }
  .mobile-used-hero-buttons { display:grid; grid-template-columns:1fr 1fr; grid-column:1 / -1; gap:.45rem; }
  .mobile-used-hero-buttons button { display:grid; gap:.12rem; min-height:3rem; padding:.45rem .55rem; border:1px solid var(--line); background:rgba(255,255,255,.9); color:var(--ink); text-align:left; }
  .mobile-used-hero-buttons button.blue { border-left:3px solid #286999; }
  .mobile-used-hero-buttons button.red { border-left:3px solid #a84b4b; }
  .mobile-used-hero-buttons span { font:700 .68rem var(--mono); }
  .mobile-used-hero-buttons small { color:var(--ink-soft); font-size:.54rem; }
  .mobile-used-scrim { position:fixed; z-index:100; inset:0; display:block; width:100%; height:100%; margin:0; padding:0; border:0; background:rgba(16,42,46,.32); }
  .mobile-used-hero-modal { position:fixed; z-index:101; right:.75rem; bottom:calc(5.25rem + env(safe-area-inset-bottom)); left:.75rem; display:grid; max-height:min(28rem, 60dvh); overflow:auto; border:1px solid var(--line); border-radius:.8rem; background:#fff; box-shadow:0 1rem 3rem rgba(16,42,46,.28); }
  .mobile-used-hero-modal header { display:flex; align-items:start; justify-content:space-between; gap:.75rem; padding:.8rem; border-bottom:1px solid var(--line); }
  .mobile-used-hero-modal h2 { margin:.12rem 0 0; font:700 1rem var(--display); }
  .mobile-used-hero-modal header button { display:grid; width:2rem; height:2rem; min-height:2rem; place-items:center; padding:0; border:1px solid var(--line); border-radius:50%; background:#fff; color:var(--ink); font:400 1.2rem/1 var(--display); }
  .mobile-used-hero-list { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:.4rem; padding:.8rem; }
  .mobile-used-hero-list button { display:grid; justify-items:center; gap:.2rem; min-width:0; padding:.25rem; border:1px solid var(--line); background:#fff; color:var(--ink); font-size:.5rem; text-align:center; }
  .mobile-used-hero-list img { width:2.5rem; max-width:100%; aspect-ratio:1; object-fit:cover; }
  .mobile-used-hero-list span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .mobile-used-hero-list > p { grid-column:1 / -1; margin:.4rem 0; color:var(--ink-soft); font-size:.66rem; text-align:center; }
  .global-actions { display:grid; width:100%; gap:.55rem; }
  .global-team-row { grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:.35rem; min-width:0; }
  .global-team-row :deep(.team-combobox) { min-width:0; }
  .draft-board {
    position:sticky;
    z-index:20;
    top:0;
    grid-template-columns:repeat(2, minmax(0, 1fr));
    grid-template-areas:"blue-bans red-bans" "blue-picks red-picks";
    gap:1px;
    border:1px solid var(--line);
    background:var(--line);
    box-shadow:0 2px 8px rgba(16,42,46,.1);
  }
  .draft-group { min-height:0; padding:.55rem; border:0; background:#fff; }
  .draft-group.blue_bans { grid-area:blue-bans; }
  .draft-group.red_bans { grid-area:red-bans; }
  .draft-group.blue_picks { grid-area:blue-picks; }
  .draft-group.red_picks { grid-area:red-picks; }
  .draft-group > p { margin:0 0 .4rem; overflow:hidden; font-size:.52rem; letter-spacing:.07em; text-overflow:ellipsis; white-space:nowrap; }
  .desktop-group-title { display:none; }
  .mobile-group-title { display:inline; }
  .draft-slots { gap:.16rem; }
  .draft-slots button, .draft-slots span { max-width:none; font-size:.55rem; }
  .draft-slots button { min-height:0; overflow:hidden; }
}
@media (max-width: 620px) { .simulator-page { width:calc(100% - 1rem); padding-top:1.25rem; }.simulator-status { gap:1rem; }.simulator-actions { flex-wrap:wrap; }.picker-heading { align-items:stretch; flex-direction:column; }.picker-controls { align-items:stretch; flex-direction:column; }.picker-controls input, .hero-lane-filter select { width:100%; }.hero-options { grid-template-columns:repeat(auto-fill, minmax(3.25rem, 1fr)); }.coach-rail{display:none}.coach-rail.coach-open{position:fixed;z-index:91;right:.75rem;bottom:calc(5.25rem + env(safe-area-inset-bottom));left:.75rem;display:block;overflow:hidden;border:1px solid var(--line);border-radius:.8rem;background:#fff;box-shadow:0 1rem 3rem rgba(16,42,46,.28)}.coach-rail.coach-open :deep(.coach-panel){height:auto;min-height:0;max-height:none;grid-template-rows:auto minmax(150px,auto) auto auto;border:0;box-shadow:none}.coach-rail.coach-open :deep(.coach-header){padding:.72rem 3.25rem .72rem .8rem}.coach-rail.coach-open :deep(.coach-thread){min-height:150px;max-height:42dvh;padding:.75rem}.coach-rail.coach-open :deep(.coach-form){padding:.65rem .7rem .45rem}.coach-rail.coach-open :deep(.coach-disclaimer){padding:0 .7rem .45rem}.coach-scrim{position:fixed;z-index:90;inset:0;display:block;width:100%;height:100%;border:0;background:rgba(16,42,46,.28)}.mobile-coach-toggle{position:fixed;z-index:80;right:1rem;bottom:calc(6rem + env(safe-area-inset-bottom));display:grid;width:3.5rem;height:3.5rem;place-items:center;border:1px solid rgba(255,255,255,.7);border-radius:50%;background:var(--ink);color:#fff;box-shadow:0 .6rem 1.4rem rgba(16,42,46,.28);font-family:var(--mono)}.mobile-coach-toggle span{position:absolute;top:.38rem;right:.5rem;color:#8fe0c8;font-size:.8rem}.mobile-coach-toggle strong{font-size:.7rem;letter-spacing:.08em}.coach-open~.mobile-coach-toggle{display:none}.mobile-coach-close{position:absolute;z-index:2;top:.65rem;right:.65rem;display:grid;width:1.85rem;height:1.85rem;min-height:1.85rem;place-items:center;margin:0;padding:0;border:1px solid rgba(255,255,255,.28);border-radius:.5rem;background:rgba(255,255,255,.12);color:#fff;box-shadow:none;font:400 1.15rem/1 var(--display)} }
@media (max-width: 620px) { .coach-rail.coach-open{top:auto;height:75dvh;max-height:75dvh;border-radius:1rem}.coach-rail.coach-open :deep(.coach-panel){height:100% !important;min-height:0 !important;max-height:none !important;grid-template-rows:auto minmax(0,1fr) auto auto !important}.coach-rail.coach-open :deep(.coach-thread){min-height:0 !important;max-height:none !important} }
</style>
