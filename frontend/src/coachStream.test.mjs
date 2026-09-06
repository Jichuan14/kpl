import assert from "node:assert/strict";
import test from "node:test";

import { parseCoachStreamChunk } from "./coachStream.js";

test("parses events split across network chunks", () => {
  const first = parseCoachStreamChunk("", '{"type":"progress","message":"Checking');
  assert.equal(first.events.length, 0);
  const second = parseCoachStreamChunk(first.buffer, ' evidence"}\n{"type":"result","data":{"answer":"done"}}\n');
  assert.equal(second.events.length, 2);
  assert.equal(second.events[0].message, "Checking evidence");
  assert.equal(second.events[1].data.answer, "done");
});

test("preserves Chinese text when a UTF-8 character is split", () => {
  const bytes = new TextEncoder().encode('{"type":"progress","message":"正在检查"}\n');
  const split = bytes.length - 3;
  const decoder = new TextDecoder();
  const first = parseCoachStreamChunk("", decoder.decode(bytes.slice(0, split), { stream: true }));
  const second = parseCoachStreamChunk(
    first.buffer,
    decoder.decode(bytes.slice(split), { stream: true }),
  );
  assert.equal(second.events[0].message, "正在检查");
});
