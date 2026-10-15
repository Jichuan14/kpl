import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse } from "@vue/compiler-sfc";
import { computed, ref } from "vue";
import { fetchEventBoard, submitEventCode, reportInvalidEventCode } from "./api.js";

const flush = () => new Promise((resolve) => setImmediate(resolve));
const rows = (first, last) => Array.from({ length: first - last + 1 }, (_, index) => ({ id: first - index, code: `CODE-${first - index}` }));
const page = (codes = [], next = null) => ({ active: true, config: { title_en: "Test board", max_code_length: 500 }, codes, next_cursor: next });

async function mountBoard(overrides = {}) {
  const source = await readFile(new URL("./EventCodeBoard.vue", import.meta.url), "utf8");
  const script = parse(source).descriptor.scriptSetup.content.replace(/^import[\s\S]*?from ["'][^"']+["'];\n/gm, "");
  const mounted = [], cleanup = [], timers = [];
  const context = {
    computed, ref, language: ref("en"), t: (key) => key,
    onMounted: (fn) => mounted.push(fn), onBeforeUnmount: (fn) => cleanup.push(fn),
    setInterval: (fn) => { timers.push(fn); return fn; }, clearInterval: () => {},
    fetchEventBoard: async () => page(), submitEventCode: async () => ({ id: 100, code: "NEW" }),
    reportInvalidEventCode: async () => ({ reported: true, deleted: false }),
    getStored: (_key, fallback) => fallback, setStored: () => true,
    navigator: { clipboard: { writeText: async () => {} } }, ...overrides,
  };
  const returned = "active, codes, nextCursor, config, error, notice, busy, editorOpen, draft, copied, hiddenCodeIds, title, instructions, load, openSubmission, save, copy, reportInvalid, displayCode";
  const board = new Function(...Object.keys(context), `${script}\nreturn { ${returned} };`)(...Object.values(context));
  mounted.forEach((fn) => fn());
  await flush();
  return { ...board, timers, dispose: () => cleanup.forEach((fn) => fn()) };
}

test("board starts with 10 newest codes, appends older pages and refresh returns to newest", async () => {
  const requests = [];
  const board = await mountBoard({ fetchEventBoard: async ({ before }) => {
    requests.push(before);
    return before ? page(rows(15, 1)) : page(rows(25, 16), 16);
  } });
  try {
    assert.equal(board.codes.value.length, 10);
    assert.equal(board.codes.value[0].id, 25);
    await board.load(true);
    assert.equal(board.codes.value.length, 25);
    assert.deepEqual(board.codes.value.map((row) => row.id), rows(25, 1).map((row) => row.id));
    assert.equal(board.nextCursor.value, null);
    await board.load();
    assert.equal(board.codes.value.length, 10);
    assert.deepEqual(requests, [undefined, 16, undefined]);
  } finally { board.dispose(); }
});

test("the same browser can submit multiple codes without changing earlier posts", async () => {
  const posted = [];
  const board = await mountBoard({
    fetchEventBoard: async () => page([...posted].reverse()),
    submitEventCode: async (code) => { const row = { id: posted.length + 1, code }; posted.push(row); return row; },
  });
  try {
    board.openSubmission(); board.draft.value = "MY-CODE";
    await board.save();
    assert.equal(board.editorOpen.value, false);
    assert.equal(board.codes.value[0].code, "MY-CODE");
    assert.equal(board.draft.value, "");
    board.openSubmission();
    assert.equal(board.editorOpen.value, true);
    board.draft.value = "ANOTHER-CODE";
    await board.save();
    assert.deepEqual(board.codes.value.map((row) => row.code), ["ANOTHER-CODE", "MY-CODE"]);
    assert.equal(posted.length, 2);
    assert.equal(board.draft.value, "");
  } finally { board.dispose(); }
  const returning = await mountBoard({ fetchEventBoard: async () => page(posted) });
  try {
    returning.openSubmission();
    assert.equal(returning.editorOpen.value, true);
  } finally { returning.dispose(); }
});

