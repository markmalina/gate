import crypto from "node:crypto";

import { classifyFoodText } from "../food/text.js";

export const FOOD_EXPERIENCE_SCHEMA_VERSION = "aqub.food-experience.v1";

const ACTOR_RE = /^actor:[A-Za-z][A-Za-z0-9._~-]{7,127}$/u;
const REF_RE = /^(?:media|system|operator):[A-Za-z][A-Za-z0-9._~-]{7,127}$/u;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/u;
const IMAGE_MEDIA_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const CONFIDENCE = new Set(["high", "medium", "low"]);
const NUTRIENTS = Object.freeze([
  "calories_kcal",
  "protein_g",
  "fat_g",
  "carbs_g",
]);
const OMARK_FOOD_RETURN_AFTER_MS = 4 * 60 * 60 * 1000;

function fail(code, message) {
  const error = new Error(message);
  error.code = code;
  throw error;
}

function exactKeys(value, allowed, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} is invalid`);
  }
  const extra = Object.keys(value).filter((key) => !allowed.has(key));
  if (extra.length > 0) {
    fail(
      "AQUB_FOOD_EXPERIENCE_EXTRA_FIELDS",
      `${label} has forbidden fields: ${extra.join(",")}`,
    );
  }
}

function requireActor(actorRef) {
  if (!ACTOR_RE.test(actorRef ?? "")) {
    throw new TypeError("Food experience actorRef is invalid");
  }
  return actorRef;
}

function requireUuid(value, label) {
  if (!UUID_RE.test(value ?? "")) throw new TypeError(`${label} is invalid`);
  return value;
}

function requireIso(value, label) {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    throw new TypeError(`${label} is invalid`);
  }
  return new Date(value).toISOString();
}

function requireTimeZone(value) {
  if (typeof value !== "string" || value.length < 1 || value.length > 128) {
    throw new TypeError("owner time zone is invalid");
  }
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format(new Date(0));
  } catch {
    throw new TypeError("owner time zone is invalid");
  }
  return value;
}

export function foodOwnerDay(value, timeZone) {
  const normalizedTimeZone = requireTimeZone(timeZone);
  const occurredAt = requireIso(
    value instanceof Date ? value.toISOString() : value,
    "Food owner day timestamp",
  );
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: normalizedTimeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(occurredAt));
  const values = Object.fromEntries(
    parts.map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
}

function requireText(value, label, maxLength = 2000) {
  if (
    typeof value !== "string" ||
    value.trim().length < 1 ||
    value.length > maxLength
  ) {
    throw new TypeError(`${label} is invalid`);
  }
  return value.trim().replace(/\s+/gu, " ");
}

function normalizeRange(range, label) {
  exactKeys(range, new Set(["min", "max"]), label);
  if (
    !Number.isFinite(range.min) ||
    !Number.isFinite(range.max) ||
    range.min < 0 ||
    range.max < range.min ||
    range.max > 100_000
  ) {
    throw new TypeError(`${label} is invalid`);
  }
  return Object.freeze({ min: range.min, max: range.max });
}

function normalizeNutrition(nutrition) {
  if (nutrition === null || nutrition === undefined) return null;
  exactKeys(nutrition, new Set(NUTRIENTS), "nutrition");
  return Object.freeze(
    Object.fromEntries(
      NUTRIENTS.map((key) => [key, normalizeRange(nutrition[key], key)]),
    ),
  );
}

function normalizeAssumptions(assumptions) {
  if (
    !Array.isArray(assumptions) ||
    assumptions.length > 8 ||
    assumptions.some((item) => typeof item !== "string" || item.length > 300)
  ) {
    throw new TypeError("Food inference assumptions are invalid");
  }
  return Object.freeze(
    [...new Set(assumptions.map((item) => item.trim()))].filter(Boolean),
  );
}

function normalizeProvenance(provenance) {
  exactKeys(
    provenance,
    new Set(["mode", "system_ref", "operator_ref", "operator_minutes"]),
    "provenance",
  );
  if (!new Set(["automated", "assisted"]).has(provenance.mode)) {
    throw new TypeError("Food inference provenance mode is invalid");
  }
  if (!REF_RE.test(provenance.system_ref ?? "")) {
    throw new TypeError("Food inference system_ref is invalid");
  }
  const minutes = provenance.operator_minutes ?? 0;
  if (!Number.isSafeInteger(minutes) || minutes < 0 || minutes > 120) {
    throw new TypeError("Food inference operator_minutes is invalid");
  }
  if (
    provenance.mode === "automated" &&
    (minutes !== 0 || provenance.operator_ref !== undefined)
  ) {
    throw new TypeError("Automated inference cannot claim operator work");
  }
  if (
    provenance.mode === "assisted" &&
    (!REF_RE.test(provenance.operator_ref ?? "") || minutes < 1)
  ) {
    throw new TypeError("Assisted inference requires opaque operator evidence");
  }
  return Object.freeze({
    mode: provenance.mode,
    system_ref: provenance.system_ref,
    ...(provenance.mode === "assisted"
      ? {
          operator_ref: provenance.operator_ref,
          operator_minutes: minutes,
        }
      : { operator_minutes: 0 }),
  });
}

export function normalizeFoodExperienceInput({
  actorRef,
  occurredAt,
  content,
}) {
  requireActor(actorRef);
  const occurred = requireIso(occurredAt, "occurredAt");
  exactKeys(
    content,
    new Set(["kind", "media_type", "text", "media_ref"]),
    "food content",
  );

  if (content.kind === "text" && content.media_type === "text/plain") {
    if (content.media_ref !== undefined) {
      fail(
        "AQUB_FOOD_EXPERIENCE_PRIVATE_PAYLOAD_FORBIDDEN",
        "text content cannot carry a media reference",
      );
    }
    const classification = classifyFoodText(content.text);
    if (classification.kind === "unsupported") {
      fail("AQUB_FOOD_EXPERIENCE_UNSUPPORTED", "Food text is unsupported");
    }
    if (classification.kind === "ambiguous") {
      return Object.freeze({
        kind: "clarification",
        reason: classification.reason,
        question:
          classification.reason === "food_prefix_required"
            ? "Начните сообщение с /food и напишите, что вы ели или пили."
            : "Уточните одним сообщением, что именно вы ели или пили.",
      });
    }
    return Object.freeze({
      kind: "raw_mark",
      payload: Object.freeze({
        schema_version: FOOD_EXPERIENCE_SCHEMA_VERSION,
        actor_ref: actorRef,
        occurred_at: occurred,
        source: Object.freeze({ kind: "text", media_type: "text/plain" }),
        raw: Object.freeze({ text: classification.text }),
      }),
      resolverInput: Object.freeze({
        kind: "text",
        text: classification.text,
      }),
    });
  }

  if (
    content.kind === "photo_reference" &&
    IMAGE_MEDIA_TYPES.has(content.media_type)
  ) {
    if (content.text !== undefined || !REF_RE.test(content.media_ref ?? "")) {
      fail(
        "AQUB_FOOD_EXPERIENCE_PRIVATE_PAYLOAD_FORBIDDEN",
        "Photo input requires one opaque media_ref and no embedded payload",
      );
    }
    const mediaRefSha256 = crypto
      .createHash("sha256")
      .update(content.media_ref, "utf8")
      .digest("hex");
    return Object.freeze({
      kind: "raw_mark",
      payload: Object.freeze({
        schema_version: FOOD_EXPERIENCE_SCHEMA_VERSION,
        actor_ref: actorRef,
        occurred_at: occurred,
        source: Object.freeze({
          kind: "photo_reference",
          media_type: content.media_type,
          media_ref_sha256: mediaRefSha256,
        }),
        raw: null,
      }),
      resolverInput: Object.freeze({
        kind: "photo_reference",
        media_type: content.media_type,
        media_ref: content.media_ref,
      }),
    });
  }

  fail(
    "AQUB_FOOD_EXPERIENCE_UNSUPPORTED",
    "Food experience accepts text/plain or an opaque photo reference",
  );
}

export function createFoodInference(input) {
  exactKeys(
    input,
    new Set([
      "actorRef",
      "sourceEventId",
      "claim",
      "confidence",
      "assumptions",
      "nutrition",
      "clarificationQuestion",
      "provenance",
    ]),
    "food inference",
  );
  const {
    actorRef,
    sourceEventId,
    claim,
    confidence,
    assumptions = [],
    nutrition = null,
    clarificationQuestion = null,
    provenance,
  } = input;
  requireActor(actorRef);
  requireUuid(sourceEventId, "sourceEventId");
  if (!CONFIDENCE.has(confidence)) {
    throw new TypeError("Food inference confidence is invalid");
  }
  const normalizedQuestion =
    clarificationQuestion === null
      ? null
      : requireText(clarificationQuestion, "clarificationQuestion", 300);
  if (confidence === "low" && normalizedQuestion === null) {
    throw new TypeError("Low-confidence inference requires one clarification");
  }
  if (confidence !== "low" && normalizedQuestion !== null) {
    throw new TypeError("Clarification is reserved for low confidence");
  }
  return Object.freeze({
    schema_version: FOOD_EXPERIENCE_SCHEMA_VERSION,
    actor_ref: actorRef,
    source_food_event_id: sourceEventId,
    claim: requireText(claim, "Food inference claim"),
    confidence,
    assumptions: normalizeAssumptions(assumptions),
    nutrition: normalizeNutrition(nutrition),
    clarification:
      normalizedQuestion === null
        ? null
        : Object.freeze({ sequence: 1, question: normalizedQuestion }),
    provenance: normalizeProvenance(provenance),
    human_status: "proposed",
    execution_score: null,
  });
}

export function createFoodOwnerCorrection({ actorRef, sourceEventId, claim }) {
  requireActor(actorRef);
  requireUuid(sourceEventId, "sourceEventId");
  return Object.freeze({
    schema_version: FOOD_EXPERIENCE_SCHEMA_VERSION,
    actor_ref: actorRef,
    source_food_event_id: sourceEventId,
    claim: requireText(claim, "Food correction claim"),
    confidence: "high",
    assumptions: Object.freeze([]),
    nutrition: null,
    clarification: null,
    provenance: Object.freeze({ mode: "owner", operator_minutes: 0 }),
    human_status: "modified",
    execution_score: null,
  });
}

function eventPayload(event) {
  return event.payload_json ?? event.payload ?? {};
}

function eventType(event) {
  return event.event_type ?? event.eventType;
}

function sumNutrition(items) {
  if (items.length === 0) return null;
  return Object.freeze(
    Object.fromEntries(
      NUTRIENTS.map((key) => [
        key,
        Object.freeze({
          min: Number(
            items.reduce((sum, item) => sum + item[key].min, 0).toFixed(2),
          ),
          max: Number(
            items.reduce((sum, item) => sum + item[key].max, 0).toFixed(2),
          ),
        }),
      ]),
    ),
  );
}

export function projectFoodDay({ actorRef, day, events, timeZone, openedAt }) {
  requireActor(actorRef);
  if (!DAY_RE.test(day ?? "") || !Array.isArray(events)) {
    throw new TypeError("Food day projection input is invalid");
  }
  const normalizedTimeZone = requireTimeZone(timeZone);
  const normalizedOpenedAt = requireIso(openedAt, "Food day openedAt");
  const actorEvents = events.filter(
    (event) => eventPayload(event).actor_ref === actorRef,
  );
  const marks = actorEvents.filter(
    (event) =>
      eventType(event) === "food.mark.recorded" &&
      foodOwnerDay(eventPayload(event).occurred_at, normalizedTimeZone) === day,
  );
  const markIds = new Set(marks.map((event) => event.id));
  const inferences = actorEvents.filter(
    (event) =>
      new Set(["food.inference.recorded", "food.inference.corrected"]).has(
        eventType(event),
      ) && markIds.has(eventPayload(event).source_food_event_id),
  );
  const confirmations = new Set(
    actorEvents
      .filter((event) => eventType(event) === "food.inference.confirmed")
      .map((event) => eventPayload(event).effective_inference_event_id),
  );
  const clarifications = actorEvents.filter(
    (event) =>
      eventType(event) === "food.clarification.requested" &&
      markIds.has(eventPayload(event).source_food_event_id),
  );

  const entries = marks.map((mark) => {
    const effective = inferences
      .filter((event) => eventPayload(event).source_food_event_id === mark.id)
      .at(-1);
    const payload = effective ? eventPayload(effective) : null;
    return Object.freeze({
      source_event_id: mark.id,
      source_kind: eventPayload(mark).source?.kind ?? "unknown",
      effective_inference_event_id: effective?.id ?? null,
      claim: payload?.claim ?? null,
      confirmed: effective ? confirmations.has(effective.id) : false,
      nutrition: payload?.nutrition ?? null,
      assumptions: payload?.assumptions ?? [],
      provenance: payload?.provenance ?? null,
    });
  });
  const confirmed = entries.filter((entry) => entry.confirmed);
  const nutrition = confirmed
    .map((entry) => entry.nutrition)
    .filter((value) => value !== null);
  const assisted = confirmed.filter(
    (entry) => entry.provenance?.mode === "assisted",
  );
  const assumptions = Object.freeze([
    ...new Set(confirmed.flatMap((entry) => entry.assumptions)),
  ]);
  const proposedStep =
    confirmed.length === 0
      ? null
      : Object.freeze({
          mode: "K",
          text: "Зафиксировать следующий приём пищи.",
          return_at: new Date(
            Date.parse(normalizedOpenedAt) + OMARK_FOOD_RETURN_AFTER_MS,
          ).toISOString(),
          return_condition: "after_next_food_mark_or_return_at",
          human_status: "proposed",
        });

  return Object.freeze({
    schema_version: FOOD_EXPERIENCE_SCHEMA_VERSION,
    actor_ref: actorRef,
    day,
    time_zone: normalizedTimeZone,
    opened_at: normalizedOpenedAt,
    raw_mark_count: marks.length,
    confirmed_mark_count: confirmed.length,
    pending_mark_count: marks.length - confirmed.length,
    clarification_count: clarifications.length,
    nutrition_range: sumNutrition(nutrition),
    nutrition_coverage:
      confirmed.length === 0 ? 0 : nutrition.length / confirmed.length,
    assumptions,
    assisted_output_count: assisted.length,
    operator_minutes: assisted.reduce(
      (sum, entry) => sum + entry.provenance.operator_minutes,
      0,
    ),
    entries: Object.freeze(entries),
    proposed_step: proposedStep,
    execution_score: null,
  });
}

function rangeText(range, unit) {
  return range ? `${range.min}–${range.max} ${unit}` : "не определено";
}

export function formatFoodInferenceReply(inference) {
  const nutrition = inference.nutrition;
  const provenance =
    inference.provenance.mode === "assisted"
      ? `assisted, ${inference.provenance.operator_minutes} мин.`
      : "automated";
  const base =
    `AQUB: исходная отметка сохранена отдельно. Предположение (${inference.confidence}; ${provenance}): ` +
    `${inference.claim}. Ккал ${rangeText(nutrition?.calories_kcal, "")}; ` +
    `Б ${rangeText(nutrition?.protein_g, "г")}; ` +
    `Ж ${rangeText(nutrition?.fat_g, "г")}; ` +
    `У ${rangeText(nutrition?.carbs_g, "г")}.`;
  return inference.clarification
    ? `${base} ${inference.clarification.question}`
    : `${base} Подтвердите или исправьте предположение.`;
}

export function formatFoodDaySummary(summary) {
  const nutrition = summary.nutrition_range;
  return (
    `AQUB Food · ${summary.day}: отметок ${summary.raw_mark_count}, ` +
    `подтверждено ${summary.confirmed_mark_count}, ожидают подтверждения ${summary.pending_mark_count}. ` +
    `Ккал ${rangeText(nutrition?.calories_kcal, "")}; ` +
    `Б ${rangeText(nutrition?.protein_g, "г")}; ` +
    `Ж ${rangeText(nutrition?.fat_g, "г")}; ` +
    `У ${rangeText(nutrition?.carbs_g, "г")}. ` +
    (summary.proposed_step
      ? `OMARK K: ${summary.proposed_step.text} Возврат после следующей отметки или ${summary.proposed_step.return_at}. Подтвердите или измените шаг.`
      : "OMARK-шаг не предлагается без подтверждённого факта.")
  );
}
