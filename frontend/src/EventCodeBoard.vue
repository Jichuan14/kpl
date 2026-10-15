<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fetchEventBoard, submitEventCode, reportInvalidEventCode } from "./api.js";
import { language, t } from "./i18n.js";
import { getStored, setStored } from "./storage.js";

const HIDDEN_CODES_KEY = "draft-atlas-invalid-event-codes";
function readHiddenCodeIds() {
  try {
    const stored = JSON.parse(getStored(HIDDEN_CODES_KEY, "[]"));
    return new Set(Array.isArray(stored) ? stored.filter((id) => Number.isSafeInteger(id) && id > 0) : []);
  } catch { return new Set(); }
}

const active = ref(false);
const config = ref(null);
const codes = ref([]);
const nextCursor = ref(null);
const busy = ref(false);
const editorOpen = ref(false);
const draft = ref("");
const error = ref("");
const notice = ref("");
const copied = ref(null);
const hiddenCodeIds = ref(readHiddenCodeIds());
const controller = new AbortController();
let expiryTimer;
const localized = (key) => config.value?.[`${key}_${language.value === "zh-CN" ? "zh" : "en"}`] || "";
const title = computed(() => localized("title"));
const instructions = computed(() => localized("instructions"));
const placeholder = computed(() => localized("placeholder"));
const endDate = computed(() => config.value?.ends_at
  ? new Intl.DateTimeFormat(language.value, { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai" }).format(new Date(config.value.ends_at))
  : "");

function applyPage(page, append = false) {
  active.value = page.active;
  if (!page.active) { codes.value = []; editorOpen.value = false; return; }
  config.value = page.config;
  hiddenCodeIds.value = new Set([...hiddenCodeIds.value, ...readHiddenCodeIds()]);
  const rows = append ? [...codes.value, ...page.codes] : page.codes;
  codes.value = rows.filter((row, index) => !hiddenCodeIds.value.has(row.id) && rows.findIndex((other) => other.id === row.id) === index);
  nextCursor.value = page.next_cursor;
}

function showError(err) {
  if (err.name === "AbortError") return;
  const messages = {
    duplicate_code: "This code is already on the board.",
    invalid_code: "Enter a code within the allowed length.",
    invalid_share_message: "Paste a complete birthday standee share message from the game.",
    multiple_share_messages: "Paste one share message at a time.",
  };
  if (err.code === "board_closed") {
    active.value = false;
    editorOpen.value = false;
  } else {
    error.value = t(messages[err.code] || (err.status === 429 ? "Too many requests. Try again shortly." : "Could not update the code board. Please try again."));
  }
}

async function load(append = false) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try { applyPage(await fetchEventBoard({ before: append ? nextCursor.value : undefined, signal: controller.signal }), append); }
  catch (err) { showError(err); }
  finally { busy.value = false; }
}

function openSubmission() {
  if (busy.value) return;
  error.value = "";
  notice.value = "";
  editorOpen.value = true;
}

function displayCode(row) {
  if (config.value?.code_format === "birthday_standee") {
    return row.code.match(/【[^【】]+】/u)?.[0] || row.code;
  }
  return row.code;
}

async function save() {
  if (busy.value || !draft.value.trim()) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await submitEventCode(draft.value, { signal: controller.signal });
    draft.value = "";
    editorOpen.value = false;
    notice.value = "Your code is on the board.";
    applyPage(await fetchEventBoard({ signal: controller.signal }));
  } catch (err) { showError(err); }
  finally { busy.value = false; }
}

async function copy(row) {
  error.value = "";
  try {
    await navigator.clipboard.writeText(row.code);
    copied.value = row.id;
    notice.value = "Code copied.";
  } catch {
    error.value = t("Could not copy automatically. Select and copy the code instead.");
  }
}

