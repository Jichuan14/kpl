<script setup>
import { computed } from "vue";
import { t } from "./i18n";

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  heroes: { type: Array, default: () => [] },
  heroAsset: { type: Function, required: true },
  activeCheckpoint: { type: String, default: "" },
});

const emit = defineEmits(["restore-checkpoint"]);

const heroNames = computed(() => new Map(
  props.heroes.map((hero) => [Number(hero.hero_id), hero.hero_name])
));
const visibleNodes = computed(() => props.nodes.filter((node) =>
  node.type === "segment"
    ? node.actions.length
    : node.branches.some((branch) => branch.actions.length)
));
const hasTree = computed(() => visibleNodes.value.length > 0);

function actionSide(action) {
  return action.field.startsWith("blue_") ? "blue" : "red";
}
function actionLabel(action) {
  const side = t(actionSide(action) === "blue" ? "Blue" : "Red");
  const kind = t(action.field.endsWith("picks") ? "Pick" : "Ban");
  return `${side} · ${kind}`;
}
function heroName(heroId) {
  return heroNames.value.get(Number(heroId)) || `#${heroId}`;
}
function restore(nodeId, branchId = null) {
  emit("restore-checkpoint", { nodeId, branchId });
}
function canRestoreSegment(node) {
  return !node.pending && Boolean(node.state);
}
function restoreSegment(node) {
  if (!canRestoreSegment(node)) return;
  restore(node.id);
}
function restoreFork(node) {
  if (!node.baseState) return;
  restore(node.id);
}
function restoreBranch(node, branch) {
  if (!branch.state) return;
  restore(node.id, branch.id);
}
</script>

