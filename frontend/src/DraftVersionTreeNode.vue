<script setup>
import { computed } from "vue";
import { t } from "./i18n";
const props = defineProps({ node: Object, childrenByParent: Object, heroes: Array, heroAsset: Function, activeCheckpoint: String, currentTrail: Set, depth: { type: Number, default: 0 } });
const emit = defineEmits(["restore"]);
const names = computed(() => new Map(props.heroes.map((hero) => [Number(hero.hero_id), hero.hero_name])));
const children = computed(() => props.childrenByParent.get(String(props.node.id)) || []);
const isActive = computed(() => String(props.activeCheckpoint) === String(props.node.id));
const isOnTrail = computed(() => props.currentTrail?.has(String(props.node.id)));
const side = (action) => action.field.startsWith("blue_") ? "blue" : "red";
const label = (action) => `${t(side(action) === "blue" ? "Blue" : "Red")} · ${t(action.field.endsWith("picks") ? "Pick" : "Ban")}`;
const name = (id) => names.value.get(Number(id)) || `#${id}`;
const actionSummary = computed(() => props.node.actions.map((action) => `${label(action)} · ${name(action.heroId)}`).join("\n"));
</script>
<template>
  <li class="tree-item" :class="{ 'on-trail': isOnTrail }">
    <button type="button" class="checkpoint-node" :class="{ active: isActive, ancestor: isOnTrail }" :aria-current="isActive ? 'step' : undefined" :aria-label="`${t('Checkpoint')} BP ${Math.max(0, Number(node.state?.bpOrder || 1) - 1)}, ${node.actions.length} ${t('actions')}`" :title="actionSummary" @click="emit('restore', node)">
      <span class="node-step">{{ t('Checkpoint') }}{{ $t("· BP") }}{{ Math.max(0, Number(node.state?.bpOrder || 1) - 1) }}</span><span class="node-count">{{ node.actions.length }} {{ t('actions') }}</span>
      <span class="hero-summary"><span v-for="action in node.actions.slice(-3)" :key="`${action.bpOrder}-${action.heroId}`" class="hero-dot" :class="side(action)" :title="`${label(action)} · ${name(action.heroId)}`"><img v-if="heroAsset(action.heroId)" :src="heroAsset(action.heroId)" :alt="name(action.heroId)" /><i v-else>{{ name(action.heroId).slice(0, 1) }}</i></span></span>
    </button>
    <ol v-if="children.length" class="tree-level child-level"><DraftVersionTreeNode v-for="child in children" :key="child.id" :node="child" :children-by-parent="childrenByParent" :heroes="heroes" :hero-asset="heroAsset" :active-checkpoint="activeCheckpoint" :current-trail="currentTrail" :depth="depth + 1" @restore="emit('restore', $event)" /></ol>
  </li>
</template>
<style scoped>
.tree-item{position:relative;display:flex;flex-direction:column;align-items:center;min-width:5.8rem;padding:0 .45rem;list-style:none}
.checkpoint-node{position:relative;display:grid;width:5.05rem;height:5.05rem;z-index:1;place-content:center;gap:.14rem;padding:.38rem;border:1px solid var(--ink);border-radius:50%;background:#fff;color:var(--ink);cursor:pointer;text-align:center;transition:border-color 140ms ease,background-color 140ms ease,box-shadow 140ms ease,transform 140ms ease}
.checkpoint-node:hover{border-color:#28745d;background:#f1f8f5;transform:translateY(-1px)}
.checkpoint-node:focus-visible{outline:2px solid #28745d;outline-offset:3px}
.checkpoint-node.active{border:2px solid #28745d;background:#e8f6ef;box-shadow:0 .2rem .5rem rgba(40,116,93,.16)}
.checkpoint-node.ancestor:not(.active){border-color:#6ba88e}
.node-step{font:700 .48rem var(--mono)}
.node-count{color:var(--ink-soft);font:600 .41rem var(--mono)}
.hero-summary{display:flex;justify-content:center}
.hero-dot{display:grid;width:.88rem;height:.88rem;margin-left:-.13rem;border:1px solid #fff;border-radius:50%;overflow:hidden;background:#eef2f1}
.hero-dot:first-child{margin-left:0}
.hero-dot.blue{outline:1px solid #247aa5}
.hero-dot.red{outline:1px solid #b74942}
.hero-dot img{width:100%;height:100%;object-fit:cover}
.hero-dot i{display:grid;height:100%;place-items:center;font:700 .35rem var(--mono);font-style:normal}
.blue{color:#247aa5}
.red{color:#b74942}
.tree-level{display:flex;justify-content:center;margin:0;padding:0;list-style:none}
.child-level{position:relative;margin-top:1.65rem}
.child-level::before{position:absolute;top:-1.65rem;left:50%;width:1px;height:1.65rem;background:var(--ink);content:""}
.child-level>.tree-item{padding-top:1.8rem}
.child-level>.tree-item::before,.child-level>.tree-item::after{position:absolute;top:0;width:50%;height:1.8rem;border-top:1px solid var(--ink);pointer-events:none;content:""}
.child-level>.tree-item::before{right:50%}
.child-level>.tree-item::after{left:50%;border-left:1px solid var(--ink)}
.child-level>.tree-item:first-child::before,.child-level>.tree-item:last-child::after{border-top-color:transparent}
.child-level>.tree-item:last-child::before{border-right:1px solid var(--ink);border-radius:0 .45rem 0 0}
.child-level>.tree-item:first-child::after{border-radius:.45rem 0 0 0}
/* The last branch's rounded ::before already draws its vertical connector. */
.child-level>.tree-item:last-child:not(:only-child)::after{display:none}
.child-level>.tree-item:only-child::before{display:none}
.child-level>.tree-item:only-child::after{width:0;border-top:0}
.child-level>.tree-item.on-trail::before,.child-level>.tree-item.on-trail::after{border-color:#6ba88e}
.child-level>.tree-item.on-trail:first-child::before,.child-level>.tree-item.on-trail:last-child::after{border-top-color:transparent}
.child-level:has(>.tree-item.on-trail)::before{background:#6ba88e}
.child-level>.tree-item>.checkpoint-node::before{position:absolute;top:-.36rem;left:50%;border-top:.36rem solid var(--ink);border-right:.2rem solid transparent;border-left:.2rem solid transparent;transform:translateX(-50%);content:""}
.child-level>.tree-item.on-trail>.checkpoint-node::before{border-top-color:#6ba88e}
@media(max-width:620px){.checkpoint-node{width:4.7rem;height:4.7rem}.tree-item{min-width:5.35rem;padding-inline:.3rem}.child-level{margin-top:1.45rem}.child-level::before{top:-1.45rem;height:1.45rem}}
</style>
