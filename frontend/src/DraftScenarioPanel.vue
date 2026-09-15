<script setup>
import { computed, ref } from "vue";
import DraftScenarioNode from "./DraftScenarioNode.vue";
import { t } from "./i18n";

const props = defineProps({ nodes: { type: Array, required: true }, loadingId: String, error: String, stale: Boolean });
const emit = defineEmits(["branch", "apply", "promote"]);
const compared = ref([]);
const roots = computed(() => props.nodes.filter((node) => !node.parentId));
const comparison = computed(() => {
  const selected = props.nodes.filter((node) => compared.value.includes(node.id));
  if (selected.length !== 2) return null;
  const [left, right] = selected;
  const leftValue = left.result?.average_blue_relative_lineup_advantage;
  const rightValue = right.result?.average_blue_relative_lineup_advantage;
  return { left, right, delta: leftValue == null || rightValue == null ? null : leftValue - rightValue };
});
function toggleCompare(id) {
  compared.value = compared.value.includes(id) ? compared.value.filter((item) => item !== id) : [...compared.value.slice(-1), id];
}
function label(node) { return node.hero?.hero_name || `Hero ${node.hero?.hero_id || ""}`; }
function tr(key, values = {}) { return Object.entries(values).reduce((text, [name, value]) => text.replace(`{${name}}`, value), t(key)); }
</script>

<template>
  <section class="scenario-panel" :class="{ stale }">
    <header>
      <div>
        <h2>{{ t('What-if branches') }}</h2>
        <p>{{ t('Freeze the current snapshot and sample 50 legal draft completions after each branch. Policy likelihood, sample completion, and relative lineup advantage are separate signals—not win probability.') }}</p>
      </div>
      <span>{{ stale ? t('Official match state changed. These branches are stale.') : tr('{count}/200 branches', { count: nodes.length }) }}</span>
    </header>
    <p v-if="error" class="recommendation-error">{{ error }}</p>
    <p v-if="!roots.length" class="scenario-empty">{{ t('Choose a predicted or listed hero to create a hypothetical branch.') }}</p>
    <ol v-else class="scenario-tree">
      <DraftScenarioNode v-for="node in roots" :key="node.id" :node="node" :nodes="nodes" :loading-id="loadingId" :stale="stale" :compared="compared" @branch="emit('branch', $event)" @apply="emit('apply', $event)" @promote="emit('promote', $event)" @compare="toggleCompare" />
    </ol>
    <div v-if="comparison" class="scenario-compare">
      <strong>{{ label(comparison.left) }}{{ $t("vs") }}{{ label(comparison.right) }}</strong>
      <span>{{ tr('Policy {left}% / {right}%', { left: (comparison.left.result?.policy_likelihood * 100 || 0).toFixed(1), right: (comparison.right.result?.policy_likelihood * 100 || 0).toFixed(1) }) }}</span>
      <span v-if="comparison.delta != null">{{ tr('Blue relative lineup-advantage difference: {delta} points', { delta: (comparison.delta * 100).toFixed(1) }) }}</span>
      <span v-else>{{ t('Both branches need completed samples before their advantages can be compared.') }}</span>
    </div>
  </section>
</template>

<style scoped>
.scenario-panel { margin-top:.75rem; padding:1rem 1.15rem; border:1px solid var(--line); background:rgba(255,255,255,.8); }
.scenario-panel.stale { background:#fff8e7; }
.scenario-panel > header { display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; }
.scenario-panel h2 { margin:0; font:700 1.25rem var(--display); letter-spacing:-.035em; }
.scenario-panel header p,.scenario-empty,.scenario-tree p,.scenario-compare { margin:.35rem 0 0; color:var(--ink-soft); font-size:.65rem; line-height:1.5; }
.scenario-panel header > span { padding:.38rem .55rem; border:1px solid var(--line); background:#edf8f3; color:var(--accent-deep); font:700 .58rem var(--mono); white-space:nowrap; }
.scenario-tree { display:grid; gap:.5rem; margin:.8rem 0 0; padding:0; list-style:none; }
.scenario-tree article { display:grid; grid-template-columns:minmax(7rem,1fr) repeat(3,auto); gap:.4rem .7rem; align-items:center; padding:.7rem; border:1px solid var(--line); background:#fff; }
.scenario-tree strong { font:700 .76rem var(--mono); }.scenario-tree small { color:var(--ink-soft); font-size:.55rem; }
.scenario-tree article > div { display:flex; gap:.3rem; }.scenario-tree button { min-height:29px; padding:.32rem .45rem; border:1px solid var(--line); background:#fff; color:var(--ink); font:700 .57rem var(--mono); cursor:pointer; }.scenario-tree button.active { background:var(--ink); color:#fff; }.scenario-tree button:disabled { cursor:not-allowed; opacity:.5; }.scenario-tree article > p,.scenario-tree article > ol { grid-column:1 / -1; }
:deep(.scenario-next) { grid-column:1 / -1; display:flex; flex-wrap:wrap; align-items:center; gap:.3rem; }:deep(.scenario-next > span) { width:100%; color:var(--ink-soft); font-size:.55rem; }:deep(.scenario-tree) { display:grid; gap:.35rem; margin:.45rem 0 0; padding:0 0 0 .8rem; list-style:none; }:deep(.scenario-tree article) { display:grid; grid-template-columns:minmax(7rem,1fr) repeat(3,auto); gap:.4rem .7rem; align-items:center; padding:.7rem; border:1px solid var(--line); background:#f8fbf9; }
@media (max-width:700px) { .scenario-panel > header,.scenario-tree article { display:grid; grid-template-columns:1fr; }.scenario-panel header > span { justify-self:start; }.scenario-tree article > div { flex-wrap:wrap; } }
</style>
