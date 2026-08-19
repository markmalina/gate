const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[0-9a-f]{64}$/;
const IDEMPOTENCY_KEY_RE = /^[A-Za-z][A-Za-z0-9._:~/-]{7,255}$/;
const REFERENCE_SUFFIX_RE = /^[A-Za-z][A-Za-z0-9._~-]{7,127}$/;
const MEDIA_TYPE_RE =
  /^[a-z][a-z0-9!#$&^_.+-]{0,63}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$/;
const CANONICAL_UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:[0-5][0-9]\.\d{3}Z$/;

export const AQUB_V2_DELIVERY_TYPES = Object.freeze([
  "food.clarification",
  "food.fact_reply",
  "food.correction_reply",
  "food.day_summary",
  "food.omark_check",
]);
const AQUB_V2_DELIVERY_TYPE_SET = new Set(AQUB_V2_DELIVERY_TYPES);

const ENVELOPE_KEYS = new Set([
  "schema_version",
  "envelope_ref",
  "installation_ref",
  "cube_ref",
  "route_generation",
  "event_ref",
  "occurred_at",
  "conversation_ref",
  "actor_ref",
  "message_ref",
  "reply_to_message_ref",
  "transport_receipt_ref",
  "payload_sha256",
  "content",
]);
const CONTENT_KEYS = new Set(["kind", "media_type", "text"]);
const COMMAND_KEYS = new Set([
  "schema_version",
  "command_id",
  "idempotency_key",
  "installation_ref",
  "cube_ref",
  "route_generation",
  "conversation_ref",
  "requested_at",
  "payload",
]);
const PAYLOAD_KEYS = new Set([
  "delivery_type",
  "kind",
  "media_type",
  "text",
  "reply_to_message_ref",
]);
const ACK_KEYS = new Set([
  "schema_version",
  "ack_ref",
  "command_id",
  "idempotency_key",
  "installation_ref",
  "cube_ref",
  "route_generation",
  "observed_at",
  "status",
  "reason_code",
]);
const RECEIPT_KEYS = new Set([
  "schema_version",
  "receipt_ref",
  "command_id",
  "idempotency_key",
  "installation_ref",
  "cube_ref",
  "route_generation",
  "observed_at",
  "status",
  "retry_disposition",
  "transport_receipt_ref",
  "delivered_message_ref",
  "failure_code",
  "reconciliation_ref",
]);

export class GateContractV2Error extends Error {
  constructor(code, message) {
    super(message);
    this.name = "GateContractV2Error";
    this.code = code;
  }
}

function fail(code, message) {
  throw new GateContractV2Error(code, message);
}

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("AQUB_V2_BAD_OBJECT", `${label} must be an object`);
  }
}

function assertExactKeys(value, allowed, required, label) {
  assertObject(value, label);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      fail("AQUB_V2_EXTRA_PROPERTY", `${label}.${key} is not allowed`);
    }
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) {
      fail("AQUB_V2_MISSING_PROPERTY", `${label}.${key} is required`);
    }
  }
}

function assertString(value, label, { min = 1, max = 256, pattern } = {}) {
  if (typeof value !== "string") {
    fail("AQUB_V2_BAD_STRING", `${label} is invalid`);
  }
  let length = 0;
  const iterator = value[Symbol.iterator]();
  for (let next = iterator.next(); !next.done; next = iterator.next()) {
    length += 1;
    if (length > max) {
      fail("AQUB_V2_BAD_STRING", `${label} is invalid`);
    }
  }
  if (length < min || (pattern && !pattern.test(value))) {
    fail("AQUB_V2_BAD_STRING", `${label} is invalid`);
  }
}

