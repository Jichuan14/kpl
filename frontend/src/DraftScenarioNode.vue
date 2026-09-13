<script setup>
import { computed } from "vue";
import { t } from "./i18n";

const props = defineProps({
  node: { type: Object, required: true }, nodes: { type: Array, required: true },
  loadingId: String, stale: Boolean, compared: { type: Array, required: true },
});
const emit = defineEmits(["branch", "apply", "compare", "promote"]);
const children = computed(() => props.nodes.filter((item) => item.parentId === props.node.id));
const completedLeaf = computed(() => props.node.result?.leaves?.find((leaf) => leaf.completed));
const label = (node) => node.hero?.hero_name || `Hero ${node.hero?.hero_id || ""}`;
function tr(key, values = {}) { return Object.entries(values).reduce((text, [name, value]) => text.replace(`{${name}}`, value), t(key)); }
</script>

<template>
  <li>
    <article>
      <strong>{{ label(node) }}</strong>
      <small>{{ tr('Policy likelihood {value}%', { value: (Number(node.result?.policy_likelihood || node.hero?.probability || 0) * 100).toFixed(1) }) }}</small>
      <small v-if="node.result">{{ tr('Completed {completed}/{rollouts} · sample completion {frequency}%', { completed: node.result.completed_count, rollouts: node.result.rollouts, frequency: (node.result.completed_count / node.result.rollouts * 100).toFixed(1) }) }}</small>
      <small v-if="node.result?.average_blue_relative_lineup_advantage != null">{{ tr('Blue relative lineup advantage {value}%', { value: (node.result.average_blue_relative_lineup_advantage * 100).toFixed(1) }) }}</small>
      <div>
        <button type="button" :disabled="stale" @click="emit('apply', node)">{{ t('Apply to practice board') }}</button>
        <button v-if="completedLeaf" type="button" :disabled="stale" @click="emit('promote', { node, leaf: completedLeaf })">{{ t('Add to series') }}</button>
        <button type="button" :class="{ active: compared.includes(node.id) }" @click="emit('compare', node.id)">{{ t('Compare branch') }}</button>
      </div>
      <p v-if="node.result?.incomplete_count">{{ tr('{count} incomplete samples were excluded from the advantage average.', { count: node.result.incomplete_count }) }}</p>
      <div v-if="node.result?.next_actions?.length" class="scenario-next">
        <span>{{ t('Next-action branches') }}</span>
        <button v-for="option in node.result.next_actions" :key="option.hero_id" type="button" :disabled="stale || Boolean(loadingId)" @click="emit('branch', { parent: node, hero: option })">{{ option.hero_name }} {{ (option.probability * 100).toFixed(1) }}%</button>
      </div>
    </article>
    <ol v-if="children.length" class="scenario-tree">
      <DraftScenarioNode
        v-for="child in children" :key="child.id" :node="child" :nodes="nodes" :loading-id="loadingId" :stale="stale" :compared="compared"
        @branch="emit('branch', $event)" @apply="emit('apply', $event)" @compare="emit('compare', $event)" @promote="emit('promote', $event)"
      />
    </ol>
  </li>
</template>
