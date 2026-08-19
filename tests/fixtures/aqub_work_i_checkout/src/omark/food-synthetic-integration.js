import crypto from "node:crypto";

import {
  assertDeliveryCorrelationV2,
  validateDeliveryAckV2,
  validateDeliveryCommandV2,
  validateDeliveryReceiptV2,
} from "../gate-adapter/contracts-v2.js";
import { assertOmarkFoodTextOnly } from "./food-consent-v2.js";

export const OMARK_FOOD_SYNTHETIC_MODE = "synthetic_rehearsal";
const MINIMUM_HMAC_SECRET_BYTES = 32;

export class OmarkFoodSyntheticIntegrationError extends Error {
  constructor(code) {
    super(code);
    this.name = "OmarkFoodSyntheticIntegrationError";
    this.code = code;
  }
}

function fail(code) {
  throw new OmarkFoodSyntheticIntegrationError(code);
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

export function canonicalSyntheticCommandBytes(command) {
  validateDeliveryCommandV2(command);
  return Buffer.from(JSON.stringify(canonicalize(command)), "utf8");
}

export function signSyntheticCommand(rawBody, secret) {
  if (!Buffer.isBuffer(rawBody))
    throw new TypeError("rawBody must be a Buffer");
  if (
    typeof secret !== "string" ||
    Buffer.byteLength(secret) < MINIMUM_HMAC_SECRET_BYTES
  ) {
    fail("AQUB_OMARK_FOOD_SYNTHETIC_HMAC_INVALID");
  }
  return `sha256=${crypto.createHmac("sha256", secret).update(rawBody).digest("hex")}`;
}

function assertDependencies({ outbox, gate, consent, checkpoint, clock }) {
  for (const [name, dependency, methods] of [
    [
      "outbox",
      outbox,
      [
        "enqueue",
        "load",
        "recordAck",
        "recordReceipt",
        "recordRejected",
        "recordRecoveryOwner",
        "redact",
      ],
    ],
    ["gate", gate, ["acceptRaw", "dispatch", "reconcile", "cancel", "redact"]],
    ["consent", consent, ["assertCurrent"]],
  ]) {
    if (
      !dependency ||
      methods.some((method) => typeof dependency[method] !== "function")
    ) {
      throw new TypeError(`${name} dependency is invalid`);
    }
  }
  if (typeof checkpoint !== "function" || typeof clock !== "function") {
    throw new TypeError("synthetic integration hooks are invalid");
  }
}

function assertEnabled(enabled, mode) {
  if (enabled !== true || mode !== OMARK_FOOD_SYNTHETIC_MODE) {
    fail("AQUB_OMARK_FOOD_SYNTHETIC_DISABLED");
  }
}

function assertTextCommand(command) {
  validateDeliveryCommandV2(command);
  assertOmarkFoodTextOnly(command.payload);
}

async function applyGateFact(outbox, row, result) {
  const ack = result?.ack;
  validateDeliveryAckV2(ack);
  assertDeliveryCorrelationV2(row.command, ack);
  await outbox.recordAck(row.idempotencyKey, ack);
  if (ack.status === "rejected") {
    return outbox.recordRejected(row.idempotencyKey, ack.reason_code);
  }
  if (result.receipt) {
    validateDeliveryReceiptV2(result.receipt);
    assertDeliveryCorrelationV2(row.command, result.receipt);
    return outbox.recordReceipt(row.idempotencyKey, result.receipt);
  }
  return outbox.recordRecoveryOwner(row.idempotencyKey, "aqub_dispatch");
}

export function createOmarkFoodSyntheticIntegration({
  enabled = false,
  mode = "disabled",
  hmacSecret = "",
  outbox,
  gate,
  consent,
  checkpoint = async () => {},
  clock = () => new Date(),
} = {}) {
  assertDependencies({ outbox, gate, consent, checkpoint, clock });
  const requireEnabled = () => assertEnabled(enabled, mode);

  return Object.freeze({
    async enqueue({ actorRef, command }) {
      requireEnabled();
      assertTextCommand(command);
      await consent.assertCurrent({ actorRef, command, boundary: "enqueue" });
      const rawBody = canonicalSyntheticCommandBytes(command);
      const signature = signSyntheticCommand(rawBody, hmacSecret);
      return outbox.enqueue({
        actorRef,
        command,
        idempotencyKey: command.idempotency_key,
        rawBody,
        signature,
        enqueuedAt: clock().toISOString(),
      });
    },

    async drive(idempotencyKey) {
      requireEnabled();
      let row = await outbox.load(idempotencyKey);
      if (!row) fail("AQUB_OMARK_FOOD_SYNTHETIC_NOT_FOUND");
      if (row.status === "redacted") fail("AQUB_OMARK_FOOD_SYNTHETIC_REDACTED");
      if (["sent", "failed", "rejected"].includes(row.status)) return row;

      try {
        await consent.assertCurrent({
          actorRef: row.actorRef,
          command: row.command,
          boundary: "recovery",
        });
      } catch (error) {
        const cancelled = await gate.cancel(
          row.command.command_id,
          error?.code ?? "AQUB_V2_CONSENT_REVOKED",
        );
        if (!cancelled?.receipt) throw error;
        validateDeliveryReceiptV2(cancelled.receipt);
        assertDeliveryCorrelationV2(row.command, cancelled.receipt);
        return outbox.recordReceipt(idempotencyKey, cancelled.receipt);
      }
      await checkpoint("before_gate_accept", row);
      const accepted = await gate.acceptRaw(row.rawBody, row.signature);
      await checkpoint("after_gate_accept", row, accepted);
      row = await applyGateFact(outbox, row, accepted);
      if (["sent", "failed", "unknown", "rejected"].includes(row.status))
        return row;

      await checkpoint("after_aqub_ack", row);
      await consent.assertCurrent({
        actorRef: row.actorRef,
        command: row.command,
        boundary: "dispatch",
      });
      await outbox.recordRecoveryOwner(idempotencyKey, "gate_restart_recovery");
      const dispatched = await gate.dispatch(row.command.command_id);
      if (!dispatched?.receipt) {
        await outbox.recordRecoveryOwner(
          idempotencyKey,
          "gate_restart_recovery",
        );
        return Object.freeze({
          ...(await outbox.load(idempotencyKey)),
          gateState: dispatched?.state ?? null,
        });
      }
      validateDeliveryReceiptV2(dispatched.receipt);
      assertDeliveryCorrelationV2(row.command, dispatched.receipt);
      await checkpoint("after_gate_terminal", row, dispatched);
      return outbox.recordReceipt(idempotencyKey, dispatched.receipt);
    },

    async reconcile(idempotencyKey) {
      requireEnabled();
      const row = await outbox.load(idempotencyKey);
      if (!row) fail("AQUB_OMARK_FOOD_SYNTHETIC_NOT_FOUND");
      if (row.status === "redacted") fail("AQUB_OMARK_FOOD_SYNTHETIC_REDACTED");
      if (row.status !== "unknown")
        fail("AQUB_OMARK_FOOD_SYNTHETIC_UNKNOWN_REQUIRED");
      await consent.assertCurrent({
        actorRef: row.actorRef,
        command: row.command,
        boundary: "reconciliation",
      });
      const result = await gate.reconcile(row.command.command_id);
      validateDeliveryReceiptV2(result.receipt);
      assertDeliveryCorrelationV2(row.command, result.receipt);
      await checkpoint("after_gate_reconcile", row, result);
      return outbox.recordReceipt(idempotencyKey, result.receipt);
    },

    async redact(idempotencyKey) {
      requireEnabled();
      const row = await outbox.load(idempotencyKey);
      if (!row) fail("AQUB_OMARK_FOOD_SYNTHETIC_NOT_FOUND");
      if (row.status === "redacted") return row;
      if (!["sent", "failed", "unknown", "rejected"].includes(row.status)) {
        fail("AQUB_OMARK_FOOD_SYNTHETIC_REDACTION_NOT_SAFE");
      }
      await gate.redact(row.command.command_id);
      return outbox.redact(idempotencyKey);
    },
  });
}
