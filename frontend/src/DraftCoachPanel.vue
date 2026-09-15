<script setup>
import { computed, nextTick, ref, watch } from "vue";

import { askDraftCoach, askDraftCoachStream, clearCoachConversation, prepareScoutReport } from "./api";
import { coachErrorCopy } from "./coachStream";
import { language } from "./i18n";

const props = defineProps({
  leagueId: { type: String, required: true },
  seasonName: { type: String, default: "" },
  draftState: { type: Object, default: null },
  forceChinese: { type: Boolean, default: false },
});

const sessionHistoryKey = "kpl-draft-coach-session-history";
const legacyHistoryKeys = [
  `${sessionHistoryKey}-draft`,
  `${sessionHistoryKey}-research`,
];

function readStoredMessages(key) {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(key) || "[]");
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function loadSessionHistory() {
  const unified = readStoredMessages(sessionHistoryKey);
  if (unified.length) return unified.slice(-20);
  const merged = legacyHistoryKeys.flatMap((key) => readStoredMessages(key));
  return merged.slice(-20).map((message, index) => ({
    ...message,
    id: index + 1,
  }));
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
      scoutReport: Boolean(message.scoutReport),
      response: message.response
        ? {
            request_id: message.response.request_id,
            model: message.response.model,
            answer: message.response.answer,
            evidence: message.response.evidence || [],
            evidence_cards: message.response.evidence_cards || [],
            warnings: message.response.warnings || [],
            usage: message.response.usage || {},
            status: message.response.status,
            response_mode: message.response.response_mode,
            sections: message.response.sections || [],
            follow_up_actions: message.response.follow_up_actions || [],
            conversation_id: message.response.conversation_id,
          }
        : null,
    }));
  try {
    window.sessionStorage.setItem(sessionHistoryKey, JSON.stringify(completed));
  } catch {
    // Session storage can be full or unavailable; keep the in-memory thread.
  }
}

const question = ref("");
const loading = ref(false);
const stopping = ref(false);
const responseMode = ref("quick");
const streamProgress = ref("");
const restoredMessages = loadSessionHistory();
const conversationId = ref(
  [...restoredMessages].reverse().find((message) => message.response?.conversation_id)
    ?.response?.conversation_id || ""
);
const messages = ref(restoredMessages);
let activeController = null;
const thread = ref(null);
let messageId = Math.max(0, ...messages.value.map((message) => Number(message.id) || 0));
const isChinese = computed(() => props.forceChinese || language.value === "zh-CN");