function assertOpaqueRef(value, kind, label) {
  assertString(value, label, { min: kind.length + 9, max: kind.length + 129 });
  const prefix = `${kind}:`;
  if (
    !value.startsWith(prefix) ||
    !REFERENCE_SUFFIX_RE.test(value.slice(prefix.length))
  ) {
    fail(
      "AQUB_V2_BAD_OPAQUE_REF",
      `${label} must be an opaque ${kind} reference`,
    );
  }
}

function assertNullableOpaqueRef(value, kind, label) {
  if (value !== null) assertOpaqueRef(value, kind, label);
}

function assertUuid(value, label) {
  if (typeof value !== "string" || !UUID_RE.test(value)) {
    fail("AQUB_V2_BAD_UUID", `${label} must be a UUID`);
  }
}

function assertRouteGeneration(value) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2_147_483_647) {
    fail(
      "AQUB_V2_BAD_ROUTE_GENERATION",
      "route_generation must be a positive 32-bit integer",
    );
  }
}

function assertDateTime(value, label) {
  assertString(value, label, {
    min: 24,
    max: 24,
    pattern: CANONICAL_UTC_RE,
  });
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString() !== value) {
    fail("AQUB_V2_BAD_DATETIME", `${label} must be a canonical UTC timestamp`);
  }
}

function assertIdempotencyKey(value) {
  assertString(value, "idempotency_key", {
    min: 8,
    max: 256,
    pattern: IDEMPOTENCY_KEY_RE,
  });
}

function assertRouting(value) {
  assertOpaqueRef(value.installation_ref, "installation", "installation_ref");
  assertOpaqueRef(value.cube_ref, "cube", "cube_ref");
  assertRouteGeneration(value.route_generation);
}

export function assertRouteMatchV2(
  value,
  { installation_ref, cube_ref, route_generation },
) {
  assertRouting(value);
  assertOpaqueRef(
    installation_ref,
    "installation",
    "expected.installation_ref",
  );
  assertOpaqueRef(cube_ref, "cube", "expected.cube_ref");
  assertRouteGeneration(route_generation);
  if (
    value.installation_ref !== installation_ref ||
    value.cube_ref !== cube_ref ||
    value.route_generation !== route_generation
  ) {
    fail(
      "AQUB_V2_ROUTE_MISMATCH",
      "installation, Cube or route generation mismatch",
    );
  }
  return value;
}

function assertTextContent(content, label) {
  assertExactKeys(content, CONTENT_KEYS, CONTENT_KEYS, label);
  if (content.kind !== "text") {
    fail("AQUB_V2_BAD_CONTENT_KIND", `${label}.kind must be text`);
  }
  assertString(content.media_type, `${label}.media_type`, {
    min: 3,
    max: 129,
    pattern: MEDIA_TYPE_RE,
  });
  assertString(content.text, `${label}.text`, { min: 1, max: 32_768 });
}

function assertDeliveryPayload(payload) {
  assertExactKeys(payload, PAYLOAD_KEYS, PAYLOAD_KEYS, "payload");
  assertString(payload.delivery_type, "payload.delivery_type", {
    min: 1,
    max: 64,
  });
  if (!AQUB_V2_DELIVERY_TYPE_SET.has(payload.delivery_type)) {
    fail(
      "AQUB_V2_BAD_DELIVERY_TYPE",
      "payload.delivery_type is not an allowed Food delivery type",
    );
  }
  if (payload.kind !== "text") {
    fail("AQUB_V2_BAD_CONTENT_KIND", "payload.kind must be text");
  }
  assertString(payload.media_type, "payload.media_type", {
    min: 3,
    max: 129,
    pattern: MEDIA_TYPE_RE,
  });
  assertString(payload.text, "payload.text", { min: 1, max: 32_768 });
  assertNullableOpaqueRef(
    payload.reply_to_message_ref,
    "message",
    "payload.reply_to_message_ref",
  );
}

