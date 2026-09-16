<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { heroAsset } from "./heroAssets";
import { language } from "./i18n";

const props = defineProps({
  payload: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: "" },
  wide: { type: Boolean, default: false },
});

const zh = computed(() => language.value === "zh-CN");
const copy = computed(() => zh.value ? {
  eyebrow: "历史证据", title: "本步意图", waiting: "完成一步禁用或选择后，这里会显示可能的后续意图。",
  loading: "正在匹配全部赛季…", unavailable: "证据暂时不可用，请确认后端正在运行。",
  ownCandidates: "本步操作方后续选择", opponentCandidates: "对方后续选择", evidenceRows: "条",
  own: "本步操作方", opponent: "对方", relativeDifference: "相对差值", support: "重合样本", actions: "次操作",
  descriptive: "可描述", insufficient: "样本不足", moreOften: "更常出现", lessOften: "更少出现", unchanged: "比例接近",
  afterMove: "这一步后", afterAlternatives: "其他合法操作后", contradiction: "这个方向不支持当前解读",
  disagreement: "不同赛季的方向不一致", season: "赛季", selectedColumn: "这一步", alternativesColumn: "其他操作", difference: "差值",
  method: "比较口径", methodBody: "仅比较同赛制、同小局、同 BP 顺位，以及双方已选英雄数量相同的历史操作。各赛季分别计算后再汇总。",
  close: "关闭", noData: "无样本", detailsTitle: "意图证据详情",
} : {
  eyebrow: "Historical evidence", title: "Move intents", waiting: "Complete a ban or pick to see plausible follow-up intents here.",
  loading: "Matching all available seasons...", unavailable: "Evidence is unavailable. Confirm that the backend is running.",
  ownCandidates: "Team making this move", opponentCandidates: "Opponent follow-ups", evidenceRows: "rows",
  own: "Team making this move", opponent: "Opponent", relativeDifference: "Relative difference", support: "Overlap sample", actions: "actions",
  descriptive: "Descriptive", insufficient: "Insufficient sample", moreOften: "appeared more often", lessOften: "appeared less often", unchanged: "appeared at a similar rate",
  afterMove: "After this move", afterAlternatives: "After other legal moves", contradiction: "The direction does not support this reading",
  disagreement: "The direction differs across seasons", season: "Season", selectedColumn: "This move", alternativesColumn: "Other moves", difference: "Difference",
  method: "Comparison basis", methodBody: "Only historical actions with the same rules, game number, BP slot, and current pick counts are compared. Seasons are calculated separately before aggregation.",
  close: "Close", noData: "No sample", detailsTitle: "Intent evidence detail",
});

const selectedIntent = ref(null);
const move = computed(() => props.payload?.move || null);
const continuation = computed(() => (move.value?.evidence || []).filter((item) => item.kind === "continuation_association"));
const signalGroups = computed(() => [
  {
    key: "own",
    label: copy.value.ownCandidates,
    items: continuation.value.filter((item) => item.perspective === "own").sort((a, b) => (a.perspective_rank || 99) - (b.perspective_rank || 99)),
  },
  {
    key: "opponent",
    label: copy.value.opponentCandidates,
    items: continuation.value.filter((item) => item.perspective === "opponent").sort((a, b) => (a.perspective_rank || 99) - (b.perspective_rank || 99)),
  },
].filter((group) => group.items.length));

watch(() => props.payload, () => { selectedIntent.value = null; });

