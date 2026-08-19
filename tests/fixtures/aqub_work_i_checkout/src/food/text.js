const AMBIGUOUS_RE =
  /(?:^|[\s,.!?])(не\s+знаю|не\s+помню|не\s+уверен(?:а)?|кажется|примерно|может\s+быть|что-то|потом)(?=$|[\s,.!?])/iu;
const COMMAND_RE = /^\//u;
const FOOD_PREFIX_RE = /^\/food(?:\s+|$)/iu;

export function classifyFoodText(text) {
  if (typeof text !== "string") {
    return Object.freeze({ kind: "unsupported", reason: "text_required" });
  }
  const normalized = text.trim().replace(/\s+/gu, " ");
  if (normalized.length === 0) {
    return Object.freeze({ kind: "ambiguous", reason: "food_prefix_required" });
  }
  if (normalized.length > 4000) {
    return Object.freeze({ kind: "unsupported", reason: "text_too_long" });
  }
  if (COMMAND_RE.test(normalized) && !FOOD_PREFIX_RE.test(normalized)) {
    return Object.freeze({ kind: "unsupported", reason: "command" });
  }
  if (!FOOD_PREFIX_RE.test(normalized)) {
    return Object.freeze({ kind: "ambiguous", reason: "food_prefix_required" });
  }
  const factText = normalized.replace(FOOD_PREFIX_RE, "").trim();
  if (factText.length === 0) {
    return Object.freeze({ kind: "ambiguous", reason: "empty" });
  }
  if (AMBIGUOUS_RE.test(factText)) {
    return Object.freeze({ kind: "ambiguous", reason: "uncertain" });
  }
  return Object.freeze({ kind: "accepted", text: factText });
}

export function foodFactReply(classification) {
  if (classification.kind === "accepted") {
    return "AQUB: факт питания принят. Продолжайте со следующим приёмом пищи.";
  }
  if (classification.kind === "ambiguous") {
    if (classification.reason === "food_prefix_required") {
      return "AQUB: факт не записан. Начните сообщение с /food и напишите, что вы ели или пили.";
    }
    return "AQUB: факт не записан. Уточните, пожалуйста, что именно вы ели или пили.";
  }
  return null;
}
