export function parseCoachStreamChunk(buffer, chunk) {
  const text = buffer + chunk;
  const events = [];
  let start = 0;
  let newline = text.indexOf("\n");
  while (newline !== -1) {
    const line = text.slice(start, newline).trim();
    if (line) {
      events.push(JSON.parse(line));
    }
    start = newline + 1;
    newline = text.indexOf("\n", start);
  }
  return { events, buffer: text.slice(start) };
}

export function coachErrorCopy(detail, isChinese) {
  const code = detail?.code || "";
  const catalog = {
    empty_input: ["请输入一个问题。", "Please enter a question."],
    input_too_long: [
      "问题过长。请将内容控制在 4000 个字符以内。",
      "The question is too long. Please keep it within 4,000 characters.",
    ],
    coach_classification_error: [
      "BP 教练暂时无法判断这个问题，请重试。这并不表示问题与王者荣耀无关。",
      "The Draft Coach could not classify this question. Please try again. This does not mean the question is off-topic.",
    ],
    conversation_not_found: [
      "无法继续该对话，请开始新的提问。",
      "This conversation is not available. Please start a new question.",
    ],
    conversation_busy: [
      "上一问仍在处理中，请稍后再试。",
      "A previous question is still running. Please wait and try again.",
    ],
  };
  if (catalog[code]) {
    return catalog[code][isChinese ? 0 : 1];
  }
  return detail?.message || (isChinese ? "BP 教练暂时无法回答该问题。" : "The Draft Coach could not answer this question.");
}