const contextKey = computed(() =>
  JSON.stringify({ league_id: props.leagueId, draft_state: props.draftState })
);
const hasBoardContext = computed(() => Boolean(props.draftState));
const canPrepareScoutReport = computed(() =>
  Boolean(
    props.draftState?.blue_team_id &&
      props.draftState?.red_team_id &&
      props.draftState?.blue_team_name &&
      props.draftState?.red_team_name
  )
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

const researchSuggestions = [
  {
    en: "What official changes did Liu Bei receive in the August 2026 patch?",
    zh: "刘备在 2026 年 8 月的官方版本中有哪些调整？",
  },
  {
    en: "Find the official patch notes for Liu Bei's mana-cost adjustment.",
    zh: "请查找刘备蓝耗调整的官方版本公告。",
  },
  {
    en: "What equipment changes are in the latest official patch notes?",
    zh: "最近的官方版本公告有哪些装备调整？",
  },
  {
    en: "What official patch changes might be relevant before discussing a hero's BP priority?",
    zh: "在讨论英雄 BP 优先级前，有哪些官方版本调整值得参考？",
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
  return {
    draft: [randomItem(phase1), randomItem(phase2)],
    research: Math.floor(Math.random() * researchSuggestions.length),
  };
}

const suggestionPick = ref(randomSuggestionIndexes());
const suggestions = computed(() => {
  const draft = suggestionPick.value.draft.map((index) =>
    isChinese.value ? suggestionPairs[index].zh : suggestionPairs[index].en
  );
  const research = researchSuggestions[suggestionPick.value.research];
  const items = [
    ...draft.map((text) => ({ kind: "chat", text })),
    { kind: "chat", text: isChinese.value ? research.zh : research.en },
  ];
  if (canPrepareScoutReport.value) {
    items.push({ kind: "scout", text: scoutReportLabel.value });
  }
  return items;
});

const welcomeTitle = computed(() =>
  isChinese.value ? "询问 BP 教练" : "Ask the Draft Coach"
);
const welcomeCopy = computed(() =>
  isChinese.value
    ? "在同一对话里询问当前 BP、战队倾向或官方版本改动。选择双方后，也可以生成赛前侦察报告。回答会附带本地工具证据或腾讯官方来源。"
    : "Ask about this draft, team tendencies, or official patch changes in one thread. After both teams are selected, you can also prepare a pre-match scout report. Answers cite local tools or Tencent announcements."
);
const composerPlaceholder = computed(() =>
  isChinese.value
    ? "询问 BP、战队、英雄或官方版本调整…"
    : "Ask about the draft, a team, a hero, or an official patch…"
);
const scoutReportLabel = computed(() =>
  isChinese.value ? "生成对阵侦察报告" : "Prepare scout report"
);
const scoutReportBadge = computed(() =>
  isChinese.value ? "侦察报告" : "Scout report"
);
const contextLabel = computed(() =>
  hasBoardContext.value
    ? isChinese.value
      ? "已附加 BP 面板"
      : "Board attached"
    : isChinese.value
      ? "赛季上下文"
      : "Season context"
);
const scoutReportQuestion = computed(() => {
  const blue = props.draftState?.blue_team_name || (isChinese.value ? "蓝方" : "Blue");
  const red = props.draftState?.red_team_name || (isChinese.value ? "红方" : "Red");
  return isChinese.value
    ? `生成 ${blue} 对阵 ${red} 的赛前侦察报告`
    : `Prepare a scout report: ${blue} vs ${red}`;
});

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

function answerRows(value) {
  const answer = humanReadableAnswer(value);
  const wrappedLines = answer
    .split(/\r?\n/)
    .reduce((total, line) => total + Math.max(1, Math.ceil(line.length / 42)), 0);
  return Math.min(18, Math.max(5, wrappedLines));
}

function useSuggestion(suggestion) {
  if (suggestion.kind === "scout") {
    submitScoutReport();
    return;
  }
  submitQuestion(suggestion.text);
}

function newClientRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `req-${Date.now().toString(16)}`;
}

function stopWaiting() {
  stopping.value = true;
  activeController?.abort();
}

function retryQuestion(message) {
  question.value = message.question;
  submitQuestion(message.question);
}

function editQuestion(message) {
  question.value = message.question;
}

function useFollowUp(action, message) {
  const text = action?.prompt || action?.label || "";
  if (!text) return;
  submitQuestion(text);
}

function handleComposerKeydown(event) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  submitQuestion();
}

async function scrollThreadToBottom() {
  await nextTick();
  if (thread.value) thread.value.scrollTop = thread.value.scrollHeight;
}

