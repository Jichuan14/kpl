<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  label: { type: String, required: true },
  modelValue: { type: String, default: "" },
  teams: { type: Array, default: () => [] },
  excludedId: { type: String, default: "" },
  opponentTeam: { type: Object, default: null },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue"]);
const open = ref(false);
const query = ref("");

const selected = computed(() =>
  props.teams.find((team) => String(team.team_id) === String(props.modelValue))
);

const visibleTeams = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase();
  return props.teams.filter((team) => {
    if (String(team.team_id) === String(props.excludedId || "")) return false;
    return (
      !needle ||
      String(team.team_name).toLocaleLowerCase().includes(needle) ||
      String(team.team_id).toLocaleLowerCase().includes(needle)
    );
  });
});

watch(
  () => props.modelValue,
  () => {
    if (!open.value) query.value = selected.value?.team_name || "";
  },
  { immediate: true }
);

function focus() {
  if (props.disabled) return;
  open.value = true;
  query.value = "";
}

function close() {
  window.setTimeout(() => {
    open.value = false;
    query.value = selected.value?.team_name || "";
  }, 120);
}

function selectTeam(team) {
  emit("update:modelValue", String(team.team_id));
  query.value = String(team.team_name);
  open.value = false;
}
</script>

<template>
  <label class="team-combobox">
    <span>{{ label }}</span>
    <div class="team-combobox-control">
      <img v-if="selected?.team_icon" :src="selected.team_icon" alt="" />
      <input
        v-model="query"
        type="search"
        autocomplete="off"
        :disabled="disabled"
        :placeholder="`搜索${label}…`"
        role="combobox"
        :aria-expanded="open"
        aria-autocomplete="list"
        @focus="focus"
        @input="open = true"
        @blur="close"
      />
      <div v-if="open" class="team-options" role="listbox">
        <button
          v-if="opponentTeam && !query.trim()"
          type="button"
          class="team-opponent"
          role="option"
          :aria-selected="String(opponentTeam.team_id) === String(modelValue)"
          @mousedown.prevent="selectTeam(opponentTeam)"
        >
          <img v-if="opponentTeam.team_icon" :src="opponentTeam.team_icon" alt="" />
          <span><small>对手</small>{{ opponentTeam.team_name }}</span>
        </button>
        <button
          v-for="team in visibleTeams"
          :key="team.team_id"
          type="button"
          role="option"
          :aria-selected="String(team.team_id) === String(modelValue)"
          @mousedown.prevent="selectTeam(team)"
        >
          <img v-if="team.team_icon" :src="team.team_icon" alt="" />
          <span>{{ team.team_name }}</span>
          <small>{{ team.battle_count }} 场</small>
        </button>
        <small v-if="!visibleTeams.length" class="no-team">没有匹配的本赛季战队。</small>
      </div>
    </div>
  </label>
</template>

<style scoped>
.team-combobox { display:grid; gap:.2rem; min-width:12rem; color:var(--ink-soft); font-size:.58rem; letter-spacing:.08em; text-transform:uppercase; }
.team-combobox-control { position:relative; display:flex; align-items:center; min-height:34px; border:1px solid var(--line); background:#fff; }
.team-combobox-control > img { width:1.5rem; height:1.5rem; margin-left:.35rem; object-fit:contain; }
input { width:100%; min-width:0; min-height:32px; padding:.35rem .5rem; border:0; outline:0; background:transparent; color:var(--ink); font:inherit; font-size:.7rem; letter-spacing:normal; text-transform:none; }
.team-options { position:absolute; z-index:30; top:calc(100% + .25rem); left:0; right:0; display:grid; max-height:16rem; overflow:auto; border:1px solid var(--line); box-shadow:0 12px 26px rgba(16,42,46,.16); background:#fff; }
.team-options .team-opponent { display:grid; grid-template-columns:1.6rem 1fr; gap:.45rem; align-items:center; min-height:2.5rem; padding:.35rem .45rem; border-bottom:1px solid var(--line); background:#edf8f3; color:var(--ink); }
.team-options .team-opponent:hover { background:#dff1e9; }
.team-opponent img { width:1.5rem; height:1.5rem; object-fit:contain; }
.team-opponent span { display:grid; gap:.05rem; font-size:.7rem; letter-spacing:normal; text-transform:none; }
.team-opponent small { color:var(--accent-deep); font-size:.52rem; letter-spacing:.08em; text-transform:uppercase; }
.team-options button { display:grid; grid-template-columns:1.6rem 1fr auto; gap:.45rem; align-items:center; min-height:2.5rem; padding:.35rem .45rem; border:0; border-bottom:1px solid var(--line); background:#fff; color:var(--ink); text-align:left; cursor:pointer; }
.team-options button:hover, .team-options button[aria-selected="true"] { background:#fff7e7; }
.team-options img { width:1.5rem; height:1.5rem; object-fit:contain; }
.team-options span { font-size:.7rem; letter-spacing:normal; text-transform:none; }
.team-options small { color:var(--ink-soft); font-size:.58rem; letter-spacing:normal; text-transform:none; white-space:nowrap; }
.team-options .no-team { padding:.7rem; }
</style>