async function reportInvalid(row) {
  if (busy.value || hiddenCodeIds.value.has(row.id)) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  const previousCodes = codes.value;
  hiddenCodeIds.value = new Set([...readHiddenCodeIds(), ...hiddenCodeIds.value, row.id]);
  setStored(HIDDEN_CODES_KEY, JSON.stringify([...hiddenCodeIds.value]));
  codes.value = codes.value.filter((code) => code.id !== row.id);
  try {
    await reportInvalidEventCode(row.id, { signal: controller.signal });
    notice.value = "Code hidden and reported as invalid.";
  } catch (err) {
    // A failed request is retryable; it must not strand an unsent report behind
    // a permanently hidden row. Other tabs' local hides remain intact.
    hiddenCodeIds.value = new Set([...readHiddenCodeIds(), ...hiddenCodeIds.value].filter((id) => id !== row.id));
    setStored(HIDDEN_CODES_KEY, JSON.stringify([...hiddenCodeIds.value]));
    codes.value = previousCodes.filter((code) => !hiddenCodeIds.value.has(code.id));
    showError(err);
  } finally { busy.value = false; }
}

onMounted(() => {
  load();
  expiryTimer = setInterval(() => {
    if (config.value?.ends_at && Date.now() >= Date.parse(config.value.ends_at)) {
      active.value = false;
      editorOpen.value = false;
    }
  }, 1000);
});
onBeforeUnmount(() => { controller.abort(); clearInterval(expiryTimer); });
</script>

<template>
  <section v-if="active" class="event-code-board" aria-labelledby="event-board-title" :aria-busy="busy">
    <header class="board-header">
      <div>
        <p class="board-eyebrow">{{ t("Player code sharing") }}</p>
        <h2 id="event-board-title">{{ title }}</h2>
        <p class="board-instructions">{{ instructions }}</p>
        <p v-if="endDate" class="board-meta">{{ t("Ends (Beijing time)") }} · {{ endDate }}</p>
      </div>
      <button class="board-primary" type="button" :disabled="busy" @click="openSubmission">
        {{ t("Share my code") }}
      </button>
    </header>

    <form v-if="editorOpen" class="board-editor" @submit.prevent="save">
      <label for="event-board-code">{{ t("Your event code or share message") }}</label>
      <textarea id="event-board-code" v-model="draft" rows="2" :maxlength="config.max_code_length" :placeholder="placeholder" required :disabled="busy" />
      <p v-if="config.code_format === 'birthday_standee'" class="board-meta">{{ t("Paste the complete game share message, including the standee name, six-character code and points. One message at a time.") }}</p>
      <p class="board-meta">{{ t("You can share multiple codes, one at a time. Published codes cannot be edited.") }}</p>
      <div class="board-editor-actions">
        <button class="board-primary" type="submit" :disabled="busy || !draft.trim()">{{ busy ? t("Saving…") : t("Publish code") }}</button>
        <button type="button" :disabled="busy" @click="editorOpen = false">{{ t("Cancel") }}</button>
      </div>
    </form>

    <p v-if="error" class="board-error" role="alert">{{ error }}</p>
    <p class="board-notice" role="status" aria-live="polite">{{ notice ? t(notice) : "" }}</p>
    <div class="board-list-heading">
      <span>{{ t("Newest first") }}</span>
      <button type="button" :disabled="busy" @click="load()">{{ t("Refresh codes") }}</button>
    </div>
    <ul v-if="codes.length" class="board-code-grid" aria-labelledby="event-board-title">
      <li v-for="row in codes" :key="row.id" class="board-code-cell">
        <span class="board-code" dir="auto">{{ displayCode(row) }}</span>
        <div class="board-code-actions">
          <button type="button" :aria-label="`${t('Copy code')} ${displayCode(row)}`" @click="copy(row)">{{ copied === row.id ? t("Copied") : t("Copy") }}</button>
          <button class="board-invalid" type="button" :disabled="busy" :aria-label="`${t('Report as invalid')} ${displayCode(row)}`" @click="reportInvalid(row)">{{ t("Invalid") }}</button>
        </div>
      </li>
    </ul>
    <p v-else class="board-empty">{{ t("No codes yet. Be the first to share yours.") }}</p>
    <footer class="board-footer">
      <div>
        <p class="board-meta">{{ t("Shared by players. Code validity and rewards are not verified.") }}</p>
        <p class="board-meta">{{ t("Reported codes disappear for you; three separate reports remove them from the board.") }}</p>
      </div>
      <button v-if="nextCursor" type="button" :disabled="busy" @click="load(true)">{{ busy ? t("Loading…") : t("Show more") }}</button>
    </footer>
  </section>
