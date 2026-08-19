import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

export class DurableOmarkFoodSyntheticOutbox {
  constructor(path) {
    if (!path) throw new TypeError("durable outbox path is required");
    this.path = path;
  }

  #read() {
    if (!existsSync(this.path)) return { rows: {} };
    return JSON.parse(readFileSync(this.path, "utf8"));
  }

  #write(document) {
    const temporary = `${this.path}.next`;
    writeFileSync(temporary, `${JSON.stringify(document, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    renameSync(temporary, this.path);
  }

  #toPublic(row) {
    if (!row) return null;
    const value = clone(row);
    value.rawBody = value.rawBodyBase64
      ? Buffer.from(value.rawBodyBase64, "base64")
      : null;
    delete value.rawBodyBase64;
    return value;
  }

  #transition(row, to, action) {
    if (row.status === to) return;
    row.transitions.push({
      ordinal: row.transitions.length + 1,
      from: row.status ?? null,
      to,
      action,
    });
    row.status = to;
  }

  async enqueue(value) {
    const document = this.#read();
    const existing = document.rows[value.idempotencyKey];
    if (existing) return this.#toPublic(existing);
    const row = {
      actorRef: value.actorRef,
      command: clone(value.command),
      idempotencyKey: value.idempotencyKey,
      rawBodyBase64: Buffer.from(value.rawBody).toString("base64"),
      signature: value.signature,
      enqueuedAt: value.enqueuedAt,
      status: null,
      ack: null,
      receipt: null,
      reasonCode: null,
      recoveryOwner: "aqub_enqueue",
      transitions: [],
    };
    this.#transition(row, "queued", "enqueue");
    document.rows[value.idempotencyKey] = row;
    this.#write(document);
    return this.#toPublic(row);
  }

  async load(key) {
    return this.#toPublic(this.#read().rows[key]);
  }

  async recordAck(key, value) {
    const document = this.#read();
    const row = document.rows[key];
    if (!row) throw new Error("AQUB_SYNTHETIC_OUTBOX_NOT_FOUND");
    row.ack = clone(value);
    if (!["sent", "failed", "unknown", "redacted"].includes(row.status)) {
      this.#transition(
        row,
        value.status === "rejected" ? "rejected" : "acknowledged",
        "gate_ack",
      );
    }
    this.#write(document);
    return this.#toPublic(row);
  }

  async recordReceipt(key, value) {
    const document = this.#read();
    const row = document.rows[key];
    if (!row) throw new Error("AQUB_SYNTHETIC_OUTBOX_NOT_FOUND");
    row.receipt = clone(value);
    this.#transition(row, value.status, "gate_receipt");
    row.recoveryOwner =
      value.status === "unknown" ? "aqub_reconciliation" : null;
    this.#write(document);
    return this.#toPublic(row);
  }

  async recordRecoveryOwner(key, recoveryOwner) {
    const document = this.#read();
    const row = document.rows[key];
    if (!row) throw new Error("AQUB_SYNTHETIC_OUTBOX_NOT_FOUND");
    row.recoveryOwner = recoveryOwner;
    this.#write(document);
    return this.#toPublic(row);
  }

  async recordRejected(key, reasonCode) {
    const document = this.#read();
    const row = document.rows[key];
    if (!row) throw new Error("AQUB_SYNTHETIC_OUTBOX_NOT_FOUND");
    row.reasonCode = reasonCode;
    this.#transition(row, "rejected", "gate_rejection");
    row.recoveryOwner = null;
    this.#write(document);
    return this.#toPublic(row);
  }

  async redact(key) {
    const document = this.#read();
    const row = document.rows[key];
    if (!row) throw new Error("AQUB_SYNTHETIC_OUTBOX_NOT_FOUND");
    this.#transition(row, "redacted", "redact");
    row.command = null;
    row.rawBodyBase64 = null;
    row.signature = null;
    row.ack = null;
    row.receipt = null;
    row.recoveryOwner = null;
    this.#write(document);
    return this.#toPublic(row);
  }
}