export function validateTransportEnvelopeV2(envelope) {
  assertExactKeys(envelope, ENVELOPE_KEYS, ENVELOPE_KEYS, "envelope");
  if (envelope.schema_version !== "aqub.transport-envelope.v2") {
    fail("AQUB_V2_BAD_SCHEMA", "unsupported transport envelope schema");
  }
  assertOpaqueRef(envelope.envelope_ref, "envelope", "envelope_ref");
  assertRouting(envelope);
  assertOpaqueRef(envelope.event_ref, "event", "event_ref");
  assertDateTime(envelope.occurred_at, "occurred_at");
  assertOpaqueRef(
    envelope.conversation_ref,
    "conversation",
    "conversation_ref",
  );
  assertOpaqueRef(envelope.actor_ref, "actor", "actor_ref");
  assertOpaqueRef(envelope.message_ref, "message", "message_ref");
  assertNullableOpaqueRef(
    envelope.reply_to_message_ref,
    "message",
    "reply_to_message_ref",
  );
  assertOpaqueRef(
    envelope.transport_receipt_ref,
    "receipt",
    "transport_receipt_ref",
  );
  assertString(envelope.payload_sha256, "payload_sha256", {
    min: 64,
    max: 64,
    pattern: SHA256_RE,
  });
  assertTextContent(envelope.content, "content");
  return envelope;
}

export function validateDeliveryCommandV2(command) {
  assertExactKeys(command, COMMAND_KEYS, COMMAND_KEYS, "command");
  if (command.schema_version !== "aqub.delivery-command.v2") {
    fail("AQUB_V2_BAD_SCHEMA", "unsupported delivery command schema");
  }
  assertUuid(command.command_id, "command_id");
  assertIdempotencyKey(command.idempotency_key);
  assertRouting(command);
  assertOpaqueRef(command.conversation_ref, "conversation", "conversation_ref");
  assertDateTime(command.requested_at, "requested_at");
  assertDeliveryPayload(command.payload);
  return command;
}

export function buildDeliveryCommandV2(input) {
  return validateDeliveryCommandV2({
    schema_version: "aqub.delivery-command.v2",
    ...input,
  });
}

export function validateDeliveryAckV2(ack) {
  const required = new Set(ACK_KEYS);
  if (ack?.status !== "rejected") required.delete("reason_code");
  assertExactKeys(ack, ACK_KEYS, required, "ack");
  if (ack.schema_version !== "aqub.delivery-ack.v2") {
    fail("AQUB_V2_BAD_SCHEMA", "unsupported delivery acknowledgement schema");
  }
  assertOpaqueRef(ack.ack_ref, "ack", "ack_ref");
  assertUuid(ack.command_id, "command_id");
  assertIdempotencyKey(ack.idempotency_key);
  assertRouting(ack);
  assertDateTime(ack.observed_at, "observed_at");
  if (!["committed", "duplicate", "rejected"].includes(ack.status)) {
    fail("AQUB_V2_BAD_ACK_STATUS", "ack.status is invalid");
  }
  if (ack.status === "rejected") {
    assertString(ack.reason_code, "reason_code", {
      min: 3,
      max: 96,
      pattern: /^[A-Z][A-Z0-9_]{2,95}$/,
    });
  } else if (Object.hasOwn(ack, "reason_code")) {
    fail(
      "AQUB_V2_UNEXPECTED_REASON",
      "reason_code is only valid for rejected acknowledgements",
    );
  }
  return ack;
}

