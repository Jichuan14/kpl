<script setup>
import { computed, nextTick, ref, watch } from "vue";

import { askDraftCoach } from "./api";
import { language, t } from "./i18n";

const props = defineProps({
  leagueId: { type: String, required: true },
  seasonName: { type: String, default: "" },
  draftState: { type: Object, default: null },
});

const sessionHistoryKey = "kpl-draft-coach-session-history";

function loadSessionHistory() {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(sessionHistoryKey) || "[]");
    return Array.isArray(stored) ? stored.slice(-20) : [];
  } catch {
    return [];
  }
}

function persistSessionHistory(value) {
  const completed = value
    .filter((message) => !message.loading)
    .slice(-20)
    .map((message) => ({
      id: message.id,
      question: message.question,
      context: message.context,
      error: message.error,
      loading: false,
      response: message.response
        ? {
            request_id: message.response.request_id,
            model: message.response.model,
            answer: message.response.answer,
            warnings: message.response.warnings || [],
            usage: message.response.usage || {},
          }
        : null,
    }));
  window.sessionStorage.setItem(sessionHistoryKey, JSON.stringify(completed));
}

const question = ref("");
const loading = ref(false);
const messages = ref(loadSessionHistory());
const thread = ref(null);
let messageId = Math.max(0, ...messages.value.map((message) => Number(message.id) || 0));

const contextKey = computed(() =>
  JSON.stringify({ league_id: props.leagueId, draft_state: props.draftState })
);
const hasBoardContext = computed(() => Boolean(props.draftState));
const answeredCount = computed(
  () => messages.value.filter((message) => message.response).length
);
const suggestionPairs = [
  {
    phase: 1,
    en: "What are the top three choices right now? Include evidence.",
    zh: "当前最可能的三个选择是什么？请提供证据。",
  },
  {
    phase: 1,
    en: "What are the likely next three BP actions?",
    zh: "接下来的三个 BP 操作可能是什么？",
  },
  {
    phase: 1,
    en: "What are the top five priority heroes this season?",
    zh: "本赛季优先级最高的五名英雄是谁？",
  },
  {
    phase: 1,
    en: "Which heroes are commonly picked with 鲁班大师?",
    zh: "哪些英雄最常与鲁班大师一起选择？",
  },
  {
    phase: 2,
    en: "What does Wolves most often pick from Blue?",
    zh: "重庆狼队在蓝方最常选择什么？",
  },
  {
    phase: 2,
    en: "What are Wolves' most common first three BP actions?",
    zh: "重庆狼队最常见的前三步 BP 操作是什么？",
  },
  {
    phase: 2,
    en: "What does Wolves tend to ban against AG?",
    zh: "重庆狼队对阵 AG 时通常禁用什么？",
  },
  {
    phase: 2,
    en: "Which heroes has 重庆狼队.紫幻 played most this season?",
    zh: "重庆狼队.紫幻本赛季最常使用哪些英雄？",
  },
  {
    phase: 2,
    en: "Which Wolves pick tendencies increased in its last five matches?",
    zh: "重庆狼队最近五场比赛中，哪些选择倾向上升最多？",
  },
  {
    phase: 2,
    en: "What Blue-side hero pairs has Wolves used most often?",
    zh: "重庆狼队在蓝方最常使用哪些英雄组合？",
  },
];

function randomItem(values) {
  return values[Math.floor(Math.random() * values.length)];
}

function randomSuggestionIndexes() {
  const phase1 = suggestionPairs
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.phase === 1)
    .map(({ index }) => index);
  const phase2 = suggestionPairs
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.phase === 2)
    .map(({ index }) => index);
  const required = [randomItem(phase1), randomItem(phase2)];
  const remaining = suggestionPairs
    .map((_, index) => index)
    .filter((index) => !required.includes(index));
  return [...required, randomItem(remaining)].sort(() => Math.random() - 0.5);
}

const suggestionIndexes = ref(randomSuggestionIndexes());
const suggestions = computed(() =>
  suggestionIndexes.value.map((index) =>
    language.value === "zh-CN"
      ? suggestionPairs[index].zh
      : suggestionPairs[index].en
  )
);

function stripInlineMarkdown(value) {
  return value
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^#{1,6}\s+/, "")
    .replace(/^>\s?/, "")
    .replace(/^[-*]\s+/, "• ");
}