test("duplicate submissions retain the editor; closed boards hide themselves", async () => {
  let code = "duplicate_code";
  const board = await mountBoard({ submitEventCode: async () => { throw Object.assign(new Error(), { code }); } });
  try {
    board.openSubmission(); board.draft.value = "DUPLICATE";
    await board.save();
    assert.match(board.error.value, /already on the board/);
    assert.equal(board.editorOpen.value, true);
    code = "board_closed";
    await board.save();
    assert.equal(board.active.value, false);
    assert.equal(board.editorOpen.value, false);
  } finally { board.dispose(); }
});

test("invalid share patterns and multi-message pastes explain rejection without closing the editor", async () => {
  let code = "invalid_share_message";
  const board = await mountBoard({ submitEventCode: async () => { throw Object.assign(new Error(), { code, status: 422 }); } });
  try {
    board.openSubmission(); board.draft.value = "incomplete share message";
    await board.save();
    assert.match(board.error.value, /complete birthday standee share message/);
    assert.equal(board.editorOpen.value, true);
    assert.equal(board.draft.value, "incomplete share message");
    code = "multiple_share_messages";
    await board.save();
    assert.equal(board.error.value, "Paste one share message at a time.");
    assert.equal(board.editorOpen.value, true);
  } finally { board.dispose(); }
});

test("copy failure offers manual selection", async () => {
  const board = await mountBoard({
    navigator: { clipboard: { writeText: async () => { throw new Error("denied"); } } },
  });
  try {
    const row = { id: 1, code: "COPY-ME" };
    await board.copy(row);
    assert.match(board.error.value, /Select and copy/);
    assert.equal(board.copied.value, null);
  } finally { board.dispose(); }
});

test("standee rows show only the bracketed code while copying the complete game message", async () => {
  const message = "王者荣耀【墨子顶级立牌2V470T】价值910分，复制口令获取立牌，来小王生日会免费换周年庆限定传说皮肤！";
  const row = { id: 1, code: message };
  let copied = "";
  const board = await mountBoard({
    fetchEventBoard: async () => ({ ...page([row]), config: { code_format: "birthday_standee" } }),
    navigator: { clipboard: { writeText: async (text) => { copied = text; } } },
  });
  try {
    assert.equal(board.displayCode(row), "【墨子顶级立牌2V470T】");
    assert.equal(board.codes.value[0].code, message);
    await board.copy(row);
    assert.equal(copied, message);
    assert.equal(board.copied.value, row.id);
  } finally { board.dispose(); }
});

test("generic and older unformatted entries keep their complete code visible", async () => {
  const board = await mountBoard();
  try {
    assert.equal(board.displayCode({ code: "GENERIC-123" }), "GENERIC-123");
    assert.equal(board.displayCode({ code: "prefix【custom】suffix" }), "prefix【custom】suffix");
    board.config.value.code_format = "birthday_standee";
    assert.equal(board.displayCode({ code: "OLDER-TEST-CODE" }), "OLDER-TEST-CODE");
  } finally { board.dispose(); }
});

test("invalid reporting hides immediately, persists for this browser, and leaves others unchanged", async () => {
  const storage = new Map();
  let resolveReport, calls = 0;
  const response = new Promise((resolve) => { resolveReport = resolve; });
  const source = rows(3, 1);
  const shared = {
    fetchEventBoard: async () => page(source),
    getStored: (key, fallback) => storage.get(key) ?? fallback,
    setStored: (key, value) => { storage.set(key, value); return true; },
  };
  const board = await mountBoard({ ...shared, reportInvalidEventCode: async () => { calls++; return response; } });
  try {
    const report = board.reportInvalid(source[0]);
    assert.deepEqual(board.codes.value.map((row) => row.id), [2, 1]);
    assert.equal(board.busy.value, true);
    await board.reportInvalid(source[0]);
    assert.equal(calls, 1);
    resolveReport({ reported: true, deleted: false });
    await report;
    assert.equal(board.notice.value, "Code hidden and reported as invalid.");
    await board.load();
    assert.deepEqual(board.codes.value.map((row) => row.id), [2, 1]);
    const reloaded = await mountBoard(shared);
    try { assert.deepEqual(reloaded.codes.value.map((row) => row.id), [2, 1]); }
    finally { reloaded.dispose(); }
    const otherBrowser = await mountBoard({ fetchEventBoard: async () => page(source) });
    try { assert.deepEqual(otherBrowser.codes.value.map((row) => row.id), [3, 2, 1]); }
    finally { otherBrowser.dispose(); }
  } finally { resolveReport({ reported: true }); board.dispose(); }
});