export function validateDeliveryReceiptV2(receipt) {
  const required = new Set([
    "schema_version",
    "receipt_ref",
    "command_id",
    "idempotency_key",
    "installation_ref",
    "cube_ref",
    "route_generation",
    "observed_at",
    "status",
    "retry_disposition",
    "transport_receipt_ref",
  ]);
  assertExactKeys(receipt, RECEIPT_KEYS, required, "receipt");
  if (receipt.schema_version !== "aqub.delivery-receipt.v2") {
    fail("AQUB_V2_BAD_SCHEMA", "unsupported delivery receipt schema");
  }
  assertOpaqueRef(receipt.receipt_ref, "receipt", "receipt_ref");
  assertUuid(receipt.command_id, "command_id");
  assertIdempotencyKey(receipt.idempotency_key);
  assertRouting(receipt);
  assertDateTime(receipt.observed_at, "observed_at");
  assertOpaqueRef(
    receipt.transport_receipt_ref,
    "receipt",
    "transport_receipt_ref",
  );

  if (!["sent", "failed", "unknown"].includes(receipt.status)) {
    fail("AQUB_V2_BAD_DELIVERY_STATUS", "receipt.status is invalid");
  }

  if (receipt.status === "sent") {
    if (receipt.retry_disposition !== "do_not_retry") {
      fail("AQUB_V2_BAD_RETRY_DISPOSITION", "sent must be do_not_retry");
    }
    assertOpaqueRef(
      receipt.delivered_message_ref,
      "message",
      "delivered_message_ref",
    );
    if (
      Object.hasOwn(receipt, "failure_code") ||
      Object.hasOwn(receipt, "reconciliation_ref")
    ) {
      fail(
        "AQUB_V2_CONFLICTING_DELIVERY_FACT",
        "sent receipt has conflicting fields",
      );
    }
  }

  if (receipt.status === "failed") {
    if (receipt.retry_disposition !== "do_not_retry") {
      fail(
        "AQUB_V2_BAD_RETRY_DISPOSITION",
        "failed must be do_not_retry at the AQUB boundary",
      );
    }
    assertString(receipt.failure_code, "failure_code", {
      min: 3,
      max: 96,
      pattern: /^[A-Z][A-Z0-9_]{2,95}$/,
    });
    if (
      Object.hasOwn(receipt, "delivered_message_ref") ||
      Object.hasOwn(receipt, "reconciliation_ref")
    ) {
      fail(
        "AQUB_V2_CONFLICTING_DELIVERY_FACT",
        "failed receipt has conflicting fields",
      );
    }
  }

  if (receipt.status === "unknown") {
    if (receipt.retry_disposition !== "reconcile_only") {
      fail(
        "AQUB_V2_UNKNOWN_REQUIRES_RECONCILIATION",
        "unknown delivery must be reconcile_only",
      );
    }
    assertOpaqueRef(
      receipt.reconciliation_ref,
      "reconcile",
      "reconciliation_ref",
    );
    if (
      Object.hasOwn(receipt, "delivered_message_ref") ||
      Object.hasOwn(receipt, "failure_code")
    ) {
      fail(
        "AQUB_V2_CONFLICTING_DELIVERY_FACT",
        "unknown receipt has conflicting fields",
      );
    }
  }
  return receipt;
}

export function deliveryDispositionV2(receipt) {
  validateDeliveryReceiptV2(receipt);
  if (receipt.status === "unknown") {
    return Object.freeze({ action: "reconcile" });
  }
  return Object.freeze({ action: "settled" });
}

export function assertDeliveryCorrelationV2(command, fact) {
  validateDeliveryCommandV2(command);
  if (fact?.schema_version === "aqub.delivery-ack.v2") {
    validateDeliveryAckV2(fact);
  } else if (fact?.schema_version === "aqub.delivery-receipt.v2") {
    validateDeliveryReceiptV2(fact);
  } else {
    fail("AQUB_V2_BAD_SCHEMA", "unsupported delivery fact schema");
  }
  if (
    fact.command_id.toLowerCase() !== command.command_id.toLowerCase() ||
    fact.idempotency_key !== command.idempotency_key ||
    fact.installation_ref !== command.installation_ref ||
    fact.cube_ref !== command.cube_ref ||
    fact.route_generation !== command.route_generation
  ) {
    fail(
      "AQUB_V2_DELIVERY_CORRELATION_MISMATCH",
      "delivery fact does not match command",
    );
  }
  return fact;
}
