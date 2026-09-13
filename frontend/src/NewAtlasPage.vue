<script setup>
import { computed, defineAsyncComponent, onMounted, ref, shallowRef, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import {
  fetchDailyMatches,
  fetchBattleLineups,
  fetchHeroMatchupRecommendations,
  fetchHeroResponses,
  fetchLearnedFeatureSpace,
  fetchLiveWinnerPredictions,
  fetchMetaHistory,
  fetchPatternManifest,
  fetchPowerRankings,
  fetchTeamSynergies,
  fetchVisualizationPatterns,
  saveLiveWinnerPrediction,
} from "./api";
import { heroAsset } from "./heroAssets";
import { language } from "./i18n";
import { selectedLeagueId } from "./selectedLeague";
import { useSeasonCatalog } from "./composables/useSeasonCatalog";
import { finishStartupLoading } from "./startupLoader";
import { getStored, setStored } from "./storage";

const LineupAnalyzerWidget = defineAsyncComponent(() => import("./LineupAnalyzerWidget.vue"));
const DraftSimulatorPage = defineAsyncComponent(() => import("./DraftSimulatorPage.vue"));
const RankingsPage = defineAsyncComponent(() => import("./RankingsPage.vue"));
const MethodologyPage = defineAsyncComponent(() => import("./MethodologyPage.vue"));

const route = useRoute();
const router = useRouter();
const leagueId = selectedLeagueId;
const { seasons, loadSeasons } = useSeasonCatalog();
const space = shallowRef(null);
const meta = shallowRef(null);
const patterns = shallowRef(null);
const rankings = shallowRef(null);
const synergies = shallowRef(null);
const matches = shallowRef([]);
const responseRows = shallowRef([]);
const historicalLineups = shallowRef([]);
const loading = ref(false);
const error = ref("");
const opponentIds = ref([]);
const opponentSearch = ref("");
const lane = ref("");
const recommendations = shallowRef(null);
const recommendationLoading = ref(false);
const relation = ref("counter_pick");
const teamId = ref("");
const rankBoard = ref("teams");
const matchDate = ref("");
const atlasHeroId = ref(null);
const favoriteIds = ref([]);
const favoriteSearch = ref("");
const matchupExpanded = ref(false);
const matchupEvidence = ref(null);
const atlasShowAll = ref(false);
const atlasZoom = ref(1);
const selectedMetaHeroId = ref(null);
const metaExpanded = ref(false);
const relationMetric = ref("smoothed_lift");
const relationSearch = ref("");
const relationSupport = ref(3);
const relationLimit = ref(20);
const relationEvidence = ref(null);
const pairMetric = ref("selection_count");
const pairSearch = ref("");
const pairSupport = ref(3);
const pairLimit = ref(20);
const pairEvidence = ref(null);
const matchForms = ref({});
const matchVotes = ref({});
const predictionSaving = ref("");
const predictionNotice = ref("");
const favoriteStorageKey = "draft-atlas-favorite-hero-ids";
const visitorStorageKey = "draft-atlas-visitor-id";

const isChinese = computed(() => language.value === "zh-CN");
const tr = (english, chinese) => isChinese.value ? chinese : english;
const sections = {
  plan: [["matchups", "Hero matchups", "英雄对局推荐"], ["atlas", "Hero atlas", "英雄图谱"], ["lineups", "Lineup lab", "阵容实验室"]],
  draft: [["workspace", "Draft workspace", "BP 工作台"]],
  research: [["meta", "Season meta", "赛季版本"], ["relationships", "BP relationships", "BP 关系"], ["rankings", "Rankings", "实力排行"], ["pairs", "Team pairs", "战队组合"]],
  matches: [["schedule", "Schedule & predictions", "赛程与预测"]],
  learn: [["methods", "How it works", "方法说明"]],
};
const navigationLabels = { plan: ["Plan", "规划"], draft: ["Draft", "BP"], research: ["Research", "研究"], matches: ["Matches", "赛程"], learn: ["How it works", "方法说明"] };
const workspaceLabels = { plan: ["Plan workspace", "规划工作台"], draft: ["Draft workspace", "BP 工作台"], research: ["Research workspace", "研究工作台"], matches: ["Matches workspace", "赛程工作台"], learn: ["Learning", "方法说明"] };
const mechanicLabelsZh = {
  condition__ally_targeted: "以友方为目标", condition__back_attack: "背后攻击", condition__channel_or_charge: "引导或蓄力", condition__delayed_effect: "延迟生效", condition__directional: "方向性技能", condition__distance_scaling: "随距离变化", condition__front_attack_penalty: "正面攻击惩罚", condition__low_health_condition: "低生命值触发", condition__on_kill_or_assist: "击杀或助攻触发", condition__proximity: "近距离触发", condition__recast: "可再次施放", condition__requires_mark: "需要标记", condition__single_target_bonus: "单体目标加成", condition__water_or_river: "水域或河道触发",
  mechanic__buff_attack_power: "攻击力增益", mechanic__buff_attack_speed: "攻速增益", mechanic__buff_defense: "防御增益", mechanic__control_anti_mobility: "限制位移", mechanic__control_blind: "致盲", mechanic__control_disarm: "缴械", mechanic__control_fear: "恐惧", mechanic__control_freeze: "冰冻", mechanic__control_knockback: "击退", mechanic__control_knockup: "击飞", mechanic__control_petrify: "石化", mechanic__control_pull: "牵引", mechanic__control_root: "定身", mechanic__control_silence: "沉默", mechanic__control_slow: "减速", mechanic__control_stun: "眩晕", mechanic__control_suppress: "压制", mechanic__control_taunt: "嘲讽",
  mechanic__damage_amplification: "伤害增幅", mechanic__damage_execute: "斩杀伤害", mechanic__damage_magic: "法术伤害", mechanic__damage_percent_health: "百分比生命伤害", mechanic__damage_physical: "物理伤害", mechanic__damage_true: "真实伤害", mechanic__debuff_armor: "降低物防", mechanic__debuff_healing_reduction: "降低治疗", mechanic__debuff_magic_defense: "降低法防", mechanic__debuff_shield_break: "破盾",
  mechanic__defense_cleanse: "净化", mechanic__defense_control_immunity: "控制免疫", mechanic__defense_damage_block: "伤害格挡", mechanic__defense_damage_reduction: "伤害减免", mechanic__defense_damage_reflect: "伤害反弹", mechanic__defense_death_prevention: "免死", mechanic__defense_invulnerable: "无敌", mechanic__defense_projectile_block: "飞行物格挡", mechanic__defense_revive: "复活", mechanic__defense_shield: "护盾", mechanic__defense_untargetable: "不可选中",
  mechanic__mobility_dash: "位移", mechanic__mobility_speed_boost: "移速提升", mechanic__mobility_teleport: "传送", mechanic__mobility_wall_traverse: "穿越地形", mechanic__support_ally_heal: "治疗友方", mechanic__support_ally_reposition: "友方位移", mechanic__support_ally_shield: "友方护盾", mechanic__support_ally_skill_refresh: "刷新友方技能", mechanic__support_damage_rewind: "伤害回溯", mechanic__sustain_heal: "自我治疗", mechanic__sustain_lifesteal: "吸血", mechanic__sustain_mana_restore: "法力回复",
  mechanic__utility_aoe_basic_attack: "范围普攻", mechanic__utility_basic_attack_enhancement: "强化普攻", mechanic__utility_clone_or_mimic: "分身或模仿", mechanic__utility_cooldown_reduction: "冷却缩减", mechanic__utility_gold_generation: "额外经济", mechanic__utility_mark: "标记", mechanic__utility_range_extension: "攻击距离延伸", mechanic__utility_skill_refresh: "技能刷新", mechanic__utility_stealth: "隐身", mechanic__utility_structure_interaction: "防御塔交互", mechanic__utility_summon: "召唤物", mechanic__utility_terrain: "地形生成", mechanic__utility_transformation: "形态切换", mechanic__utility_vision: "视野", mechanic__utility_zone: "区域控制", mechanic__vulnerability_projectile_blockable: "可被飞行物格挡",
};
const displayedSections = computed(() => Object.fromEntries(Object.entries(sections).map(([name, items]) => [name, items.map(([key, en, zh]) => [key, tr(en, zh)])])));
const section = computed(() => sections[route.params.section] ? route.params.section : "plan");
const view = computed(() => {
  const requested = String(route.params.view || "");
  return sections[section.value].some(([value]) => value === requested)
    ? requested : sections[section.value][0][0];
});
const title = computed(() => {
  const copy = {
    matchups: ["Find a hero for this matchup", "为这场对局找到合适英雄", "Start from visible opponent picks, then narrow by lane.", "从已知敌方英雄开始，再按分路缩小范围。"],
    atlas: ["Explore hero similarities", "探索英雄相似性", "Browse the learned feature space and inspect a hero’s profile.", "浏览学习到的特征空间，并查看英雄档案。"],
    lineups: ["Build and compare two lineups", "构建并比较两套阵容", "Explore role coverage, synergy, responses, and complete-lineup value.", "探索位置覆盖、阵容协同、克制应对与完整阵容价值。"],
    workspace: ["Draft workspace", "BP 工作台", "Run the full production draft flow with live recommendations and evidence.", "使用实时推荐与证据，完成正式 BP 全流程。"],
    meta: ["Follow the season meta", "追踪赛季版本", "Opening priorities and how they change across competitions.", "查看开局优先级，以及它如何随赛事变化。"],
    relationships: ["Inspect a draft relationship", "查看 BP 关系", "Observed responses, legal opportunities, and supporting evidence.", "查看观测到的应对、合法机会和支撑证据。"],
    rankings: ["Compare current strength", "比较当前实力", "Teams, hero specialists, and role-normalized player performance.", "比较战队、英雄绝活玩家和按位置标准化的选手表现。"],
    pairs: ["Understand a team’s pairings", "理解战队英雄组合", "Combinations completed while both heroes remain legally available.", "查看双方英雄均合法时，战队完成的英雄组合。"],
    schedule: ["Matches and predictions", "赛程与预测", "Browse scheduled fixtures and make a pre-match prediction.", "浏览赛程，并在赛前作出预测。"],
    methods: ["Understand the evidence", "理解证据", "Read the definitions, limits, model flow, and interpretation guidance.", "查看定义、限制、模型流程与解读指南。"],
  }[view.value];
  return [tr(copy[0], copy[1]), tr(copy[2], copy[3])];
});
const heroes = computed(() => space.value?.rows || []);
const selectedOpponents = computed(() => heroes.value.filter((hero) => opponentIds.value.includes(Number(hero.hero_id))));
const selectedFavorites = computed(() => heroes.value.filter((hero) => favoriteIds.value.includes(Number(hero.hero_id))));
function matchingHeroes(query, excluded = []) {
  const q = query.trim().toLocaleLowerCase().replaceAll(/\s+/g, "");
  const excludedIds = new Set(excluded.map(Number));
  return heroes.value.filter((hero) => !excludedIds.has(Number(hero.hero_id)) && (!q || hero.hero_name.toLocaleLowerCase().replaceAll(/\s+/g, "").includes(q))).sort((a, b) => Number(b.weighted_bp_action_count || 0) - Number(a.weighted_bp_action_count || 0));
}
const opponentOptions = computed(() => matchingHeroes(opponentSearch.value, [...opponentIds.value, ...favoriteIds.value]).slice(0, 12));
const favoriteOptions = computed(() => matchingHeroes(favoriteSearch.value, [...opponentIds.value, ...favoriteIds.value]).slice(0, 12));
const atlasOptions = computed(() => matchingHeroes(opponentSearch.value).slice(0, atlasShowAll.value ? heroes.value.length : 48));
const matchupRows = computed(() => (recommendations.value?.recommendations || []).slice(0, matchupExpanded.value ? 12 : 6));
const atlasHero = computed(() => heroes.value.find((hero) => Number(hero.hero_id) === Number(atlasHeroId.value)) || heroes.value[0] || null);
const atlasNeighbours = computed(() => {
  const ids = new Set((atlasHero.value?.nearest_hero_ids || []).map(Number));
  return heroes.value.filter((hero) => ids.has(Number(hero.hero_id))).slice(0, 8);
});
const atlasBounds = computed(() => {
  const xs = heroes.value.map((hero) => Number(hero.x)); const ys = heroes.value.map((hero) => Number(hero.y));
  return { minX: Math.min(...xs, -1), maxX: Math.max(...xs, 1), minY: Math.min(...ys, -1), maxY: Math.max(...ys, 1) };
});
function atlasX(value) { const b = atlasBounds.value; return 38 + ((Number(value) - b.minX) / (b.maxX - b.minX || 1)) * 724; }
function atlasY(value) { const b = atlasBounds.value; return 402 - ((Number(value) - b.minY) / (b.maxY - b.minY || 1)) * 364; }
const currentMeta = computed(() => (meta.value || []).find((row) => String(row.season?.league_id) === String(leagueId.value)) || (meta.value || []).at(-1) || null);
const allMetaHeroes = computed(() => (currentMeta.value?.meta_heroes || []).filter((hero) => hero.early_priority_count > 0).sort((a, b) => Number(a.priority_rank) - Number(b.priority_rank)));
const metaHeroes = computed(() => allMetaHeroes.value.slice(0, metaExpanded.value ? 12 : 5));
const selectedMetaHero = computed(() => allMetaHeroes.value.find((hero) => Number(hero.hero_id) === Number(selectedMetaHeroId.value)) || allMetaHeroes.value[0] || null);
const metaTrend = computed(() => (meta.value || []).map((season) => {
  const hero = (season.meta_heroes || []).find((row) => Number(row.hero_id) === Number(selectedMetaHero.value?.hero_id));
  return { season: season.season, hero };
}).filter((row) => row.hero));
const metaMaximum = computed(() => Math.max(...metaTrend.value.map((row) => Number(row.hero.early_priority_rate || 0)), 0.01));
const metaPoints = computed(() => metaTrend.value.map((row, index) => ({ ...row, x: 38 + (index / Math.max(1, metaTrend.value.length - 1)) * 524, y: 166 - (Number(row.hero.early_priority_rate || 0) / metaMaximum.value) * 132 })));
const metaPolyline = computed(() => metaPoints.value.map((row) => `${row.x},${row.y}`).join(" "));
const relationRows = computed(() => {
  const needle = relationSearch.value.trim().toLocaleLowerCase();
  return (patterns.value?.rows || []).filter((row) => row.relation === relation.value && row.context_level === "overall" && !row.is_peak_battle && Number(row.selections || 0) >= relationSupport.value && (!needle || `${row.source_hero_name} ${row.target_hero_name}`.toLocaleLowerCase().includes(needle))).sort((a, b) => Number(b[relationMetric.value] || 0) - Number(a[relationMetric.value] || 0)).slice(0, relationLimit.value);
});
const rankRows = computed(() => rankings.value?.team_rankings || []);
const teams = computed(() => synergies.value?.teams || []);
const activeTeam = computed(() => teams.value.find((team) => team.team_id === teamId.value));
const pairRows = computed(() => {
  const needle = pairSearch.value.trim().toLocaleLowerCase();
  return (synergies.value?.rows || []).filter((row) => row.team_id === teamId.value && Number(row.selection_count || 0) >= pairSupport.value && (!needle || `${row.hero_a_name} ${row.hero_b_name} ${row.pair_name}`.toLocaleLowerCase().includes(needle))).sort((a, b) => Number(b[pairMetric.value] || 0) - Number(a[pairMetric.value] || 0)).slice(0, pairLimit.value);
});
const relationOptions = computed(() => [["counter_pick", tr("Counter picks", "反制选人")], ["counter_ban", tr("Counter bans", "反制禁用")], ["pick_synergy", tr("Ally synergy", "协同选人")], ["ban_response", tr("After a ban", "禁用后的应对")]]);

function go(nextSection, nextView) { router.push(`/new/${nextSection}/${nextView}`); }
function percent(value) { return value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`; }
function number(value, digits = 0) { return Number(value || 0).toLocaleString(language.value, { maximumFractionDigits: digits, minimumFractionDigits: digits }); }
function heroIcon(hero) { return hero?.catalog_filler ? "" : heroAsset(hero?.hero_id); }
function addOpponent(hero) { opponentIds.value = [...opponentIds.value, Number(hero.hero_id)].slice(0, 5); opponentSearch.value = ""; }
function removeOpponent(heroId) { opponentIds.value = opponentIds.value.filter((id) => id !== Number(heroId)); recommendations.value = null; }
function addFavorite(hero) { favoriteIds.value = [...favoriteIds.value, Number(hero.hero_id)].slice(0, 12); favoriteSearch.value = ""; }
function removeFavorite(heroId) { favoriteIds.value = favoriteIds.value.filter((id) => id !== Number(heroId)); recommendations.value = null; }
function laneLabel(value) { return ({ clash: tr("Clash", "对抗路"), jungle: tr("Jungle", "打野"), mid: tr("Mid", "中路"), farm: tr("Farm", "发育路"), roam: tr("Roam", "游走"), unknown: tr("Unknown", "未知") })[value] || value; }
function mechanicLabel(value) { return isChinese.value ? (mechanicLabelsZh[value] || value.replaceAll("_", " ")) : value.replace(/^(mechanic|condition)__/, "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
async function recommend() {
  if (!leagueId.value || !opponentIds.value.length) return;
  recommendationLoading.value = true; error.value = "";
  try { recommendations.value = await fetchHeroMatchupRecommendations({ league_id: leagueId.value, favorite_hero_ids: favoriteIds.value, opponent_hero_ids: opponentIds.value, preferred_lane: lane.value || null, limit: 12 }); matchupExpanded.value = false; matchupEvidence.value = null; }
  catch { error.value = tr("Could not calculate recommendations.", "无法计算英雄推荐。"); }
  finally { recommendationLoading.value = false; }
}
async function load() {
  loading.value = true; error.value = "";
  try {
    const id = leagueId.value;
    if (!id) return;
    if (["matchups", "atlas", "lineups"].includes(view.value)) space.value = await fetchLearnedFeatureSpace(id);
    if (view.value === "lineups") {
      const [responses, battles] = await Promise.all([
        fetchHeroResponses(id).catch(() => null),
        fetchBattleLineups(id).catch(() => null),
      ]);
      responseRows.value = responses?.rows || [];
      historicalLineups.value = battles?.rows || battles?.battles || [];
    }
    if (view.value === "meta") { meta.value = await fetchMetaHistory(); selectedMetaHeroId.value = selectedMetaHero.value?.hero_id || null; }
    if (view.value === "relationships") {
      const manifest = await fetchPatternManifest(id); patterns.value = await fetchVisualizationPatterns({ leagueId: id, relation: relation.value, context: "overall" });
      void manifest;
    }
    if (view.value === "rankings") rankings.value = await fetchPowerRankings(id, { cache: false });
    if (view.value === "pairs") { synergies.value = await fetchTeamSynergies({ leagueId: id }); if (!teamId.value) teamId.value = synergies.value?.teams?.[0]?.team_id || ""; }
    if (view.value === "schedule") { const payload = await fetchDailyMatches({ date: matchDate.value || undefined }); matches.value = payload?.matches || []; matchDate.value = payload?.date || matchDate.value; await Promise.all(matches.value.map(loadMatchVotes)); }
  } catch { error.value = tr("Could not load this workspace.", "无法加载此工作台。"); }
  finally { loading.value = false; finishStartupLoading(); }
}
onMounted(async () => { await loadSeasons(); await load(); });
watch([leagueId, view], load);
watch(relation, () => { if (view.value === "relationships") load(); });
watch(favoriteIds, (value) => setStored(favoriteStorageKey, JSON.stringify(value)), { deep: true });

function visitorId() {
  let id = getStored(visitorStorageKey);
  if (id) return id;
  id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  setStored(visitorStorageKey, id);
  return id;
}
function matchTeams(match) { return match?.teams || []; }
function matchBo(match) { return Number(match?.bo || 7); }
function predictionForm(match) {
  if (!matchForms.value[match.match_id]) matchForms.value[match.match_id] = { winner: "", losingScore: "" };
  return matchForms.value[match.match_id];
}
function scoreOptions(match) { return Array.from({ length: Math.ceil(matchBo(match) / 2) }, (_, value) => value); }
function predictionScore(match) {
  const form = predictionForm(match); const wins = Math.ceil(matchBo(match) / 2); const firstWins = String(form.winner) === String(matchTeams(match)[0]?.team_id);
  return form.losingScore === "" ? "—" : firstWins ? `${wins}–${form.losingScore}` : `${form.losingScore}–${wins}`;
}
function voteCount(match, team) { return matchVotes.value[match.match_id]?.votes_by_team?.[String(team?.team_id)] || 0; }
function totalVotes(match) { return matchVotes.value[match.match_id]?.total_votes || 0; }
async function loadMatchVotes(match) {
  try { matchVotes.value[match.match_id] = await fetchLiveWinnerPredictions({ leagueId: match.league_id, matchId: match.match_id, gameNumber: 0 }); } catch { matchVotes.value[match.match_id] = null; }
}
async function savePrediction(match) {
  const form = predictionForm(match); const teamsForMatch = matchTeams(match); const winner = teamsForMatch.find((team) => String(team.team_id) === String(form.winner));
  if (!winner || form.losingScore === "") return;
  const wins = Math.ceil(matchBo(match) / 2); const winnerIsFirst = String(winner.team_id) === String(teamsForMatch[0]?.team_id);
  predictionSaving.value = match.match_id; predictionNotice.value = "";
  try {
    await saveLiveWinnerPrediction({ leagueId: match.league_id, visitorId: visitorId(), matchId: match.match_id, gameNumber: 0, teamAId: teamsForMatch[0].team_id, teamBId: teamsForMatch[1].team_id, winnerTeamId: winner.team_id, bestOf: matchBo(match), teamAScore: winnerIsFirst ? wins : Number(form.losingScore), teamBScore: winnerIsFirst ? Number(form.losingScore) : wins });
    predictionNotice.value = tr("Prediction saved.", "预测已保存。"); await loadMatchVotes(match);
  } catch { predictionNotice.value = tr("Could not save this prediction.", "无法保存此预测。"); }
  finally { predictionSaving.value = ""; }
}

try { const storedFavorites = JSON.parse(getStored(favoriteStorageKey) || "[]"); if (Array.isArray(storedFavorites)) favoriteIds.value = storedFavorites.map(Number).slice(0, 12); } catch { favoriteIds.value = []; }
</script>

<template>
  <main class="new-atlas">
    <header class="na-topbar">
      <RouterLink class="na-brand" to="/new/plan/matchups">Draft Atlas <span>{{ tr('New', '新版') }}</span></RouterLink>
      <nav class="na-primary" :aria-label="tr('Primary navigation', '主导航')">
        <button v-for="(items, name) in displayedSections" :key="name" :class="{ active: section === name }" @click="go(name, items[0][0])">{{ tr(navigationLabels[name][0], navigationLabels[name][1]) }}</button>
      </nav>
      <div class="na-utilities"><select v-model="leagueId" :aria-label="tr('Competition', '赛事')"><option v-for="season in seasons" :key="season.league_id" :value="season.league_id">{{ season.year }} · {{ season.league_name }} · S{{ season.season }}</option></select><select v-model="language" :aria-label="tr('Language', '语言')"><option value="en">EN</option><option value="zh-CN">中文</option></select><RouterLink class="na-old-link" to="/">{{ tr('Old site', '原版网站') }}</RouterLink></div>
    </header>
    <div class="na-shell" :class="{ 'na-wide': section === 'draft' }">
      <aside class="na-subnav"><small>{{ tr(workspaceLabels[section][0], workspaceLabels[section][1]) }}</small><button v-for="item in displayedSections[section]" :key="item[0]" :class="{ active: view === item[0] }" @click="go(section, item[0])">{{ item[1] }}</button></aside>
      <section class="na-content">
        <header class="na-heading"><h1>{{ title[0] }}</h1><p>{{ title[1] }}</p></header>
        <p v-if="error" class="na-message error">{{ error }}</p><p v-else-if="loading" class="na-message">{{ tr('Loading published analysis…', '正在加载已发布分析…') }}</p>

        <template v-if="view === 'matchups'">
          <div class="na-split">
            <section class="na-panel na-stack">
              <div><h2>{{ tr('Matchup inputs', '对局输入') }}</h2><p class="na-muted">{{ tr('Use only information already visible in the draft.', '仅输入当前 BP 中已经可见的信息。') }}</p></div>
              <label>{{ tr('Opponent picks · up to 5', '敌方已选 · 最多 5 位') }}<input v-model="opponentSearch" :placeholder="tr('Find an opponent hero', '搜索敌方英雄')" type="search" /></label>
              <div v-if="opponentSearch" class="na-picker"><button v-for="hero in opponentOptions" :key="hero.hero_id" @click="addOpponent(hero)"><img v-if="heroIcon(hero)" :src="heroIcon(hero)" alt="" />{{ hero.hero_name }}</button></div>
              <div class="na-chips"><button v-for="hero in selectedOpponents" :key="hero.hero_id" @click="removeOpponent(hero.hero_id)">{{ hero.hero_name }} ×</button><span v-if="!selectedOpponents.length" class="na-muted">{{ tr('No opponents selected', '尚未选择敌方英雄') }}</span></div>
              <details class="na-disclosure" open><summary>{{ tr('Favorite pool · optional', '常用英雄池 · 可选') }}</summary><p>{{ tr('Favorites add a small style-fit signal and are saved on this device.', '常用英雄会提供少量风格匹配信号，并保存在本设备。') }}</p><input v-model="favoriteSearch" :placeholder="tr('Add a favorite hero', '添加常用英雄')" type="search" /><div v-if="favoriteSearch" class="na-picker"><button v-for="hero in favoriteOptions" :key="hero.hero_id" @click="addFavorite(hero)"><img v-if="heroIcon(hero)" :src="heroIcon(hero)" alt="" />{{ hero.hero_name }}</button></div><div class="na-chips"><button v-for="hero in selectedFavorites" :key="hero.hero_id" @click="removeFavorite(hero.hero_id)">{{ hero.hero_name }} ×</button></div></details>
              <label>{{ tr('Your lane', '你的分路') }}<select v-model="lane"><option value="">{{ tr('Any lane', '不限分路') }}</option><option value="clash">{{ tr('Clash', '对抗路') }}</option><option value="jungle">{{ tr('Jungle', '打野') }}</option><option value="mid">{{ tr('Mid', '中路') }}</option><option value="farm">{{ tr('Farm', '发育路') }}</option><option value="roam">{{ tr('Roam', '游走') }}</option></select></label>
              <button class="na-primary-button" :disabled="!opponentIds.length || recommendationLoading" @click="recommend">{{ recommendationLoading ? tr('Calculating…', '正在计算…') : tr('Recommend heroes', '推荐英雄') }}</button>
            </section>
            <section class="na-results">
              <div class="na-section-row"><div><h2>{{ recommendations ? tr('Recommended responses', '推荐应对') : tr('Your shortlist appears here', '你的候选列表将显示在这里') }}</h2><p class="na-muted">{{ recommendations ? tr(`${recommendations.methodology?.candidate_count || 0} legal candidates ranked`, `已对 ${recommendations.methodology?.candidate_count || 0} 位合法候选进行排序`) : tr('Add opponent picks to receive a real, evidence-based shortlist.', '添加敌方已选英雄，获取基于真实证据的候选列表。') }}</p></div></div>
              <div v-if="recommendations?.favorites?.length" class="na-favorite-ranks"><span>{{ tr('Your favorites in this matchup', '你的常用英雄在本对局中的排名') }}</span><button v-for="hero in recommendations.favorites" :key="hero.hero_id" @click="matchupEvidence = hero">#{{ hero.rank }} {{ hero.hero_name }}</button></div>
              <article v-for="hero in matchupRows" :key="hero.hero_id" class="na-matchup-result">
                <span class="na-rank">{{ hero.rank }}</span><img v-if="heroAsset(hero.hero_id)" :src="heroAsset(hero.hero_id)" alt="" />
                <div><strong>{{ hero.hero_name }}</strong><small>{{ laneLabel(hero.primary_lane || 'unknown') }}<template v-if="hero.is_favorite"> · {{ tr('Favorite', '常用') }}</template></small></div>
                <dl><div><dt>{{ tr('Fit', '综合适配') }}</dt><dd>{{ Number(hero.score).toFixed(1) }}</dd></div><div><dt>{{ tr('Evidence', '证据样本') }}</dt><dd>{{ number(hero.evidence_selections) }}</dd></div><div><dt>{{ tr('Style', '风格') }}</dt><dd>{{ percent(hero.style_similarity) }}</dd></div></dl>
                <button class="na-text-button" @click="matchupEvidence = hero">{{ tr('Why this hero?', '为什么推荐？') }}</button>
              </article>
              <button v-if="(recommendations?.recommendations?.length || 0) > 6" class="na-expand" @click="matchupExpanded = !matchupExpanded">{{ matchupExpanded ? tr('Show fewer', '收起') : tr('Show more recommendations', '查看更多推荐') }}</button>
              <aside v-if="matchupEvidence" class="na-evidence">
                <div class="na-section-row"><div><h3>{{ matchupEvidence.hero_name }} · {{ tr('evidence', '推荐依据') }}</h3><p>{{ tr('The score combines matchup evidence, favorite-pool similarity, and season usage.', '综合分结合了对局证据、常用英雄风格相似度与赛季使用情况。') }}</p></div><button :aria-label="tr('Close', '关闭')" @click="matchupEvidence = null">×</button></div>
                <div class="na-evidence-metrics"><div><span>{{ tr('Matchup score', '对局得分') }}</span><strong>{{ Number(matchupEvidence.matchup_score).toFixed(1) }}</strong></div><div><span>{{ tr('Style similarity', '风格相似度') }}</span><strong>{{ percent(matchupEvidence.style_similarity) }}</strong></div><div><span>{{ tr('Evidence selections', '证据样本') }}</span><strong>{{ number(matchupEvidence.evidence_selections) }}</strong></div></div>
                <div v-if="matchupEvidence.opponent_evidence?.length" class="na-tablewrap"><table><thead><tr><th>{{ tr('Against', '对阵') }}</th><th>{{ tr('Selections', '选择次数') }}</th><th>{{ tr('Smoothed lift', '平滑提升') }}</th><th>{{ tr('Battle win rate', '小局胜率') }}</th></tr></thead><tbody><tr v-for="evidence in matchupEvidence.opponent_evidence" :key="evidence.opponent_hero_id"><td>{{ evidence.opponent_hero_name }}</td><td>{{ number(evidence.selections) }}</td><td>{{ number(evidence.smoothed_lift, 2) }}×</td><td>{{ percent(evidence.battle_win_rate) }}</td></tr></tbody></table></div>
              </aside>
              <p class="na-note">{{ tr('Historical draft responses are a reference, not a guaranteed in-game counter. Sparse evidence is shown instead of being invented.', '历史 BP 应对仅供参考，不代表必然的对局克制；样本不足时会如实显示，不会补造数据。') }}</p>
            </section>
          </div>
        </template>

        <template v-else-if="view === 'atlas'">
          <div class="na-atlas">
            <div class="na-space-column">
              <div class="na-toolbar"><label>{{ tr('Find a hero', '搜索英雄') }}<input v-model="opponentSearch" type="search" :placeholder="tr('Search the feature space', '搜索特征空间')" /></label><div class="na-toolbar-actions"><button :class="{ active: !atlasShowAll }" @click="atlasShowAll = false">{{ tr('Frequent', '常用') }}</button><button :class="{ active: atlasShowAll }" @click="atlasShowAll = true">{{ tr('All', '全部') }}</button><button :aria-label="tr('Zoom out', '缩小')" @click="atlasZoom = Math.max(.7, atlasZoom - .15)">−</button><button :aria-label="tr('Reset zoom', '重置缩放')" @click="atlasZoom = 1">{{ number(atlasZoom, 1) }}×</button><button :aria-label="tr('Zoom in', '放大')" @click="atlasZoom = Math.min(1.75, atlasZoom + .15)">+</button></div></div>
              <div class="na-space-frame">
                <svg class="na-space" viewBox="0 0 800 440" role="img" :aria-label="tr('Learned hero similarity map', '学习得到的英雄相似性图谱')">
                  <path d="M38 220H762M400 38V402" />
                  <g :style="{ transform: `scale(${atlasZoom})`, transformOrigin: 'center' }">
                    <g v-for="hero in atlasOptions" :key="hero.hero_id" class="na-space-node" :class="{ active: Number(atlasHero?.hero_id) === Number(hero.hero_id) }" tabindex="0" role="button" @click="atlasHeroId = hero.hero_id" @keydown.enter="atlasHeroId = hero.hero_id">
                      <circle :cx="atlasX(hero.x)" :cy="atlasY(hero.y)" :r="Number(atlasHero?.hero_id) === Number(hero.hero_id) ? 20 : 15" />
                      <image v-if="heroIcon(hero)" :href="heroIcon(hero)" :x="atlasX(hero.x) - 13" :y="atlasY(hero.y) - 13" width="26" height="26" preserveAspectRatio="xMidYMid slice" />
                      <title>{{ hero.hero_name }} · {{ laneLabel(hero.primary_lane || 'unknown') }}</title>
                    </g>
                  </g>
                </svg>
                <div class="na-space-legend"><span>{{ tr('Each point is a hero', '每个点代表一位英雄') }}</span><span>{{ tr('Closer means more similar in learned draft and mechanic features', '距离越近，学习到的 BP 与机制特征越相似') }}</span></div>
              </div>
            </div>
            <aside v-if="atlasHero" class="na-panel na-atlas-detail">
              <div class="na-atlas-identity"><img v-if="heroIcon(atlasHero)" :src="heroIcon(atlasHero)" alt="" /><div><h2>{{ atlasHero.hero_name }}</h2><p>{{ tr('Primary lane', '主要分路') }} · {{ laneLabel(atlasHero.primary_lane || 'unknown') }}</p></div></div>
              <h3>{{ tr('Nearest heroes in the learned space', '学习空间中最相近的英雄') }}</h3>
              <div class="na-neighbours"><button v-for="hero in atlasNeighbours" :key="hero.hero_id" @click="atlasHeroId = hero.hero_id"><img v-if="heroIcon(hero)" :src="heroIcon(hero)" alt="" /><span>{{ hero.hero_name }}</span></button></div>
              <dl class="na-definition"><div><dt>{{ tr('Professional BP actions', '职业 BP 操作') }}</dt><dd>{{ number(atlasHero.weighted_bp_action_count || atlasHero.bp_action_count) }}</dd></div><div><dt>{{ tr('Mechanic dimensions', '机制维度') }}</dt><dd>{{ (atlasHero.gameplay_mechanic_keys || []).length }}</dd></div></dl>
              <div v-if="atlasHero.gameplay_mechanic_keys?.length" class="na-tags"><span v-for="key in atlasHero.gameplay_mechanic_keys" :key="key">{{ mechanicLabel(key) }}</span></div>
              <p class="na-note">{{ tr('Similarity describes learned draft and mechanic patterns; it does not claim identical gameplay.', '相似性描述学习到的 BP 与机制模式，不代表玩法完全相同。') }}</p>
            </aside>
          </div>
        </template>
        <template v-else-if="view === 'lineups'"><LineupAnalyzerWidget class="na-embedded-widget" :league-id="leagueId" :heroes="heroes" :response-rows="responseRows" :historical-lineups="historicalLineups" /></template>
        <template v-else-if="view === 'workspace'"><DraftSimulatorPage class="na-embedded-widget na-draft-widget" /></template>
        <template v-else-if="view === 'meta'">
          <div class="na-meta">
            <section><div class="na-section-row"><div><h2>{{ tr('Opening priorities', '开局优先级') }}</h2><p class="na-muted">{{ tr('First bans and Blue first picks', '首轮禁用与蓝色方一选') }}</p></div><button class="na-text-button" @click="metaExpanded = !metaExpanded">{{ metaExpanded ? tr('Show top 5', '显示前 5') : tr('Explore top 12', '查看前 12') }}</button></div><article v-for="hero in metaHeroes" :key="hero.hero_id" class="na-ranked" :class="{ active: Number(selectedMetaHero?.hero_id) === Number(hero.hero_id) }" @click="selectedMetaHeroId = hero.hero_id"><img v-if="heroAsset(hero.hero_id)" :src="heroAsset(hero.hero_id)" alt="" /><strong>#{{ hero.priority_rank }} {{ hero.hero_name }}</strong><span>{{ percent(hero.early_priority_rate) }}</span></article></section>
            <section class="na-panel"><div class="na-section-row"><div><h2>{{ tr('Meta evolution', '版本演变') }}</h2><p>{{ tr('Select a hero to compare its opening priority across available competitions.', '选择英雄，比较其在现有赛事中的开局优先率。') }}</p></div><label>{{ tr('Hero', '英雄') }}<select v-model.number="selectedMetaHeroId"><option v-for="hero in allMetaHeroes" :key="hero.hero_id" :value="hero.hero_id">{{ hero.hero_name }}</option></select></label></div>
              <div v-if="metaPoints.length" class="na-chart-wrap"><svg class="na-chart" viewBox="0 0 600 200" role="img" :aria-label="tr('Opening-priority trend', '开局优先率趋势')"><path class="na-chart-axis" d="M38 34V166H562"/><polyline :points="metaPolyline"/><g v-for="point in metaPoints" :key="point.season?.league_id"><circle :cx="point.x" :cy="point.y" r="5"><title>{{ point.season?.year }} · {{ point.season?.league_name }} · S{{ point.season?.season }}: {{ percent(point.hero.early_priority_rate) }}</title></circle><text :x="point.x" y="187" text-anchor="middle">{{ point.season?.year }} S{{ point.season?.season }}</text></g></svg><p class="na-chart-caption">{{ selectedMetaHero?.hero_name }} · {{ tr('highest visible rate', '当前可见最高值') }} {{ percent(metaMaximum) }}</p></div>
              <div class="na-tablewrap"><table><thead><tr><th>{{ tr('Competition', '赛事') }}</th><th>{{ tr('Priority', '优先率') }}</th><th>{{ tr('Count', '次数') }}</th><th>{{ tr('Rank', '排名') }}</th></tr></thead><tbody><tr v-for="row in metaTrend" :key="row.season?.league_id"><td>{{ row.season?.year }} · {{ row.season?.league_name }} · S{{ row.season?.season }}</td><td>{{ percent(row.hero.early_priority_rate) }}</td><td>{{ number(row.hero.early_priority_count) }}</td><td>#{{ row.hero.priority_rank }}</td></tr></tbody></table></div>
            </section>
          </div>
        </template>
        <template v-else-if="view === 'relationships'">
          <div class="na-tabs"><button v-for="option in relationOptions" :key="option[0]" :class="{ active: relation === option[0] }" @click="relation = option[0]">{{ option[1] }}</button></div>
          <div class="na-filters"><label>{{ tr('Find a hero', '搜索英雄') }}<input v-model="relationSearch" type="search" :placeholder="tr('Source or response', '发起英雄或应对英雄')" /></label><label>{{ tr('Rank by', '排序指标') }}<select v-model="relationMetric"><option value="smoothed_lift">{{ tr('Smoothed lift', '平滑提升') }}</option><option value="smoothed_probability">{{ tr('Response chance', '应对概率') }}</option><option value="selections">{{ tr('Selections', '选择次数') }}</option><option value="win_rate">{{ tr('Win rate', '胜率') }}</option></select></label><label>{{ tr('Minimum evidence', '最少证据') }}<input v-model.number="relationSupport" min="1" type="number" /></label><label>{{ tr('Rows', '显示数量') }}<select v-model.number="relationLimit"><option :value="10">10</option><option :value="20">20</option><option :value="50">50</option></select></label></div>
          <p class="na-result-count">{{ relationRows.length }} {{ tr('relationships shown', '条关系') }}</p>
          <div class="na-tablewrap"><table><thead><tr><th>{{ tr('Pattern', '关系') }}</th><th>{{ tr('Chosen / legal', '选择 / 合法机会') }}</th><th>{{ tr('Chance', '概率') }}</th><th>{{ tr('Lift', '提升倍数') }}</th><th>{{ tr('Win rate', '胜率') }}</th><th></th></tr></thead><tbody><tr v-for="row in relationRows" :key="row.id || `${row.source_hero_id}-${row.target_hero_id}`"><td><strong>{{ row.source_hero_name }} → {{ row.target_hero_name }}</strong></td><td>{{ number(row.selections) }} / {{ number(row.opportunities) }}</td><td>{{ percent(row.smoothed_probability) }}</td><td>{{ number(row.smoothed_lift, 2) }}×</td><td>{{ percent(row.win_rate) }}</td><td><button class="na-text-button" @click="relationEvidence = row">{{ tr('Inspect', '查看') }}</button></td></tr></tbody></table></div>
          <aside v-if="relationEvidence" class="na-evidence"><div class="na-section-row"><div><h3>{{ relationEvidence.source_hero_name }} → {{ relationEvidence.target_hero_name }}</h3><p>{{ tr('Observed relationship in legal draft opportunities.', '在合法 BP 机会中观测到的关系。') }}</p></div><button :aria-label="tr('Close', '关闭')" @click="relationEvidence = null">×</button></div><div class="na-evidence-metrics"><div><span>{{ tr('Selections / legal', '选择 / 合法机会') }}</span><strong>{{ number(relationEvidence.selections) }} / {{ number(relationEvidence.opportunities) }}</strong></div><div><span>{{ tr('Smoothed chance', '平滑概率') }}</span><strong>{{ percent(relationEvidence.smoothed_probability) }}</strong></div><div><span>{{ tr('Usual chance', '通常概率') }}</span><strong>{{ percent(relationEvidence.baseline_probability) }}</strong></div><div><span>{{ tr('Likely range', '可能范围') }}</span><strong>{{ percent(relationEvidence.ci_low) }}–{{ percent(relationEvidence.ci_high) }}</strong></div><div><span>{{ tr('Lift', '提升倍数') }}</span><strong>{{ number(relationEvidence.smoothed_lift, 2) }}×</strong></div><div><span>{{ tr('Battle win rate', '小局胜率') }}</span><strong>{{ percent(relationEvidence.win_rate) }}</strong></div></div></aside>
          <p class="na-note">{{ tr('Rates only count decisions where the target hero was legal. These are associations, not proof of causation.', '统计仅计算目标英雄仍合法时的决策。这些是相关性，不代表因果关系。') }}</p>
        </template>
        <template v-else-if="view === 'rankings'"><RankingsPage class="na-embedded-widget na-rankings-widget" /></template>
        <template v-else-if="view === 'pairs'">
          <div class="na-filters"><label>{{ tr('Team', '战队') }}<select v-model="teamId"><option v-for="team in teams" :key="team.team_id" :value="team.team_id">{{ team.team_name }}</option></select></label><label>{{ tr('Find a hero', '搜索英雄') }}<input v-model="pairSearch" type="search" :placeholder="tr('Hero in the pair', '组合中的英雄')" /></label><label>{{ tr('Rank by', '排序指标') }}<select v-model="pairMetric"><option value="selection_count">{{ tr('Uses', '使用次数') }}</option><option value="smoothed_completion_probability">{{ tr('Completion chance', '完成概率') }}</option><option value="smoothed_lift">{{ tr('Smoothed lift', '平滑提升') }}</option><option value="battle_win_rate_when_paired">{{ tr('Win rate', '胜率') }}</option></select></label><label>{{ tr('Minimum uses', '最少使用') }}<input v-model.number="pairSupport" min="1" type="number" /></label><label>{{ tr('Rows', '显示数量') }}<select v-model.number="pairLimit"><option :value="10">10</option><option :value="20">20</option><option :value="50">50</option></select></label></div>
          <section v-if="activeTeam" class="na-team-head"><div><h2>{{ activeTeam.team_name }}</h2><p>{{ number(activeTeam.battle_count) }} {{ tr('battles', '局比赛') }} · {{ number(activeTeam.pair_count) }} {{ tr('eligible pairs', '有效组合') }}</p></div><span>{{ pairRows.length }} {{ tr('shown', '条结果') }}</span></section>
          <div class="na-tablewrap"><table><thead><tr><th>{{ tr('Pair', '组合') }}</th><th>{{ tr('Uses / legal', '使用 / 合法机会') }}</th><th>{{ tr('Completion', '完成概率') }}</th><th>{{ tr('Lift', '提升倍数') }}</th><th>{{ tr('Win rate', '胜率') }}</th><th></th></tr></thead><tbody><tr v-for="row in pairRows" :key="row.pair_name"><td><strong>{{ row.pair_name }}</strong></td><td>{{ number(row.selection_count) }} / {{ number(row.legal_completion_opportunity_count) }}</td><td>{{ percent(row.smoothed_completion_probability) }}</td><td>{{ number(row.smoothed_lift, 2) }}×</td><td>{{ percent(row.battle_win_rate_when_paired) }}</td><td><button class="na-text-button" @click="pairEvidence = row">{{ tr('Inspect', '查看') }}</button></td></tr></tbody></table></div>
          <aside v-if="pairEvidence" class="na-evidence"><div class="na-section-row"><div><h3>{{ pairEvidence.pair_name }}</h3><p>{{ activeTeam?.team_name }} · {{ tr('pair evidence', '组合证据') }}</p></div><button :aria-label="tr('Close', '关闭')" @click="pairEvidence = null">×</button></div><div class="na-evidence-metrics"><div><span>{{ tr('Uses / legal', '使用 / 合法机会') }}</span><strong>{{ number(pairEvidence.selection_count) }} / {{ number(pairEvidence.legal_completion_opportunity_count) }}</strong></div><div><span>{{ tr('Completion chance', '完成概率') }}</span><strong>{{ percent(pairEvidence.smoothed_completion_probability) }}</strong></div><div><span>{{ tr('Usual chance', '通常概率') }}</span><strong>{{ percent(pairEvidence.team_baseline_completion_probability) }}</strong></div><div><span>{{ tr('Likely range', '可能范围') }}</span><strong>{{ percent(pairEvidence.probability_ci95_low) }}–{{ percent(pairEvidence.probability_ci95_high) }}</strong></div><div><span>{{ tr('Smoothed lift', '平滑提升') }}</span><strong>{{ number(pairEvidence.smoothed_lift, 2) }}×</strong></div><div><span>{{ tr('Battle win rate', '小局胜率') }}</span><strong>{{ percent(pairEvidence.battle_win_rate_when_paired) }}</strong></div></div></aside>
          <p class="na-note">{{ tr('A pair is counted only when the partner remained legally available. Pair preference does not establish causation.', '仅当搭档英雄仍合法时才计入组合。组合偏好不代表因果关系。') }}</p>
        </template>
        <template v-else-if="view === 'schedule'">
          <div class="na-match-controls"><button @click="matchDate = ''; load()">{{ tr('Today', '今天') }}</button><label>{{ tr('Date', '日期') }}<input v-model="matchDate" type="date" @change="load" /></label></div>
          <section v-for="match in matches" :key="match.match_id" class="na-fixture">
            <div class="na-fixture-head"><div><small>{{ match.league_name }} · {{ match.start_time || tr('Scheduled time unavailable', '开赛时间暂不可用') }} · {{ tr(`BO${matchBo(match)}`, `${matchBo(match)} 局 ${Math.ceil(matchBo(match) / 2)} 胜`) }}</small><strong>{{ matchTeams(match)[0]?.team_name || '—' }} <span>{{ tr('vs', '对阵') }}</span> {{ matchTeams(match)[1]?.team_name || '—' }}</strong></div><RouterLink class="na-inline-link" to="/new/draft/workspace">{{ tr('Open in Draft', '在 BP 工作台中打开') }} →</RouterLink></div>
            <div class="na-prediction"><div><h3>{{ tr('Your prediction', '你的预测') }}</h3><p>{{ tr('Choose the winner and exact series score.', '选择胜者和系列赛准确比分。') }}</p></div><label>{{ tr('Winner', '胜者') }}<select v-model="predictionForm(match).winner"><option value="">{{ tr('Select a team', '选择战队') }}</option><option v-for="team in matchTeams(match)" :key="team.team_id" :value="String(team.team_id)">{{ team.team_name }}</option></select></label><label>{{ tr('Losing score', '败方局数') }}<select v-model="predictionForm(match).losingScore" :disabled="!predictionForm(match).winner"><option value="">—</option><option v-for="score in scoreOptions(match)" :key="score" :value="String(score)">{{ score }}</option></select></label><div class="na-score-preview"><span>{{ tr('Exact score', '准确比分') }}</span><strong>{{ predictionScore(match) }}</strong></div><button class="na-primary-button" :disabled="predictionSaving === match.match_id || !predictionForm(match).winner || predictionForm(match).losingScore === ''" @click="savePrediction(match)">{{ predictionSaving === match.match_id ? tr('Saving…', '保存中…') : tr('Save prediction', '保存预测') }}</button></div>
            <div class="na-votes"><span>{{ tr('Community predictions', '社区预测') }} · {{ number(totalVotes(match)) }} {{ tr('votes', '票') }}</span><div v-for="team in matchTeams(match)" :key="team.team_id"><strong>{{ team.team_name }}</strong><span>{{ number(voteCount(match, team)) }}</span></div></div>
          </section>
          <p v-if="predictionNotice" class="na-message">{{ predictionNotice }}</p><p v-if="!matches.length && !loading" class="na-empty-copy">{{ tr('No supported fixtures are available for this date.', '此日期没有可用赛程。') }}</p>
        </template>
        <template v-else-if="view === 'methods'"><MethodologyPage class="na-embedded-widget na-methodology-widget" /></template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.new-atlas{--paper:#fff;--ground:#f5f5f3;--ink:#252525;--ink-soft:#646461;--muted:#646461;--line:#d2d2cd;--panel:#fff;--accent:#252525;--accent-deep:#111;min-height:100vh;background:var(--paper);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.new-atlas *{box-sizing:border-box}.na-topbar{display:flex;align-items:center;gap:22px;padding:18px 24px;border-bottom:1px solid var(--line);background:var(--paper)}.na-brand{color:inherit;text-decoration:none;font-size:18px;font-weight:700;white-space:nowrap}.na-brand span{font-size:11px;font-weight:500;color:var(--muted);margin-left:4px}.na-primary{display:flex;gap:4px;flex:1}.na-primary button,.na-subnav button,.na-tabs button,.na-match-controls button{border:0;background:transparent;color:inherit;padding:8px 10px;cursor:pointer;text-transform:capitalize}.na-primary button.active,.na-subnav button.active,.na-tabs button.active{background:var(--ink);color:var(--paper)}.na-utilities{display:flex;gap:8px;align-items:center}.na-utilities select,.na-filter select,.na-content input,.na-content select{border:1px solid var(--line);border-radius:4px;background:var(--paper);padding:8px;color:inherit;font:inherit}.na-old-link,.na-inline-link{color:inherit;text-underline-offset:3px}.na-shell{display:grid;grid-template-columns:160px minmax(0,1fr);min-height:calc(100vh - 73px)}.na-subnav{background:var(--ground);border-right:1px solid var(--line);padding:24px 12px}.na-subnav small{display:block;color:var(--muted);margin:0 8px 12px;text-transform:capitalize}.na-subnav button{width:100%;text-align:left;margin-bottom:4px}.na-content{max-width:1280px;width:100%;padding:30px;margin:0 auto}.na-heading{margin-bottom:26px}.na-heading h1{font-size:28px;letter-spacing:-.03em;line-height:1.15;margin:0 0 8px}.na-heading p,.na-muted,.na-note{color:var(--muted)}.na-message{padding:12px 0}.na-message.error{color:#9b3030}.na-split,.na-atlas,.na-meta{display:grid;grid-template-columns:minmax(250px,.82fr) minmax(0,1.4fr);gap:28px}.na-panel{border:1px solid var(--line);padding:18px;background:var(--paper)}.na-stack{display:grid;gap:16px;align-content:start}.na-stack h2,.na-results h2,.na-panel h2,.na-meta h2,.na-empty h2,.na-team-head h2{font-size:18px;margin:0 0 7px}.na-content label{display:grid;gap:6px;font-weight:600}.na-content input,.na-content select{width:100%;font-weight:400}.na-primary-button{display:inline-block;border:1px solid var(--ink);background:var(--ink);color:white;padding:10px 14px;border-radius:4px;text-decoration:none;text-align:center;cursor:pointer}.na-primary-button:disabled{opacity:.5}.na-picker{border:1px solid var(--line);max-height:210px;overflow:auto}.na-picker button{display:flex;align-items:center;gap:8px;width:100%;background:var(--paper);border:0;border-bottom:1px solid var(--line);padding:7px;text-align:left;cursor:pointer}.na-picker img,.na-result img,.na-ranked img{width:32px;height:32px;object-fit:cover;border:1px solid var(--line)}.na-chips{display:flex;flex-wrap:wrap;gap:6px}.na-chips button{border:1px solid var(--line);border-radius:4px;background:var(--ground);padding:6px 8px;cursor:pointer}.na-results{min-width:0}.na-result{display:grid;grid-template-columns:24px 38px 1fr auto;align-items:center;gap:10px;padding:13px 0;border-bottom:1px solid var(--line)}.na-result small,.na-fixture small{display:block;color:var(--muted)}.na-hero-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:16px}.na-hero-grid button{min-height:78px;text-align:left;background:var(--ground);border:1px solid var(--line);padding:8px;cursor:pointer}.na-hero-grid button.active{background:var(--ink);color:var(--paper)}.na-hero-grid img{width:28px;height:28px;object-fit:cover;float:left;margin-right:7px}.na-hero-grid small{display:block;color:var(--muted);clear:both}.na-hero-grid button.active small{color:#d6d6d2}.na-atlas-detail{position:sticky;top:16px}.na-atlas-identity{display:flex;gap:12px;align-items:center;margin-bottom:20px}.na-atlas-identity img{width:56px;height:56px;object-fit:cover}.na-atlas-identity p{margin:2px 0;color:var(--muted)}.na-atlas-detail h3{font-size:13px;margin:20px 0 10px}.na-neighbours{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.na-neighbours button{display:flex;gap:7px;align-items:center;border:1px solid var(--line);background:var(--ground);padding:7px;text-align:left;cursor:pointer}.na-neighbours img{width:28px;height:28px;object-fit:cover}.na-definition{margin:22px 0 0}.na-definition div{display:flex;justify-content:space-between;gap:12px;border-top:1px solid var(--line);padding:10px 0}.na-definition dt{color:var(--muted)}.na-definition dd{margin:0;font-weight:700}.na-empty{max-width:650px;border:1px solid var(--line);padding:24px;background:var(--ground)}.na-empty p{color:var(--muted);margin:0 0 18px}.na-ranked{display:grid;grid-template-columns:38px 1fr auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line);cursor:pointer}.na-ranked.active{background:var(--ground);box-shadow:inset 3px 0 var(--ink)}.na-tabs{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:16px}.na-tabs button{border:1px solid var(--line);border-radius:4px}.na-tabs button:disabled{color:var(--muted);cursor:not-allowed}.na-tablewrap{overflow:auto;border-top:1px solid var(--line)}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:12px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{font-weight:500;color:var(--muted)}.na-filter{max-width:330px;margin-bottom:18px}.na-team-head{display:flex;justify-content:space-between;align-items:end;padding:15px 0}.na-team-head p{color:var(--muted);margin:0}.na-match-controls{display:flex;gap:10px;align-items:end;margin-bottom:20px}.na-match-controls label{width:190px}.na-fixture{display:block;padding:20px;border:1px solid var(--line);margin-bottom:14px}.na-empty-copy{color:var(--muted)}
.na-section-row{display:flex;justify-content:space-between;align-items:start;gap:16px}.na-section-row p{margin:3px 0;color:var(--muted)}.na-disclosure{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:12px 0}.na-disclosure summary{cursor:pointer;font-weight:700}.na-disclosure p{margin:6px 0 10px;color:var(--muted);font-size:12px}.na-favorite-ranks{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:10px;background:var(--ground);margin:12px 0}.na-favorite-ranks span{font-size:12px;color:var(--muted);margin-right:auto}.na-favorite-ranks button,.na-expand,.na-text-button,.na-toolbar-actions button{border:1px solid var(--line);background:var(--paper);padding:6px 8px;color:inherit;cursor:pointer;border-radius:3px}.na-matchup-result{display:grid;grid-template-columns:25px 38px minmax(100px,1fr) minmax(235px,1.1fr) auto;gap:10px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)}.na-matchup-result img{width:36px;height:36px;object-fit:cover}.na-matchup-result small{display:block;color:var(--muted)}.na-matchup-result dl{display:grid;grid-template-columns:repeat(3,1fr);margin:0;gap:10px}.na-matchup-result dl div{display:grid}.na-matchup-result dt{font-size:11px;color:var(--muted)}.na-matchup-result dd{margin:0;font-weight:700}.na-rank{font-variant-numeric:tabular-nums;color:var(--muted)}.na-expand{width:100%;margin-top:12px}.na-evidence{border:1px solid var(--ink);background:var(--ground);padding:16px;margin-top:14px}.na-evidence h3{margin:0;font-size:16px}.na-evidence .na-section-row>button{border:0;background:none;font-size:20px;cursor:pointer}.na-evidence-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin:14px 0}.na-evidence-metrics div{background:var(--paper);padding:10px;display:grid}.na-evidence-metrics span{font-size:11px;color:var(--muted)}.na-evidence-metrics strong{font-size:16px}.na-toolbar{display:flex;justify-content:space-between;align-items:end;gap:12px;margin-bottom:10px}.na-toolbar>label{flex:1}.na-toolbar-actions{display:flex;gap:3px}.na-toolbar-actions button.active{background:var(--ink);color:var(--paper)}.na-space-frame{border:1px solid var(--line);background:var(--ground);overflow:hidden}.na-space{display:block;width:100%;min-height:440px}.na-space>path{stroke:var(--line);stroke-width:1;stroke-dasharray:3 5}.na-space-node{cursor:pointer}.na-space-node circle{fill:var(--paper);stroke:#8a8a85;stroke-width:1}.na-space-node.active circle{stroke:var(--ink);stroke-width:4}.na-space-node image{clip-path:circle(13px at center)}.na-space-legend{display:flex;justify-content:space-between;gap:12px;border-top:1px solid var(--line);padding:8px 11px;font-size:11px;color:var(--muted)}.na-tags{display:flex;flex-wrap:wrap;gap:5px}.na-tags span{border:1px solid var(--line);background:var(--ground);padding:3px 6px;font-size:11px}.na-chart-wrap{margin:15px 0}.na-chart{width:100%;height:auto;display:block;background:var(--ground)}.na-chart-axis{stroke:var(--line);fill:none}.na-chart polyline{fill:none;stroke:var(--ink);stroke-width:2}.na-chart circle{fill:var(--paper);stroke:var(--ink);stroke-width:2}.na-chart text{font-size:9px;fill:var(--muted)}.na-chart-caption{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}.na-filters{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px;align-items:end;margin-bottom:10px}.na-filters label:first-child:nth-last-child(5){grid-column:span 1}.na-result-count{color:var(--muted);font-size:12px;margin:8px 0}.na-fixture-head{display:flex;justify-content:space-between;gap:20px;align-items:center}.na-fixture-head strong{display:block;font-size:18px;margin-top:4px}.na-fixture-head strong span{font-weight:400;color:var(--muted);font-size:12px;margin:0 6px}.na-prediction{display:grid;grid-template-columns:minmax(150px,1fr) repeat(2,minmax(120px,.7fr)) 90px auto;align-items:end;gap:12px;border-top:1px solid var(--line);margin-top:16px;padding-top:16px}.na-prediction h3{margin:0}.na-prediction p{margin:2px 0;color:var(--muted);font-size:12px}.na-score-preview{display:grid}.na-score-preview span{font-size:12px;color:var(--muted)}.na-score-preview strong{font-size:20px}.na-votes{display:flex;gap:15px;align-items:center;justify-content:flex-end;margin-top:14px;color:var(--muted);font-size:12px}.na-votes div{display:flex;gap:8px}.na-votes div span{font-variant-numeric:tabular-nums;color:var(--ink)}

:deep(.na-embedded-widget){width:100%;max-width:none!important;margin:0!important;padding:0!important;background:transparent!important;color:var(--ink)!important;font-family:inherit!important}
:deep(.na-embedded-widget > header),:deep(.na-draft-widget > .simulator-hero),:deep(.na-rankings-widget > .rankings-hero),:deep(.na-methodology-widget > .page-header){display:none!important}
:deep(.na-embedded-widget button),:deep(.na-embedded-widget input),:deep(.na-embedded-widget select){border-radius:4px!important;box-shadow:none!important;font-family:inherit!important}
:deep(.na-embedded-widget article),:deep(.na-embedded-widget section),:deep(.na-embedded-widget aside){box-shadow:none!important}
:deep(.na-embedded-widget .lineup-side),:deep(.na-embedded-widget .lineup-picker),:deep(.na-embedded-widget .lineup-analysis),:deep(.na-embedded-widget .ultimate-results),:deep(.na-embedded-widget .global-bp-panel),:deep(.na-embedded-widget .draft-board),:deep(.na-embedded-widget .hero-picker),:deep(.na-embedded-widget .forecast-panel),:deep(.na-embedded-widget .recommendation-panel),:deep(.na-embedded-widget .lineup-score-panel),:deep(.na-embedded-widget .process-demo),:deep(.na-embedded-widget .ranking-table-card),:deep(.na-embedded-widget .player-board-card),:deep(.na-embedded-widget .hero-directory),:deep(.na-embedded-widget .hero-detail){border:1px solid var(--line)!important;border-radius:4px!important;background:var(--paper)!important;box-shadow:none!important}
:deep(.na-embedded-widget .lineup-side.active),:deep(.na-embedded-widget .recommendation-choice:hover),:deep(.na-embedded-widget .hero-options button:hover),:deep(.na-embedded-widget .lineup-hero-options button:hover){border-color:var(--ink)!important}
:deep(.na-embedded-widget .lineup-boards),:deep(.na-embedded-widget .simulator-layout),:deep(.na-embedded-widget .methodology-layout){gap:18px!important}
:deep(.na-embedded-widget .lineup-picker),:deep(.na-embedded-widget .hero-picker){background:var(--ground)!important}
:deep(.na-embedded-widget .lineup-picker img),:deep(.na-embedded-widget .hero-picker img){border-radius:2px!important}
:deep(.na-embedded-widget .board-switch){border:1px solid var(--line)!important;border-radius:4px!important;background:var(--ground)!important;box-shadow:none!important}
:deep(.na-embedded-widget .board-switch button.active),:deep(.na-embedded-widget .position-tabs button.active){background:var(--ink)!important;color:var(--paper)!important;border-color:var(--ink)!important}
:deep(.na-embedded-widget .podium article){border:1px solid var(--line)!important;border-radius:4px!important;background:var(--ground)!important;box-shadow:none!important;transform:none!important}
:deep(.na-rankings-widget .podium article::after){display:none!important}
:deep(.na-rankings-widget .team-monogram){border:1px solid var(--line)!important;border-radius:3px!important;background:var(--paper)!important;color:var(--ink)!important}
:deep(.na-rankings-widget .podium article>strong),:deep(.na-rankings-widget .podium h3),:deep(.na-rankings-widget .board-switch strong){font-family:inherit!important}
:deep(.na-rankings-widget .score-cell i),:deep(.na-rankings-widget .player-identity i){background:var(--ink)!important}
:deep(.na-embedded-widget .section-nav){border-right:1px solid var(--line)!important;background:var(--ground)!important}
:deep(.na-embedded-widget .section-nav button.active){background:var(--ink)!important;color:var(--paper)!important}
:deep(.na-draft-widget .simulator-workspace){border:0!important;background:transparent!important;box-shadow:none!important;padding:0!important}
:deep(.na-draft-widget .simulator-layout){grid-template-columns:minmax(0,1fr) 300px!important}
:deep(.na-draft-widget .coach-panel){border:1px solid var(--line)!important;border-radius:4px!important;background:var(--paper)!important;box-shadow:none!important}
:deep(.na-draft-widget .coach-header){border-bottom:1px solid var(--line)!important;background:var(--ink)!important;color:var(--paper)!important}
:deep(.na-draft-widget .coach-thread){background:var(--ground)!important}
:deep(.na-draft-widget .coach-mark){border-radius:3px!important;background:var(--ink)!important;color:var(--paper)!important}
:deep(.na-draft-widget .scout-suggestion),:deep(.na-draft-widget .composer-scout){border-color:var(--line)!important;background:var(--paper)!important;color:var(--ink)!important}
:deep(.na-draft-widget .assistant-message),:deep(.na-draft-widget .coach-answer-box),:deep(.na-draft-widget .coach-form textarea){border-radius:3px!important;background:var(--paper)!important}
:deep(.na-methodology-widget .methodology-layout){grid-template-columns:210px minmax(0,1fr)!important}
@media(max-width:800px){.na-topbar{padding:14px;flex-wrap:wrap;gap:12px}.na-primary{order:3;flex-basis:100%;overflow:auto}.na-primary button{flex:1;white-space:nowrap}.na-utilities{margin-left:auto}.na-utilities select:first-child{max-width:145px}.na-shell{grid-template-columns:1fr}.na-subnav{display:flex;gap:4px;overflow:auto;padding:10px 14px;border-right:0;border-bottom:1px solid var(--line)}.na-subnav small{display:none}.na-subnav button{width:auto;white-space:nowrap;margin:0}.na-content{padding:22px 15px}.na-split,.na-atlas,.na-meta{grid-template-columns:1fr;gap:20px}.na-heading h1{font-size:24px}.na-toolbar,.na-section-row,.na-fixture-head{align-items:stretch;flex-direction:column}.na-toolbar-actions{overflow:auto}.na-space{min-height:330px}.na-space-legend{flex-direction:column}.na-atlas-detail{position:static}.na-matchup-result{grid-template-columns:25px 38px 1fr auto}.na-matchup-result dl{grid-column:2/-1}.na-matchup-result .na-text-button{grid-column:2/-1}.na-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.na-prediction{grid-template-columns:1fr 1fr}.na-prediction>div:first-child,.na-prediction>.na-primary-button{grid-column:1/-1}.na-votes{justify-content:flex-start;flex-wrap:wrap}.na-utilities .na-old-link{display:none}}
@media(max-width:480px){.na-brand span{display:none}.na-utilities{width:100%;margin:0}.na-utilities select:first-child{flex:1;max-width:none}.na-filters{grid-template-columns:1fr}.na-matchup-result{grid-template-columns:22px 34px 1fr}.na-matchup-result>.na-text-button{grid-column:1/-1}.na-prediction{grid-template-columns:1fr}.na-prediction>*{grid-column:1!important}.na-evidence-metrics{grid-template-columns:1fr 1fr}}
</style>