<template>
  <section class="version-tree" aria-labelledby="version-tree-title">
    <header class="tree-heading">
      <div>
        <h2 id="version-tree-title">{{ t('Version tree') }}</h2>
        <p>{{ t('Current-game paths. Consecutive steps without a what-if are grouped into one node.') }}</p>
      </div>
      <span>{{ visibleNodes.length }} {{ t('nodes') }}</span>
    </header>

    <div v-if="!hasTree" class="tree-empty">
      <div class="empty-node">?</div>
      <p>{{ t('Open a what-if workspace to create the first branch.') }}</p>
    </div>

    <ol v-else class="tree-trunk">
      <li v-for="node in visibleNodes" :key="node.id" :class="`tree-${node.type}`">
        <div
          v-if="node.type === 'segment'"
          class="path-node"
          :class="{ clickable: canRestoreSegment(node) }"
          @click="restoreSegment(node)"
        >
          <button
            type="button"
            class="node-disc path-disc"
            :class="{ active: activeCheckpoint === node.id }"
            :disabled="!canRestoreSegment(node)"
            :title="t('Restore this checkpoint')"
            @click.stop="restoreSegment(node)"
          >
            <strong>{{ t(node.pending ? 'Current path' : 'Main path') }}</strong>
            <span>{{ node.actions.length }} {{ t('actions') }}</span>
          </button>
          <div v-if="node.actions.length" class="hero-popover" role="tooltip">
            <div v-for="action in node.actions" :key="`${action.bpOrder}-${action.field}-${action.heroId}`" class="hero-chip" :class="actionSide(action)">
              <img v-if="heroAsset(action.heroId)" :src="heroAsset(action.heroId)" :alt="heroName(action.heroId)" />
              <span v-else>{{ heroName(action.heroId).slice(0, 1) }}</span>
              <small><b>{{ actionLabel(action) }}</b>{{ heroName(action.heroId) }}</small>
            </div>
          </div>
        </div>

        <div v-else class="fork-node">
          <button
            type="button"
            class="node-disc fork-root"
            :class="{ active: activeCheckpoint === node.id }"
            :disabled="!node.baseState"
            :title="t('Restore this checkpoint')"
            @click="restoreFork(node)"
          >
            <strong>{{ t('What-if') }}</strong>
            <span>{{ node.branches.length }} {{ t('branches') }}</span>
          </button>
          <div class="fork-branches" :style="{ '--branch-count': node.branches.length }">
            <section
              v-for="(branch, index) in node.branches"
              :key="branch.id"
              class="branch-leaf"
              :class="{ selected: branch.id === node.selectedBranchId, clickable: Boolean(branch.state) }"
              @click="restoreBranch(node, branch)"
            >
              <button
                type="button"
                class="node-disc branch-disc"
                :class="{ active: activeCheckpoint === `${node.id}:${branch.id}` }"
                :disabled="!branch.state"
                :title="t('Restore this checkpoint')"
                @click.stop="restoreBranch(node, branch)"
              >
                <b>{{ t('Branch') }} {{ index + 1 }}</b>
                <span v-if="branch.id === node.selectedBranchId">{{ t('Applied') }}</span>
                <span v-else>{{ branch.actions.length }} {{ t('actions') }}</span>
              </button>
              <div v-if="branch.actions.length" class="hero-popover" role="tooltip">
                <div v-for="action in branch.actions" :key="`${action.bpOrder}-${action.field}-${action.heroId}`" class="hero-chip" :class="actionSide(action)">
                  <img v-if="heroAsset(action.heroId)" :src="heroAsset(action.heroId)" :alt="heroName(action.heroId)" />
                  <span v-else>{{ heroName(action.heroId).slice(0, 1) }}</span>
                  <small><b>{{ actionLabel(action) }}</b>{{ heroName(action.heroId) }}</small>
                </div>
              </div>
            </section>
          </div>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.version-tree{display:grid;grid-template-rows:auto minmax(0,1fr);height:100%;min-height:0;border:1px solid var(--accent-deep);background:#f8faf8;overflow:hidden}
.tree-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;padding:.7rem .85rem;border-bottom:1px solid var(--line)}
.tree-heading h2{margin:0;font:700 1.05rem var(--display);letter-spacing:-.025em}.tree-heading p{max-width:68ch;margin:.25rem 0 0;color:var(--ink-soft);font-size:.62rem}.tree-heading>span{color:var(--ink-soft);font:700 .54rem var(--mono);white-space:nowrap}
.tree-empty{display:grid;place-content:center;justify-items:center;gap:1rem;min-height:24rem;padding:2rem;text-align:center}.empty-node{display:grid;width:4.4rem;height:4.4rem;place-items:center;border:1px solid var(--ink);border-radius:50%;background:#fff;font:500 1.4rem var(--display)}.tree-empty p{max-width:25ch;margin:0;color:var(--ink-soft);font-size:.65rem;line-height:1.5}
.tree-trunk{display:grid;align-content:start;align-items:start;gap:1.55rem;margin:0;padding:1rem .55rem 1.4rem;overflow:auto;list-style:none}.tree-trunk>li{position:relative;z-index:0}.tree-trunk>li:hover,.tree-trunk>li:focus-within{z-index:2}.tree-trunk>li:not(:last-child)::after{position:absolute;bottom:-1.25rem;left:50%;width:1px;height:1rem;background:var(--ink);content:"";pointer-events:none}.tree-trunk>li:not(:last-child)::before{position:absolute;z-index:1;bottom:-1.3rem;left:calc(50% - .18rem);border-top:.32rem solid var(--ink);border-right:.18rem solid transparent;border-left:.18rem solid transparent;content:"";pointer-events:none}
.path-node,.fork-node,.branch-leaf{position:relative;display:grid;justify-items:center}.path-node.clickable,.branch-leaf.clickable{cursor:pointer}.node-disc{display:grid;width:4.35rem;height:4.35rem;place-items:center;align-content:center;box-sizing:border-box;border:1px solid var(--ink);border-radius:50%;background:#fff;text-align:center}.node-disc strong,.node-disc b{font:700 .52rem var(--mono)}.node-disc span{margin-top:.1rem;color:var(--ink-soft);font:600 .42rem var(--mono)}
button.node-disc{cursor:pointer;font:inherit}button.node-disc:hover:not(:disabled){border-color:#28745d;background:#f1f8f5}button.node-disc:focus-visible{outline:2px solid #28745d;outline-offset:3px}button.node-disc:disabled{cursor:default}.node-disc.active{border:2px solid #28745d;background:#e8f6ef;box-shadow:0 .2rem .5rem rgba(40,116,93,.16)}
.hero-popover{position:absolute;top:calc(100% + .35rem);left:50%;z-index:5;display:grid;gap:.28rem;min-width:8.8rem;max-width:12.5rem;padding:.4rem .45rem;border:1px solid var(--line);background:#fff;box-shadow:0 .35rem .85rem rgba(16,42,46,.16);opacity:0;pointer-events:none;transform:translate(-50%, .2rem);transition:opacity .14s ease, transform .14s ease}.path-node:hover .hero-popover,.path-node:focus-within .hero-popover,.branch-leaf:hover .hero-popover,.branch-leaf:focus-within .hero-popover{opacity:1;pointer-events:none;transform:translate(-50%, 0)}
.hero-chip{display:grid;grid-template-columns:1.45rem minmax(0,1fr);align-items:center;gap:.28rem;min-width:0}.hero-chip img,.hero-chip>span{display:grid;width:1.45rem;height:1.45rem;place-items:center;border:1px solid var(--line);border-radius:50%;object-fit:cover}.hero-chip small{display:grid;min-width:0;color:var(--ink-soft);font-size:.44rem;line-height:1.2}.hero-chip small b{overflow:hidden;color:var(--ink);font:700 .44rem var(--mono);text-overflow:ellipsis;white-space:nowrap}.hero-chip.blue img,.hero-chip.blue>span{outline:1px solid #247aa5;outline-offset:1px}.hero-chip.red img,.hero-chip.red>span{outline:1px solid #b74942;outline-offset:1px}
.fork-node{gap:0}.fork-root{position:relative}.fork-root::after{position:absolute;top:100%;left:50%;width:1px;height:.85rem;background:var(--ink);content:"";pointer-events:none}.fork-branches{position:relative;display:grid;width:100%;grid-template-columns:repeat(var(--branch-count),minmax(4.5rem,1fr));gap:.35rem;padding-top:1.7rem}.fork-branches::before{position:absolute;top:.85rem;left:calc(50% / var(--branch-count));right:calc(50% / var(--branch-count));height:1px;background:var(--ink);content:"";pointer-events:none}.branch-leaf{align-content:start;min-width:4.5rem;padding-top:.55rem}.branch-leaf::before{position:absolute;top:-.85rem;left:50%;width:1px;height:1.15rem;background:var(--ink);content:"";pointer-events:none}.branch-leaf::after{position:absolute;top:.12rem;left:calc(50% - .16rem);border-top:.28rem solid var(--ink);border-right:.16rem solid transparent;border-left:.16rem solid transparent;content:"";pointer-events:none}.branch-disc{width:4.05rem;height:4.05rem}.branch-leaf.selected .branch-disc{border-color:#28745d;box-shadow:0 .2rem .5rem rgba(40,116,93,.16)}.branch-leaf.selected .branch-disc>span{color:#28745d}
@media(max-width:620px){.tree-heading>span{display:none}.tree-trunk{min-height:0}.fork-branches{grid-template-columns:repeat(var(--branch-count),minmax(4.2rem,1fr))}.branch-leaf{min-width:4.2rem}}
@media(hover:none){.hero-popover{position:static;left:auto;top:auto;display:flex;flex-wrap:wrap;justify-content:center;min-width:0;max-width:100%;padding:.3rem 0 0;border:0;background:transparent;box-shadow:none;opacity:1;transform:none}.hero-chip{grid-template-columns:1.2rem}.hero-chip img,.hero-chip>span{width:1.2rem;height:1.2rem}.hero-chip small{display:none}}
@media(prefers-reduced-motion:reduce){.hero-popover{transition:none}}
</style>