test("a failed invalid report restores the row and allows retry", async () => {
  const storage = new Map();
  let fail = true;
  const source = rows(2, 1);
  const board = await mountBoard({
    fetchEventBoard: async () => page(source),
    getStored: (key, fallback) => storage.get(key) ?? fallback,
    setStored: (key, value) => { storage.set(key, value); return true; },
    reportInvalidEventCode: async () => { if (fail) throw new Error("offline"); return { reported: true }; },
  });
  try {
    await board.reportInvalid(source[0]);
    assert.deepEqual(board.codes.value.map((row) => row.id), [2, 1]);
    assert.equal(board.hiddenCodeIds.value.has(2), false);
    assert.ok(board.error.value);
    fail = false;
    await board.reportInvalid(source[0]);
    assert.deepEqual(board.codes.value.map((row) => row.id), [1]);
    assert.equal(board.hiddenCodeIds.value.has(2), true);
  } finally { board.dispose(); }
});

test("stored hidden IDs are validated and also filtered from expanded pages", async () => {
  const board = await mountBoard({
    getStored: () => '[2,"3",null,-1,0,1.5]',
    fetchEventBoard: async ({ before }) => before ? page(rows(2, 1)) : page(rows(5, 3), 3),
  });
  try {
    assert.deepEqual([...board.hiddenCodeIds.value], [2]);
    await board.load(true);
    assert.deepEqual(board.codes.value.map((row) => row.id), [5, 4, 3, 1]);
  } finally { board.dispose(); }
  const corrupted = await mountBoard({ getStored: () => "invalid json" });
  try { assert.equal(corrupted.hiddenCodeIds.value.size, 0); }
  finally { corrupted.dispose(); }
});

test("a failed refresh after posting clears the sent draft and permits another code", async () => {
  let reads = 0, posts = 0;
  const board = await mountBoard({
    fetchEventBoard: async () => { if (++reads > 1) throw new Error("offline"); return page(); },
    submitEventCode: async () => { posts++; return { id: 1, code: "FIRST" }; },
  });
  try {
    board.openSubmission(); board.draft.value = "FIRST";
    await board.save();
    assert.equal(board.draft.value, "");
    board.openSubmission();
    assert.equal(board.editorOpen.value, true);
    await board.save();
    assert.equal(posts, 1);
    board.draft.value = "SECOND";
    await board.save();
    assert.equal(posts, 2);
  } finally { board.dispose(); }
});

test("expiry hides the board and disabled configuration leaves no panel", async () => {
  const board = await mountBoard();
  try {
    board.config.value.ends_at = "2020-01-01T00:00:00Z";
    board.editorOpen.value = true;
    board.timers[0]();
    assert.equal(board.active.value, false);
    assert.equal(board.editorOpen.value, false);
  } finally { board.dispose(); }
  const disabled = await mountBoard({ fetchEventBoard: async () => ({ active: false }) });
  try { assert.equal(disabled.active.value, false); }
  finally { disabled.dispose(); }
});

test("board requests use signed-browser credentials, uncached reads and no season or event parameter", async () => {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ data: {} }) }; };
  try {
    await fetchEventBoard(); await fetchEventBoard({ before: 16 });
    await submitEventCode("EXAMPLE");
    await reportInvalidEventCode(7);
    assert.deepEqual(calls.map(({ url }) => url), ["/api/event-board", "/api/event-board?before=16", "/api/event-board/codes", "/api/event-board/codes/7/report"]);
    assert.ok(calls.every(({ options }) => options.credentials === "include"));
    assert.equal(calls[0].options.cache, "no-store");
    assert.deepEqual(JSON.parse(calls[2].options.body), { code: "EXAMPLE" });
    assert.equal(calls[2].options.method, "POST");
    assert.equal(calls[3].options.method, "POST");
  } finally { globalThis.fetch = previous; }
});