function humanReadableAnswer(value) {
  const output = [];
  let tableHeaders = null;
  for (const rawLine of String(value || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || /^-{3,}$/.test(line)) {
      if (output.at(-1) !== "") output.push("");
      continue;
    }
    if (line.startsWith("|") && line.endsWith("|")) {
      const cells = line
        .slice(1, -1)
        .split("|")
        .map((cell) => stripInlineMarkdown(cell.trim()));
      if (cells.every((cell) => /^:?-{3,}:?$/.test(cell))) continue;
      if (!tableHeaders) {
        tableHeaders = cells;
        continue;
      }
      const details = cells.slice(1).map((cell, index) => {
        const label = tableHeaders[index + 1];
        return label ? `${label}: ${cell}` : cell;
      });
      output.push(`${cells[0]}. ${details.join("; ")}`);
      continue;
    }
    tableHeaders = null;
    output.push(stripInlineMarkdown(line));
  }
  return output.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function useSuggestion(value) {
  submitQuestion(value);
}

async function scrollThreadToBottom() {
  await nextTick();
  if (thread.value) thread.value.scrollTop = thread.value.scrollHeight;
}

function clearHistory() {
  if (loading.value) return;
  messages.value = [];
  question.value = "";
  window.sessionStorage.removeItem(sessionHistoryKey);
}

function isContextStale(message) {
  return Boolean(message.response && message.context !== contextKey.value);
}

async function submitQuestion(suggestedQuestion = null) {
  const message = String(suggestedQuestion ?? question.value).trim();
  if (!message || loading.value || !props.leagueId) return;
  loading.value = true;
  question.value = "";
  const history = messages.value
    .filter((item) => item.response?.answer)
    .slice(-6)
    .map((item) => ({
      user: item.question,
      assistant: item.response.answer,
    }));
  const entry = {
    id: ++messageId,
    question: message,
    context: contextKey.value,
    response: null,
    error: "",
    loading: true,
  };
  messages.value.push(entry);
  const activeEntry = messages.value[messages.value.length - 1];
  await scrollThreadToBottom();
  try {
    activeEntry.response = await askDraftCoach({
      message,
      league_id: props.leagueId,
      draft_state: props.draftState,
      history,
    });
  } catch (err) {
    activeEntry.error = err.retryAfter
      ? language.value === "zh-CN"
        ? `BP 教练正忙，请在 ${err.retryAfter} 秒后重试。`
        : `The Draft Coach is busy. Try again in ${err.retryAfter} second${err.retryAfter === 1 ? "" : "s"}.`
      : err.message || "The Draft Coach could not answer this question.";
  } finally {
    activeEntry.loading = false;
    loading.value = false;
    persistSessionHistory(messages.value);
    await scrollThreadToBottom();
  }
}

watch([loading, messages], scrollThreadToBottom, { deep: true });
watch(
  messages,
  persistSessionHistory,
  { deep: true }
);
</script>

<template>
  <section class="coach-panel" aria-labelledby="draft-coach-title">
    <header class="coach-header">
      <div>
        <p class="coach-eyebrow"><i></i> AI · Evidence-backed</p>
        <h2 id="draft-coach-title">Draft Coach</h2>
      </div>
      <div class="coach-context" :class="{ active: hasBoardContext }">
        <span>{{ hasBoardContext ? "Board attached" : "Season context" }}</span>
        <small v-if="draftState" data-i18n-ignore>
          BP {{ draftState.bp_order }} · {{ draftState.model_type }}
        </small>
      </div>
      <button
        v-if="messages.length"
        type="button"
        class="clear-chat"
        :disabled="loading"
        @click="clearHistory"
      >
        Clear
      </button>
    </header>

    <div ref="thread" class="coach-thread" aria-live="polite">
      <div v-if="!messages.length && !loading" class="coach-welcome">
        <span class="coach-mark">AI</span>
        <h3>Ask about this draft</h3>
        <p>
          Ask a KPL question. Kimi uses approved local tools and attaches the
          current season and board automatically.
        </p>
        <div class="coach-suggestions" aria-label="Suggested questions">
          <button
            v-for="suggestion in suggestions"
            :key="suggestion"
            type="button"
            :disabled="loading"
            data-i18n-ignore
            @click="useSuggestion(suggestion)"
          >
            {{ suggestion }}
          </button>
        </div>
      </div>

      <div
        v-for="message in messages"
        :key="message.id"
        class="conversation-turn"
      >
        <div class="coach-message user-message">
          <span>You</span>
          <p data-i18n-ignore>{{ message.question }}</p>
        </div>

        <div
          v-if="message.loading"
          class="coach-message assistant-message loading-message"
        >
          <span>Draft Coach</span>
          <p>Kimi is selecting evidence tools and preparing an answer…</p>
          <i><b></b><b></b><b></b></i>
        </div>

        <p v-if="message.error" class="coach-alert error" role="alert">
          {{ message.error }}
        </p>

        <article
          v-if="message.response"
          class="coach-message assistant-message coach-response"
          :class="{ stale: isContextStale(message) }"
        >
          <header>
            <div>
              <span>Draft Coach</span>
              <small data-i18n-ignore>{{ message.response.model }}</small>
            </div>
            <span v-if="isContextStale(message)" class="stale-label">Board changed</span>
          </header>
          <p class="coach-answer" data-i18n-ignore>
            {{ humanReadableAnswer(message.response.answer) }}
          </p>

          <ul v-if="message.response.warnings?.length" class="coach-warnings">
            <li
              v-for="warning in message.response.warnings"
              :key="warning"
              data-i18n-ignore
            >
              {{ warning }}
            </li>
          </ul>

          <footer>
            <span data-i18n-ignore>
              {{ Number(message.response.usage?.total_tokens || 0).toLocaleString() }} tokens
            </span>
            <span data-i18n-ignore>
              {{ message.response.request_id.slice(0, 8) }}
            </span>
          </footer>
        </article>
      </div>
    </div>

    <form class="coach-form" @submit.prevent="submitQuestion()">
      <label class="sr-only" for="coach-question">Your question</label>
      <textarea
        id="coach-question"
        v-model="question"
        rows="2"
        maxlength="4000"
        placeholder="Ask a KPL draft question…"
        :disabled="loading"
        @keydown.ctrl.enter.prevent="submitQuestion()"
        @keydown.meta.enter.prevent="submitQuestion()"
      ></textarea>
      <button type="submit" :disabled="loading || !question.trim() || !leagueId" aria-label="Ask Draft Coach">
        <span>{{ loading ? "…" : "↑" }}</span>
      </button>
      <small data-i18n-ignore>
        {{ seasonName || leagueId }} · {{ t("context attached") }}
        <template v-if="answeredCount"> · {{ answeredCount }} {{ t("answered") }}</template>
      </small>
    </form>

    <p class="coach-disclaimer">
      KPL questions only · Historical draft behavior, not battle-win probability.
    </p>
  </section>
</template>

<style scoped>
.coach-panel { display:grid; grid-template-rows:auto minmax(260px, 1fr) auto auto; height:min(760px, calc(100vh - 2rem)); min-height:580px; overflow:hidden; border:1px solid var(--accent-deep); background:#f4f7f5; color:var(--ink); box-shadow:0 18px 42px rgba(16,42,46,.14); }
.coach-header { display:flex; align-items:center; gap:.65rem; padding:.9rem 1rem; border-bottom:1px solid rgba(255,255,255,.12); background:linear-gradient(135deg, #084f42, #102a2e); color:#f7fbf8; }.coach-header > div:first-child { margin-right:auto; }
.coach-eyebrow { display:flex; align-items:center; gap:.35rem; margin:0 0 .25rem; color:#8fe0c8; font-size:.57rem; letter-spacing:.13em; text-transform:uppercase; }.coach-eyebrow i { width:.45rem; height:.45rem; border-radius:50%; background:#8fe0c8; box-shadow:0 0 0 3px rgba(143,224,200,.12); }
.coach-header h2 { margin:0; font:700 1.3rem var(--display); letter-spacing:-.04em; }
.coach-context { display:grid; gap:.1rem; padding:.38rem .48rem; border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.06); text-align:right; }.coach-context.active { border-color:rgba(143,224,200,.55); }.coach-context span, .coach-context small { color:rgba(247,251,248,.68); font-size:.54rem; letter-spacing:.08em; text-transform:uppercase; }
.clear-chat { padding:.38rem .48rem; border:1px solid rgba(255,255,255,.2); background:transparent; color:rgba(247,251,248,.72); font:600 .54rem var(--mono); letter-spacing:.06em; text-transform:uppercase; cursor:pointer; }.clear-chat:hover:not(:disabled) { border-color:#8fe0c8; color:#fff; }.clear-chat:disabled { cursor:default; opacity:.4; }
.coach-thread { min-height:0; padding:1rem; overflow:auto; background:linear-gradient(180deg, #f7faf8, #eef3f0); }
.conversation-turn + .conversation-turn { margin-top:1rem; padding-top:1rem; border-top:1px solid rgba(16,42,46,.08); }
.coach-welcome { display:grid; justify-items:center; padding:1.25rem .5rem; text-align:center; }.coach-mark { display:grid; place-items:center; width:2.5rem; height:2.5rem; border-radius:50%; background:var(--ink); color:#8fe0c8; font:700 .65rem var(--mono); }.coach-welcome h3 { margin:.7rem 0 .3rem; font:700 1rem var(--display); }.coach-welcome > p { max-width:18rem; margin:0; color:var(--ink-soft); font-size:.68rem; line-height:1.55; }
.coach-suggestions { display:grid; width:100%; gap:.35rem; margin-top:1rem; }.coach-suggestions button { padding:.55rem .65rem; border:1px solid var(--line); background:rgba(255,255,255,.82); color:var(--ink-soft); font:inherit; font-size:.63rem; line-height:1.4; text-align:left; cursor:pointer; }.coach-suggestions button:hover:not(:disabled) { border-color:var(--accent-deep); color:var(--ink); }.coach-suggestions button:disabled { cursor:default; opacity:.5; }
.coach-message { max-width:92%; margin-bottom:.75rem; }.coach-message > span, .assistant-message > header span { display:block; margin-bottom:.25rem; color:var(--ink-soft); font-size:.56rem; letter-spacing:.08em; text-transform:uppercase; }.coach-message > p { margin:0; font-size:.7rem; line-height:1.58; }.user-message { margin-left:auto; }.user-message > span { text-align:right; }.user-message > p { padding:.65rem .75rem; border-radius:12px 12px 2px 12px; background:var(--accent-deep); color:#fff; }
.assistant-message { padding:.72rem .78rem; border:1px solid var(--line); border-radius:2px 12px 12px 12px; background:#fff; }.assistant-message > header { display:flex; align-items:start; justify-content:space-between; gap:.5rem; }.assistant-message > header > div { display:flex; align-items:baseline; gap:.45rem; }.assistant-message > header span { margin:0; color:var(--accent-deep); }.assistant-message > header small { color:var(--ink-soft); font-size:.54rem; }
.loading-message i { display:flex; gap:.2rem; margin-top:.5rem; }.loading-message b { width:.35rem; height:.35rem; border-radius:50%; background:var(--accent); animation:coach-pulse 1s infinite alternate; }.loading-message b:nth-child(2) { animation-delay:.2s; }.loading-message b:nth-child(3) { animation-delay:.4s; }@keyframes coach-pulse { to { opacity:.25; transform:translateY(-2px); } }
.coach-alert { margin:0 0 .75rem; padding:.65rem .75rem; border-left:3px solid #e27b47; background:#fff0df; color:#8e4318; font-size:.67rem; }
.coach-response.stale { border-color:#e7a36c; }.stale-label { padding:.17rem .28rem; border-radius:20px; background:#fff0df; color:#9a4d1c !important; font-size:.52rem !important; white-space:nowrap; }.coach-answer { margin:.55rem 0 0 !important; white-space:pre-wrap; }
.coach-warnings { margin:.65rem 0 0; padding:.55rem .6rem .55rem 1.5rem; background:#fff0df; color:#8e4318; font-size:.61rem; }
.coach-response > footer { display:flex; justify-content:space-between; gap:.5rem; margin-top:.65rem; padding-top:.5rem; border-top:1px solid var(--line); color:var(--ink-soft); font-size:.52rem; }
.coach-form { position:relative; display:grid; grid-template-columns:1fr auto; gap:.4rem; padding:.8rem .8rem .55rem; border-top:1px solid var(--line); background:#fff; }.coach-form textarea { width:100%; min-height:58px; max-height:120px; resize:none; padding:.62rem 2.5rem .62rem .7rem; border:1px solid var(--line); border-radius:8px; outline:none; background:#f8faf9; color:var(--ink); font:inherit; font-size:.7rem; line-height:1.45; }.coach-form textarea:focus { border-color:var(--accent-deep); box-shadow:0 0 0 2px rgba(8,79,66,.08); }.coach-form button { align-self:end; width:2.15rem; height:2.15rem; margin:0 0 .38rem -3rem; border:0; border-radius:50%; background:var(--accent-deep); color:#fff; font:700 1rem var(--mono); cursor:pointer; }.coach-form button:disabled { cursor:default; opacity:.35; }.coach-form > small { grid-column:1 / -1; color:var(--ink-soft); font-size:.52rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.coach-disclaimer { margin:0; padding:0 .8rem .7rem; background:#fff; color:var(--ink-soft); font-size:.51rem; }.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media (max-width:1000px) { .coach-panel { height:auto; min-height:520px; max-height:700px; }.coach-thread { min-height:260px; } }
@media (max-width:620px) { .coach-panel { min-height:500px; }.coach-header { align-items:flex-start; }.coach-context { max-width:9rem; }.coach-message { max-width:96%; } }
</style>