</template>

<style scoped>
.event-code-board { order: 1; min-width: 0; margin: 0 0 1.6rem; padding: 1.4rem 1.6rem; border: 1px solid var(--line); border-top: 3px solid var(--accent); background: var(--panel); }
.board-header { display: flex; justify-content: space-between; align-items: start; gap: 1.5rem; }
.board-eyebrow { margin: 0 0 .4rem; color: var(--accent-deep); font-size: .72rem; }
h2 { margin: 0; font-family: var(--display); font-size: clamp(1.4rem, 3vw, 1.9rem); line-height: 1.2; }
.board-instructions { max-width: 60rem; margin: .65rem 0 0; color: var(--ink-soft); font-size: .86rem; }
button { min-height: 40px; padding: .45rem .8rem; border: 1px solid var(--line); color: var(--ink); background: transparent; font: inherit; font-size: .8rem; }
button:hover:not(:disabled) { border-color: var(--accent-deep); color: var(--accent-deep); }
button:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent-deep); outline-offset: 3px; }
button:disabled { opacity: .55; cursor: default; }
button.board-primary { flex-shrink: 0; border-color: var(--accent-deep); background: var(--accent-deep); color: white; }
.board-editor { display: grid; gap: .6rem; margin: 1.2rem 0; padding: 1rem; background: var(--paper); border: 1px solid var(--line); }
.board-editor label { font-weight: 600; font-size: .85rem; }
textarea { width: 100%; min-width: 0; resize: vertical; padding: .75rem; border: 1px solid var(--line); background: white; color: var(--ink); font: inherit; font-size: .9rem; }
.board-editor-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
.board-meta { margin: .4rem 0 0; font-size: .72rem; color: var(--ink-soft); }
.board-error { color: var(--warn); font-size: .85rem; }
.board-notice { margin: .5rem 0; color: var(--accent-deep); font-size: .8rem; }
.board-notice:empty { display: none; }
.board-list-heading { display: flex; align-items: center; justify-content: space-between; margin-top: 1rem; color: var(--ink-soft); font-size: .74rem; }
.board-list-heading button { border-color: transparent; color: var(--ink-soft); font-size: .72rem; }
button.board-invalid { border-color: transparent; color: var(--ink-soft); font-size: .72rem; }
.board-code-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: .4rem 0 0; padding: 0; border-top: 1px solid var(--line); border-left: 1px solid var(--line); list-style: none; }
.board-code-cell { display: flex; flex-direction: column; justify-content: space-between; gap: .65rem; min-width: 0; padding: .8rem .65rem; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.board-code { display: block; user-select: text; overflow-wrap: anywhere; font-size: .85rem; line-height: 1.55; }
.board-code-actions { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .4rem; }
.board-code-actions button { min-width: 44px; min-height: 44px; padding: .35rem .55rem; white-space: nowrap; }
.board-empty { margin: 1rem 0; color: var(--ink-soft); font-size: .86rem; }
.board-footer { display: flex; gap: 1rem; justify-content: space-between; align-items: center; margin-top: .9rem; }
.board-footer button { flex-shrink: 0; }
@media (max-width: 1100px) {
  .board-code-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 700px) {
  .event-code-board { padding: 1rem; }
  .board-header { flex-direction: column; gap: 1rem; }
  .board-code-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .board-code-cell { padding: .65rem .4rem; }
  .board-code-actions { gap: .25rem; }
  .board-code-actions button { padding-right: .35rem; padding-left: .35rem; }
  .board-footer { align-items: start; flex-direction: column; }
  button { min-height: 44px; }
}
</style>
