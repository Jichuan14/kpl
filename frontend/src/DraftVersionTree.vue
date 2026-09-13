<script setup>
import { computed } from "vue";
import { buildVersionTree } from "./composables/draftVersionTree";
import DraftVersionTreeNode from "./DraftVersionTreeNode.vue";
import { t } from "./i18n";

const props = defineProps({
  nodes: { type: Array, default: () => [] }, heroes: { type: Array, default: () => [] },
  heroAsset: { type: Function, required: true }, activeCheckpoint: { type: String, default: "" },
  recordingActions: { type: Array, default: () => [] }, recordingFrom: { type: String, default: "" },
  message: { type: String, default: "" },
});
const emit = defineEmits(["restore-checkpoint"]);
const children = computed(() => buildVersionTree(props.nodes));
const hasTree = computed(() => props.nodes.length > 0);
const heroNames = computed(() => new Map(props.heroes.map((hero) => [Number(hero.hero_id), hero.hero_name])));
const recordingAnchor = computed(() => props.nodes.find((node) => String(node.id) === String(props.recordingFrom)));
const recordingAnchorStep = computed(() => Math.max(0, Number(recordingAnchor.value?.state?.bpOrder || 1) - 1));
const currentTrail = computed(() => {
  const trail = new Set(); let id = props.activeCheckpoint;
  const byId = new Map(props.nodes.map((node) => [String(node.id), node]));
  while (id && byId.has(String(id))) { trail.add(String(id)); id = byId.get(String(id)).parentId; }
  return trail;
});
const side = (action) => action.field.startsWith("blue_") ? "blue" : "red";
const label = (action) => `${t(side(action) === "blue" ? "Blue" : "Red")} · ${t(action.field.endsWith("picks") ? "Pick" : "Ban")}`;
const name = (id) => heroNames.value.get(Number(id)) || `#${id}`;
function restore(node) { emit("restore-checkpoint", { nodeId: node.id }); }
</script>

<template>
  <section class="version-tree" aria-labelledby="version-tree-title">
    <header class="tree-heading">
      <div><h2 id="version-tree-title">{{ t('Version tree') }}</h2><p>{{ t('Saved BP checkpoints and the live continuation from the current checkpoint.') }}</p></div>
      <span>{{ nodes.length }} {{ t('nodes') }}</span>
    </header>
    <p v-if="message" class="tree-message" role="status">{{ message }}</p>
    <section v-if="recordingActions.length" class="recording-status" aria-live="polite">
      <span>{{ t('Recording') }} · BP {{ recordingAnchorStep }} → {{ recordingActions.length }} {{ t('actions') }}</span>
      <div>
        <b v-for="action in recordingActions" :key="`${action.bpOrder}-${action.field}-${action.heroId}`" :class="side(action)">{{ label(action) }} {{ name(action.heroId) }}</b>
      </div>
    </section>
    <div v-if="!hasTree" class="tree-empty"><div class="empty-node">?</div><p>{{ t('Add a snapshot to create your first checkpoint.') }}</p></div>
    <div v-else class="tree-scroll">
      <ol class="tree-level root-level"><DraftVersionTreeNode v-for="node in (children.get(null) || [])" :key="node.id" :node="node" :children-by-parent="children" :heroes="heroes" :hero-asset="heroAsset" :active-checkpoint="activeCheckpoint" :current-trail="currentTrail" @restore="restore" /></ol>
    </div>
  </section>
</template>

<style scoped>
.version-tree{display:flex;flex-direction:column;height:100%;min-height:0;border:1px solid var(--accent-deep);background:#f8faf8;overflow:hidden}
.tree-heading{display:flex;flex:0 0 auto;justify-content:space-between;gap:1rem;padding:.7rem .85rem;border-bottom:1px solid var(--line)}
.tree-heading h2{margin:0;font:700 1.05rem var(--display);letter-spacing:-.025em}
.tree-heading p{max-width:42ch;margin:.25rem 0 0;color:var(--ink-soft);font-size:.62rem}
.tree-heading>span{color:var(--ink-soft);font:700 .54rem var(--mono);white-space:nowrap}
.tree-message{flex:0 0 auto;margin:0;padding:.45rem .85rem;border-bottom:1px solid var(--line);color:#28745d;background:#e8f6ef;font:600 .58rem var(--mono)}
.recording-status{display:grid;flex:0 0 auto;gap:.35rem;padding:.5rem .85rem;border-bottom:1px solid #b8d8c9;background:#edf7f2;color:#28745d;font:700 .52rem var(--mono)}
.recording-status div{display:flex;flex-wrap:wrap;gap:.25rem .55rem}
.recording-status b{font:600 .43rem var(--mono)}
.blue{color:#247aa5}
.red{color:#b74942}
.tree-empty{display:grid;flex:1 1 auto;place-content:center;justify-items:center;gap:1rem;min-height:24rem;padding:2rem;text-align:center}
.empty-node{display:grid;width:4.4rem;height:4.4rem;place-items:center;border:1px solid var(--ink);border-radius:50%;background:#fff;font:500 1.4rem var(--display)}
.tree-empty p{max-width:25ch;margin:0;color:var(--ink-soft);font-size:.65rem;line-height:1.5}
.tree-scroll{flex:1 1 auto;min-height:0;overflow:auto;padding:1.1rem .9rem 2rem}
.tree-level{display:flex;justify-content:center;margin:0;padding:0;list-style:none}
.root-level{align-items:flex-start;min-width:max-content}
@media(max-width:620px){.tree-heading>span{display:none}.tree-scroll{padding:1rem .55rem 1.6rem}.root-level{justify-content:flex-start}}
</style>