function percent(value) { return value == null ? copy.value.noData : `${(Number(value) * 100).toFixed(1)}%`; }
function points(value) {
  if (value == null) return copy.value.noData;
  const numeric = Number(value) * 100;
  const formatted = `${numeric >= 0 ? "+" : ""}${numeric.toFixed(1)}`;
  return zh.value ? `${formatted} 个百分点` : `${formatted} percentage points`;
}
function compactPoints(value) {
  if (value == null) return copy.value.noData;
  const numeric = Number(value) * 100;
  return `${numeric >= 0 ? "+" : ""}${numeric.toFixed(1)}`;
}
function trendLabel(value) {
  if (value == null || Math.abs(Number(value)) < 0.0005) return copy.value.unchanged;
  return Number(value) > 0 ? copy.value.moreOften : copy.value.lessOften;
}
function trendClass(value) {
  if (value == null || Math.abs(Number(value)) < 0.0005) return "trend-flat";
  return Number(value) > 0 ? "trend-up" : "trend-down";
}
function signalTitle(item) {
  return `${item.target_hero_name} ${trendLabel(item.standardized_difference)}`;
}
function signalSentence(item) {
  const subject = item.perspective === "own" ? copy.value.own : copy.value.opponent;
  if (item.selected_action_rate == null || item.standardized_other_action_rate == null) return copy.value.insufficient;
  if (zh.value) return `在历史相似局面中，这一步后${subject}选择 ${item.target_hero_name} 的比例为 ${percent(item.selected_action_rate)}，其他合法操作后为 ${percent(item.standardized_other_action_rate)}。`;
  return `In similar historical contexts, the ${subject.toLowerCase()} selected ${item.target_hero_name} after this move at ${percent(item.selected_action_rate)}, compared with ${percent(item.standardized_other_action_rate)} after other legal moves.`;
}
function closeModal() { selectedIntent.value = null; }
function onKeydown(event) { if (event.key === "Escape") closeModal(); }
onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <section class="intent-widget" :class="{ 'is-wide': wide }" aria-live="polite">
    <header class="intent-header">
      <div><p>{{ copy.eyebrow }}</p><h2>{{ copy.title }}</h2></div>
    </header>

    <div v-if="loading" class="intent-state loading-state"><i></i><i></i><span>{{ copy.loading }}</span></div>
    <p v-else-if="error" class="intent-state error-state">{{ copy.unavailable }}</p>
    <p v-else-if="!move" class="intent-state waiting-state">{{ copy.waiting }}</p>

    <div v-else class="intent-scroll">
      <section v-for="group in signalGroups" :key="group.key" class="intent-group">
        <header><h3>{{ group.label }}</h3><span>{{ group.items.length }} {{ copy.evidenceRows }}</span></header>
        <button
          v-for="item in group.items"
          :key="`${item.perspective}-${item.target_hero_id}`"
          type="button"
          class="intent-row"
          :aria-label="`${signalTitle(item)}，${points(item.standardized_difference)}`"
          @click="selectedIntent = item"
        >
          <img v-if="heroAsset(item.target_hero_id)" :src="heroAsset(item.target_hero_id)" alt="" />
          <span class="intent-name"><strong>{{ item.target_hero_name }}</strong><small>{{ trendLabel(item.standardized_difference) }}</small></span>
          <span class="intent-delta" :class="trendClass(item.standardized_difference)">
            <strong>{{ compactPoints(item.standardized_difference) }}</strong>
            <small>{{ zh ? "百分点" : "pp" }}</small>
          </span>
          <span class="intent-open" aria-hidden="true">›</span>
        </button>
      </section>
      <p v-if="!signalGroups.length" class="intent-state">{{ copy.insufficient }}</p>
    </div>
  </section>

  <Teleport to="body">
    <div v-if="selectedIntent" class="intent-modal-backdrop" role="presentation" @click.self="closeModal">
      <section class="intent-modal" role="dialog" aria-modal="true" :aria-label="copy.detailsTitle">
        <header class="intent-modal-header">
          <div>
            <p>{{ selectedIntent.perspective === "own" ? copy.ownCandidates : copy.opponentCandidates }}</p>
            <h2>{{ signalTitle(selectedIntent) }}</h2>
          </div>
          <button type="button" :aria-label="copy.close" @click="closeModal">×</button>
        </header>

        <div class="intent-modal-lead">
          <img v-if="heroAsset(selectedIntent.target_hero_id)" :src="heroAsset(selectedIntent.target_hero_id)" alt="" />
          <p>{{ signalSentence(selectedIntent) }}</p>
          <div>
            <span>{{ copy.relativeDifference }}</span>
            <strong :class="trendClass(selectedIntent.standardized_difference)">{{ points(selectedIntent.standardized_difference) }}</strong>
            <small>{{ copy.support }} {{ selectedIntent.overlap_selected_actions }} {{ copy.actions }}</small>
          </div>
        </div>

        <p v-if="selectedIntent.contradiction || selectedIntent.season_direction_disagreement" class="intent-flags">
          <span v-if="selectedIntent.contradiction">{{ copy.contradiction }}</span>
          <span v-if="selectedIntent.season_direction_disagreement">{{ copy.disagreement }}</span>
        </p>

        <div class="intent-rates">
          <div><span>{{ copy.afterMove }}</span><strong>{{ percent(selectedIntent.selected_action_rate) }}</strong></div>
          <div><span>{{ copy.afterAlternatives }}</span><strong>{{ percent(selectedIntent.standardized_other_action_rate) }}</strong></div>
        </div>

        <div class="season-table" role="table">
          <div class="season-table-head" role="row">
            <span role="columnheader">{{ copy.season }}</span><span role="columnheader">{{ copy.selectedColumn }}</span>
            <span role="columnheader">{{ copy.alternativesColumn }}</span><span role="columnheader">{{ copy.difference }}</span>
          </div>
          <div v-for="season in selectedIntent.season_contributions" :key="season.league_id" class="season-table-row" role="row">
            <strong role="cell">{{ season.league_id }}</strong>
            <span role="cell">{{ percent(season.selected_action.rate) }} <small>({{ season.selected_action.actions }})</small></span>
            <span role="cell">{{ percent(season.other_legal_actions.rate) }} <small>({{ season.other_legal_actions.actions }})</small></span>
            <strong role="cell" :class="trendClass(season.difference)">{{ points(season.difference) }}</strong>
          </div>
        </div>

        <footer><strong>{{ copy.method }}</strong><span>{{ copy.methodBody }}</span></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.intent-widget{display:grid;grid-template-rows:auto minmax(0,1fr);height:100%;min-height:0;box-sizing:border-box;border:1px solid var(--line);background:#fff;overflow:hidden}
.intent-header{display:flex;min-height:62px;box-sizing:border-box;align-items:center;justify-content:space-between;gap:16px;padding:10px 12px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.96)}
.intent-header p,.intent-modal-header p{margin:0 0 2px;color:var(--accent-deep);font:700 9px var(--mono);letter-spacing:.1em;text-transform:uppercase}.intent-header h2,.intent-modal-header h2{margin:0;font:750 17px var(--display);letter-spacing:-.03em}
.intent-scroll{min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.intent-group>header{position:sticky;z-index:1;top:0;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 11px;border-bottom:1px solid rgba(16,42,46,.08);background:#f4f7f5}.intent-group h3{margin:0;font:700 11px var(--display);letter-spacing:.02em}.intent-group>header span{color:var(--ink-soft);font:600 9px var(--mono)}
.intent-row{display:grid;width:100%;min-height:49px;box-sizing:border-box;grid-template-columns:34px minmax(0,1fr) 54px 10px;gap:9px;align-items:center;padding:7px 10px;border:0;border-bottom:1px solid rgba(16,42,46,.08);background:#fff;color:var(--ink);text-align:left;cursor:pointer}.intent-row:hover,.intent-row:focus-visible{background:#edf7f2;outline:none;box-shadow:inset 3px 0 var(--accent-deep)}.intent-row img{width:34px;height:34px;border-radius:3px;object-fit:cover}.intent-name{display:grid;min-width:0;gap:2px}.intent-name strong,.intent-name small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.intent-name strong{font:700 12px var(--display)}.intent-name small{color:var(--ink-soft);font:500 9px var(--mono)}.intent-delta{display:grid;justify-items:end;line-height:1;white-space:nowrap}.intent-delta strong{font:750 12px var(--mono)}.intent-delta small{margin-top:3px;color:currentColor;font:600 8px var(--mono);opacity:.75}.intent-open{color:var(--accent-deep);font:500 18px/1 var(--display)}.trend-up{color:#277458!important}.trend-down{color:#9b4d3f!important}.trend-flat{color:var(--ink-soft)!important}
.intent-state{margin:0;padding:14px;color:var(--ink-soft);font-size:11px;line-height:1.55}.error-state{color:var(--warn)}.waiting-state{align-self:start}.loading-state{display:grid;grid-template-columns:1fr 1fr;gap:6px}.loading-state i{height:38px;background:linear-gradient(90deg,rgba(16,42,46,.04),rgba(16,42,46,.09),rgba(16,42,46,.04));background-size:200% 100%;animation:intent-load 1.2s linear infinite}.loading-state span{grid-column:1/-1}
.intent-widget.is-wide .intent-scroll{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));overflow:auto}.intent-widget.is-wide .intent-group+.intent-group{border-left:1px solid var(--line)}
.intent-modal-backdrop{position:fixed;z-index:150;inset:0;display:grid;place-items:center;padding:1rem;background:rgba(16,42,46,.38)}
.intent-modal{width:min(820px,100%);max-height:min(780px,calc(100dvh - 2rem));overflow:auto;border:1px solid var(--line);background:#fff;box-shadow:0 1.4rem 4rem rgba(16,42,46,.3)}
.intent-modal-header{position:sticky;z-index:2;top:0;display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:16px 18px;border-bottom:1px solid var(--line);background:#fff}.intent-modal-header h2{font-size:20px}.intent-modal-header button{display:grid;width:34px;height:34px;place-items:center;padding:0;border:1px solid var(--line);background:#fff;color:var(--ink);font:400 21px/1 var(--display);cursor:pointer}.intent-modal-header button:hover{border-color:var(--accent-deep);color:var(--accent-deep)}
.intent-modal-lead{display:grid;grid-template-columns:52px minmax(0,1fr) auto;gap:14px;align-items:center;padding:16px 18px}.intent-modal-lead img{width:52px;height:52px;border-radius:50%;object-fit:cover}.intent-modal-lead p{margin:0;color:var(--ink-soft);font-size:12px;line-height:1.6}.intent-modal-lead>div{display:grid;justify-items:end;text-align:right}.intent-modal-lead>div span,.intent-modal-lead>div small{color:var(--ink-soft);font:600 9px var(--mono)}.intent-modal-lead>div strong{margin:3px 0;font:750 18px var(--mono)}
.intent-flags{display:flex;flex-wrap:wrap;gap:8px;margin:0 18px 12px;color:#8d473b;font:650 10px var(--mono)}.intent-flags span{padding-left:8px;border-left:2px solid currentColor}
.intent-rates{display:grid;grid-template-columns:1fr 1fr;margin:0 18px;border:1px solid var(--line);background:rgba(16,42,46,.025)}.intent-rates div{display:flex;justify-content:space-between;gap:12px;padding:10px}.intent-rates div+div{border-left:1px solid var(--line)}.intent-rates span{color:var(--ink-soft);font-size:11px}.intent-rates strong{font:700 13px var(--mono)}
.season-table{margin:16px 18px;border:1px solid var(--line);font:500 10px var(--mono);overflow-x:auto}.season-table-head,.season-table-row{display:grid;min-width:590px;grid-template-columns:minmax(90px,.75fr) repeat(3,minmax(120px,1fr));gap:10px;padding:7px 10px;align-items:center}.season-table-head{background:rgba(16,42,46,.06);color:var(--ink-soft);font-weight:700}.season-table-row+.season-table-row{border-top:1px solid rgba(16,42,46,.08)}.season-table-row small{color:var(--ink-soft)}
.intent-modal footer{display:grid;grid-template-columns:100px 1fr;gap:14px;padding:12px 18px;border-top:1px solid var(--line);color:var(--ink-soft);font-size:10px;line-height:1.55}.intent-modal footer strong{color:var(--ink)}
@keyframes intent-load{to{background-position:-200% 0}}@media(prefers-reduced-motion:reduce){.loading-state i{animation:none}}
@media(max-width:620px){.intent-widget.is-wide .intent-scroll{display:block}.intent-widget.is-wide .intent-group+.intent-group{border-left:0}.intent-modal-backdrop{align-items:end;padding:.5rem}.intent-modal{max-height:82dvh}.intent-modal-lead{grid-template-columns:2.75rem 1fr}.intent-modal-lead img{width:2.75rem;height:2.75rem}.intent-modal-lead>div{grid-column:1/-1;justify-items:start;text-align:left}.intent-rates{grid-template-columns:1fr}.intent-rates div+div{border-top:1px solid var(--line);border-left:0}.intent-modal footer{grid-template-columns:1fr}}
</style>