function clearHistory() {
  if (loading.value) return;
  messages.value = [];
  question.value = "";
  conversationId.value = "";
  try {
    window.sessionStorage.removeItem(sessionHistoryKey);
    for (const key of legacyHistoryKeys) {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // Storage may be unavailable; server-side clearing still proceeds.
  }
  clearCoachConversation().catch(() => {});
}

function loadingCopy(message) {
  if (message.scoutReport) {
    return isChinese.value
      ? "正在整理双方证据并生成侦察报告…"
      : "Collecting both teams’ evidence and writing the scout report…";
  }
  return isChinese.value
    ? "Kimi 正在选择证据工具并准备回答…"
    : "Kimi is choosing evidence tools and preparing an answer…";
}

function isContextStale(message) {
  return Boolean(message.response && message.context !== contextKey.value);
}

async function submitQuestion(suggestedQuestion = null) {
  const message = String(suggestedQuestion ?? question.value).trim();
  if (!message || loading.value || !props.leagueId) return;
  loading.value = true;
  stopping.value = false;
  streamProgress.value = "";
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
  const payload = {
    message,
    league_id: props.leagueId,
    draft_state: props.draftState,
    history,
    response_mode: responseMode.value,
    client_request_id: newClientRequestId(),
  };
  if (conversationId.value) payload.conversation_id = conversationId.value;
  activeController = new AbortController();
  try {
    const streamed = await askDraftCoachStream(payload, {
      signal: activeController.signal,
      onEvent(event) {
        if (event.type === "progress" && event.message) {
          streamProgress.value = event.message;
        }
        if (event.type === "result" && event.data?.conversation_id) {
          conversationId.value = event.data.conversation_id;
        }
      },
    });
    if (!streamed) {
      const error = new Error("The Draft Coach stream ended before a result was available.");
      error.code = "coach_incomplete";
      throw error;
    }
    activeEntry.response = streamed;
    if (streamed.conversation_id) conversationId.value = streamed.conversation_id;
  } catch (err) {
    if (err.code === "streaming_disabled") {
      activeEntry.response = await askDraftCoach(payload);
      if (activeEntry.response?.conversation_id) conversationId.value = activeEntry.response.conversation_id;
      return;
    }
    if (err.name === "AbortError") {
      activeEntry.error = isChinese.value ? "已停止等待。" : "Stopped waiting.";
    } else if (err.retryAfter) {
      activeEntry.error = isChinese.value
        ? `BP 教练正忙，请在 ${err.retryAfter} 秒后重试。`
        : `The Draft Coach is busy. Try again in ${err.retryAfter} second${err.retryAfter === 1 ? "" : "s"}.`;
    } else {
      activeEntry.error = coachErrorCopy(
        { code: err.code, message: err.message },
        isChinese.value
      );
    }
  } finally {
    activeEntry.loading = false;
    loading.value = false;
    stopping.value = false;
    streamProgress.value = "";
    activeController = null;
    persistSessionHistory(messages.value);
    await scrollThreadToBottom();
  }
}

async function submitScoutReport() {
  if (!canPrepareScoutReport.value || loading.value || !props.leagueId) return;
  const state = props.draftState;
  loading.value = true;
  const entry = {
    id: ++messageId,
    question: scoutReportQuestion.value,
    context: contextKey.value,
    response: null,
    error: "",
    loading: true,
    scoutReport: true,
  };
  messages.value.push(entry);
  const activeEntry = messages.value[messages.value.length - 1];
  await scrollThreadToBottom();
  try {
    activeEntry.response = await prepareScoutReport({
      league_id: props.leagueId,
      blue_team_id: state.blue_team_id,
      blue_team_name: state.blue_team_name,
      red_team_id: state.red_team_id,
      red_team_name: state.red_team_name,
      language: isChinese.value ? "zh-CN" : "en",
    });
  } catch (err) {
    activeEntry.error = err.retryAfter
      ? isChinese.value
        ? `BP 教练正忙，请在 ${err.retryAfter} 秒后重试。`
        : `The Draft Coach is busy. Try again in ${err.retryAfter} second${err.retryAfter === 1 ? "" : "s"}.`
      : isChinese.value
        ? "BP 教练暂时无法生成该侦察报告。"
        : err.message || "The Draft Coach could not prepare this scout report.";
  } finally {
    activeEntry.loading = false;
    loading.value = false;
    persistSessionHistory(messages.value);
    await scrollThreadToBottom();
  }
}

watch([loading, messages], scrollThreadToBottom, { deep: true });
watch(messages, (value) => persistSessionHistory(value), { deep: true });
</script>

<template>
  <section class="coach-panel" aria-labelledby="draft-coach-title">
    <header class="coach-header">
      <div>
        <p class="coach-eyebrow"><i></i>{{ $t("AI · 证据支持") }}</p>
        <h2 id="draft-coach-title">{{ $t("BP 教练") }}</h2>
      </div>
      <div class="coach-context" :class="{ active: hasBoardContext }">
        <span>{{ contextLabel }}</span>
        <small v-if="draftState">{{ $t("BP") }}{{ draftState.bp_order }} · {{ draftState.model_type }}
        </small>
      </div>
      <button
        v-if="messages.length"
        type="button"
        class="clear-chat"
        :disabled="loading"
        @click="clearHistory"
      >
        清空
      </button>
    </header>

    <div ref="thread" class="coach-thread" aria-live="polite">
      <div v-if="!messages.length && !loading" class="coach-welcome">
        <span class="coach-mark">{{ $t("AI") }}</span>
        <h3>{{ welcomeTitle }}</h3>
        <p>{{ welcomeCopy }}</p>
        <div class="coach-suggestions" aria-label="推荐问题">
          <button
            v-for="suggestion in suggestions"
            :key="suggestion.text"
            type="button"
            :class="{ 'scout-suggestion': suggestion.kind === 'scout' }"
            :disabled="loading"

            @click="useSuggestion(suggestion)"
          >
            {{ suggestion.text }}
          </button>
        </div>
      </div>

      <div
        v-for="message in messages"
        :key="message.id"
        class="conversation-turn"
      >
        <div class="coach-message user-message">
          <span>你</span>
          <p>{{ message.question }}</p>
        </div>

        <div
          v-if="message.loading"
          class="coach-message assistant-message loading-message"
        >
          <span>{{ $t("BP 教练") }}</span>
          <p>{{ streamProgress || loadingCopy(message) }}</p>
          <i><b></b><b></b><b></b></i>
          <button type="button" class="turn-action" @click="stopWaiting">
            {{ isChinese ? "停止" : "Stop" }}
          </button>
        </div>

        <div v-if="message.error" class="coach-alert error" role="alert">
          <p>{{ message.error }}</p>
          <div class="turn-actions">
            <button type="button" :disabled="loading" @click="retryQuestion(message)">
              {{ isChinese ? "重试" : "Retry" }}
            </button>
            <button type="button" :disabled="loading" @click="editQuestion(message)">
              {{ isChinese ? "编辑" : "Edit" }}
            </button>
          </div>
        </div>

        <article
          v-if="message.response"
          class="coach-message assistant-message coach-response"
          :class="{ stale: isContextStale(message) }"
        >
          <header>
            <div><span>{{ $t("BP 教练") }}</span></div>
            <div class="response-badges">
              <span v-if="message.scoutReport" class="report-label">{{ scoutReportBadge }}</span>
              <span v-if="isContextStale(message)" class="stale-label">{{ $t("BP 面板已变化") }}</span>
            </div>
          </header>
          <textarea
            class="coach-answer-box"
            :value="humanReadableAnswer(message.response.answer)"
            :rows="answerRows(message.response.answer)"
            :aria-label="isChinese ? 'BP 教练回答' : 'Draft Coach answer'"
            readonly
          ></textarea>

          <div v-if="message.response.follow_up_actions?.length" class="follow-up-actions">
            <button
              v-for="action in message.response.follow_up_actions"
              :key="action.id"
              type="button"
              :disabled="loading"
              @click="useFollowUp(action, message)"
            >
              <strong>{{ action.label }}</strong>
              <small v-if="action.description">{{ action.description }}</small>
            </button>
          </div>
        </article>
      </div>
    </div>

    <form class="coach-form" @submit.prevent="submitQuestion()">
      <div class="composer-actions">
        <div class="response-mode" role="group" :aria-label="isChinese ? '回答模式' : 'Response mode'">
          <button type="button" :class="{ active: responseMode === 'quick' }" :disabled="loading" @click="responseMode = 'quick'">
            {{ isChinese ? "简洁" : "Quick" }}
          </button>
          <button type="button" :class="{ active: responseMode === 'analysis' }" :disabled="loading" @click="responseMode = 'analysis'">
            {{ isChinese ? "分析" : "Analysis" }}
          </button>
        </div>
        <button
          v-if="canPrepareScoutReport"
          type="button"
          class="composer-scout"
          :disabled="loading"
          @click="submitScoutReport"
        >
          {{ scoutReportLabel }}
        </button>
      </div>
      <label class="sr-only" for="coach-question">你的问题</label>
      <textarea
        id="coach-question"
        v-model="question"
        rows="2"
        maxlength="4000"
        :placeholder="composerPlaceholder"
        :disabled="loading"
        @keydown="handleComposerKeydown"
      ></textarea>
      <button type="submit" :disabled="loading || !question.trim() || !leagueId" :aria-label="$t('询问 BP 教练')">
        <span>{{ loading ? "…" : "↑" }}</span>
      </button>
    </form>
  </section>
</template>

<style scoped>
.coach-panel { display:grid; grid-template-rows:auto minmax(260px, 1fr) auto; height:min(760px, calc(100vh - 2rem)); min-height:580px; overflow:hidden; border:1px solid var(--accent-deep); background:#f4f7f5; color:var(--ink); box-shadow:0 18px 42px rgba(16,42,46,.14); }
.coach-header { display:flex; align-items:center; gap:.65rem; padding:.9rem 1rem; border-bottom:1px solid rgba(255,255,255,.12); background:linear-gradient(135deg, #084f42, #102a2e); color:#f7fbf8; }.coach-header > div:first-child { margin-right:auto; }
.coach-eyebrow { display:flex; align-items:center; gap:.35rem; margin:0 0 .25rem; color:#8fe0c8; font-size:.57rem; letter-spacing:.13em; text-transform:uppercase; }.coach-eyebrow i { width:.45rem; height:.45rem; border-radius:50%; background:#8fe0c8; box-shadow:0 0 0 3px rgba(143,224,200,.12); }
.coach-header h2 { margin:0; font:700 1.3rem var(--display); letter-spacing:-.04em; }
.coach-context { display:grid; gap:.1rem; padding:.38rem .48rem; border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.06); text-align:right; }.coach-context.active { border-color:rgba(143,224,200,.55); }.coach-context span, .coach-context small { color:rgba(247,251,248,.68); font-size:.54rem; letter-spacing:.08em; text-transform:uppercase; }
.clear-chat { padding:.38rem .48rem; border:1px solid rgba(255,255,255,.2); background:transparent; color:rgba(247,251,248,.72); font:600 .54rem var(--mono); letter-spacing:.06em; text-transform:uppercase; cursor:pointer; }.clear-chat:hover:not(:disabled) { border-color:#8fe0c8; color:#fff; }.clear-chat:disabled { cursor:default; opacity:.4; }
.coach-thread { min-height:0; padding:1rem; overflow:auto; background:linear-gradient(180deg, #f7faf8, #eef3f0); }
.conversation-turn + .conversation-turn { margin-top:1rem; padding-top:1rem; border-top:1px solid rgba(16,42,46,.08); }
.coach-welcome { display:grid; justify-items:center; padding:1.25rem .5rem; text-align:center; }.coach-mark { display:grid; place-items:center; width:2.5rem; height:2.5rem; border-radius:50%; background:var(--ink); color:#8fe0c8; font:700 .65rem var(--mono); }.coach-welcome h3 { margin:.7rem 0 .3rem; font:700 1rem var(--display); }.coach-welcome > p { max-width:22rem; margin:0; color:var(--ink-soft); font-size:.68rem; line-height:1.55; }
.coach-suggestions { display:grid; width:100%; gap:.35rem; margin-top:1rem; }.coach-suggestions button { padding:.55rem .65rem; border:1px solid var(--line); background:rgba(255,255,255,.82); color:var(--ink-soft); font:inherit; font-size:.63rem; line-height:1.4; text-align:left; cursor:pointer; }.coach-suggestions button:hover:not(:disabled) { border-color:var(--accent-deep); color:var(--ink); }.coach-suggestions button:disabled { cursor:default; opacity:.5; }.coach-suggestions .scout-suggestion { border-color:rgba(8,79,66,.35); background:linear-gradient(135deg,#e7f4ee,#fff); color:var(--accent-deep); font-weight:700; }
.coach-message { max-width:92%; margin-bottom:.75rem; }.coach-message > span, .assistant-message > header span { display:block; margin-bottom:.25rem; color:var(--ink-soft); font-size:.56rem; letter-spacing:.08em; text-transform:uppercase; }.coach-message > p { margin:0; font-size:.7rem; line-height:1.58; }.user-message { margin-left:auto; }.user-message > span { text-align:right; }.user-message > p { padding:.65rem .75rem; border-radius:12px 12px 2px 12px; background:var(--accent-deep); color:#fff; }
.assistant-message { padding:.72rem .78rem; border:1px solid var(--line); border-radius:2px 12px 12px 12px; background:#fff; }.assistant-message > header { display:flex; align-items:start; justify-content:space-between; gap:.5rem; }.assistant-message > header > div { display:flex; align-items:baseline; gap:.45rem; }.assistant-message > header span { margin:0; color:var(--accent-deep); }.assistant-message > header small { color:var(--ink-soft); font-size:.54rem; }.response-badges { display:flex; flex-wrap:wrap; justify-content:end; gap:.28rem; }
.loading-message i { display:flex; gap:.2rem; margin-top:.5rem; }.loading-message b { width:.35rem; height:.35rem; border-radius:50%; background:var(--accent); animation:coach-pulse 1s infinite alternate; }.loading-message b:nth-child(2) { animation-delay:.2s; }.loading-message b:nth-child(3) { animation-delay:.4s; }@keyframes coach-pulse { to { opacity:.25; transform:translateY(-2px); } }
.coach-alert { margin:0 0 .75rem; padding:.65rem .75rem; border-left:3px solid #e27b47; background:#fff0df; color:#8e4318; font-size:.67rem; }.coach-alert p { margin:0; }.turn-actions { display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.5rem; }.turn-actions button,.turn-action { padding:.3rem .48rem; border:1px solid currentColor; border-radius:999px; background:transparent; color:inherit; font:700 .54rem var(--mono); cursor:pointer; }.turn-action { margin-top:.5rem; color:var(--accent-deep); }.turn-actions button:disabled,.follow-up-actions button:disabled { opacity:.45; cursor:default; }
.coach-response.stale { border-color:#e7a36c; }.stale-label, .report-label, .status-label { padding:.17rem .28rem; border-radius:20px; font-size:.52rem !important; white-space:nowrap; }.stale-label { background:#fff0df; color:#9a4d1c !important; }.report-label { background:#e7f4ee; color:var(--accent-deep) !important; }.status-label { background:#eef1ef; color:var(--ink-soft) !important; text-transform:capitalize; }.coach-answer { margin:.55rem 0 0 !important; white-space:pre-wrap; }
.coach-answer-box { display:block; width:100%; min-height:8rem; max-height:28rem; margin-top:.55rem; padding:.7rem .75rem; resize:vertical; overflow:auto; border:1px solid var(--line); border-radius:8px; outline:none; background:#f8faf9; color:var(--ink); font:inherit; font-size:.69rem; line-height:1.62; white-space:pre-wrap; }.coach-answer-box:focus { border-color:var(--accent-deep); box-shadow:0 0 0 2px rgba(8,79,66,.08); }
.follow-up-actions { display:grid; gap:.4rem; margin-top:.65rem; }.follow-up-actions button { display:grid; gap:.18rem; width:100%; padding:.48rem .58rem; border:1px solid rgba(8,79,66,.28); border-radius:8px; background:#f1f8f3; color:var(--accent-deep); text-align:left; cursor:pointer; }.follow-up-actions button strong { font:700 .58rem var(--display); }.follow-up-actions button small { color:var(--ink-soft); font-size:.52rem; line-height:1.35; }
.coach-form { position:relative; display:grid; grid-template-columns:1fr auto; gap:.4rem; padding:.65rem .8rem .8rem; border-top:1px solid var(--line); background:#fff; }.composer-actions { grid-column:1 / -1; display:flex; align-items:center; gap:.45rem; min-width:0; }.coach-form textarea { width:100%; min-height:58px; max-height:120px; resize:none; padding:.62rem 2.5rem .62rem .7rem; border:1px solid var(--line); border-radius:8px; outline:none; background:#f8faf9; color:var(--ink); font:inherit; font-size:.7rem; line-height:1.45; }.coach-form textarea:focus { border-color:var(--accent-deep); box-shadow:0 0 0 2px rgba(8,79,66,.08); }.coach-form button[type="submit"] { align-self:end; width:2.4rem; height:2.4rem; min-height:2.4rem; aspect-ratio:1; margin:0 0 .38rem -3.1rem; padding:0; border:0; border-radius:50%; background:var(--accent-deep); color:#fff; font:700 1rem var(--mono); cursor:pointer; }.coach-form button[type="submit"]:disabled { cursor:default; opacity:.35; }.response-mode { display:flex; padding:.12rem; border:1px solid var(--line); border-radius:999px; }.response-mode button { padding:.24rem .48rem; border:0; border-radius:999px; background:transparent; color:var(--ink-soft); font:700 .54rem var(--mono); cursor:pointer; }.response-mode button.active { background:var(--accent-deep); color:#fff; }.composer-scout { flex:0 1 auto; min-width:0; padding:.28rem .52rem; border:1px solid rgba(8,79,66,.28); border-radius:999px; background:#e7f4ee; color:var(--accent-deep); font:700 .54rem var(--mono); letter-spacing:.03em; cursor:pointer; white-space:nowrap; }.composer-scout:hover:not(:disabled) { border-color:var(--accent-deep); }.composer-scout:disabled { cursor:default; opacity:.5; }.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media (max-width:1000px) { .coach-panel { height:auto; min-height:520px; max-height:700px; }.coach-thread { min-height:260px; } }
@media (max-width:620px) { .coach-panel { min-height:500px; }.coach-header { align-items:flex-start; }.coach-context { max-width:9rem; }.coach-message { max-width:96%; }.coach-form textarea,.coach-answer-box { font-size:16px; }.composer-actions { flex-wrap:wrap; } }
</style>
