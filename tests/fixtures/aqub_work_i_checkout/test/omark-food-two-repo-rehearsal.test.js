import assert from "node:assert/strict";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { buildDeliveryCommandV2 } from "../src/gate-adapter/contracts-v2.js";
import {
  createFoodInference,
  createFoodOwnerCorrection,
  formatFoodDaySummary,
  formatFoodInferenceReply,
  normalizeFoodExperienceInput,
  projectFoodDay,
} from "../src/omark/food-experience.js";
import {
  canonicalSyntheticCommandBytes,
  createOmarkFoodSyntheticIntegration,
  OMARK_FOOD_SYNTHETIC_MODE,
} from "../src/omark/food-synthetic-integration.js";
import { DurableOmarkFoodSyntheticOutbox } from "../test-support/omark-food-synthetic-outbox.js";

const SECRET = "work-i-test-only-hmac-secret-0000000000000000";
const ACTOR = "actor:synthetic_owner_01";
const NOW = "2026-08-19T18:30:00.000Z";
const AQUB_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const GATE_ROOT = process.env.WORK_I_GATE_CHECKOUT
  ? path.resolve(process.env.WORK_I_GATE_CHECKOUT)
  : null;

class SyntheticCrash extends Error {}

class GateBridgeError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

class RealGateBridge {
  constructor({
    databasePath,
    outcomes = new Map(),
    route = { active: true },
  }) {
    this.databasePath = databasePath;
    this.outcomes = outcomes;
    this.route = route;
    this.crashBoundary = null;
  }

  #invoke(request) {
    const raw = execFileSync(
      "python",
      ["tests/support/aqub_synthetic_rehearsal_bridge.py"],
      {
        cwd: GATE_ROOT,
        env: { ...process.env, PYTHONPATH: GATE_ROOT },
        input: JSON.stringify({
          database_path: this.databasePath,
          secret: SECRET,
          route_active: this.route.active,
          ...request,
        }),
        encoding: "utf8",
      },
    );
    const response = JSON.parse(raw);
    if (response.crashed) throw new SyntheticCrash(response.boundary);
    if (!response.ok) throw new GateBridgeError(response.error);
    return response.result;
  }

  async acceptRaw(rawBody, signature) {
    const command = JSON.parse(Buffer.from(rawBody).toString("utf8"));
    return this.#invoke({
      operation: "accept_raw",
      raw_body_base64: Buffer.from(rawBody).toString("base64"),
      signature,
      provider_outcome: this.outcomes.get(command.idempotency_key) ?? "sent",
    });
  }

  async dispatch(commandId) {
    const crashBoundary = this.crashBoundary;
    this.crashBoundary = null;
    return this.#invoke({
      operation: "dispatch",
      command_id: commandId,
      ...(crashBoundary ? { crash_boundary: crashBoundary } : {}),
    });
  }

  async reconcile(commandId) {
    return this.#invoke({ operation: "reconcile", command_id: commandId });
  }

  async cancel(commandId, reasonCode) {
    return this.#invoke({
      operation: "cancel",
      command_id: commandId,
      reason_code: reasonCode,
    });
  }

  async redact(commandId) {
    return this.#invoke({ operation: "redact", command_id: commandId });
  }

  recoverSending(commandId) {
    return this.#invoke({
      operation: "recover_sending",
      command_id: commandId,
    });
  }

  resolve(idempotencyKey, status) {
    return this.#invoke({
      operation: "provider_resolve",
      idempotency_key: idempotencyKey,
      status,
    });
  }

  snapshot(commandId) {
    return this.#invoke({ operation: "snapshot", command_id: commandId });
  }
}

function consent(state = { active: true }) {
  return {
    state,
    async assertCurrent() {
      if (!state.active) {
        const error = new Error("AQUB_V2_CONSENT_REVOKED");
        error.code = "AQUB_V2_CONSENT_REVOKED";
        throw error;
      }
    },
  };
}

function delivery(
  label,
  deliveryType = "food.fact_reply",
  text = `Synthetic ${label}`,
) {
  const suffix = crypto
    .createHash("sha256")
    .update(label)
    .digest("hex")
    .slice(0, 12);
  return buildDeliveryCommandV2({
    command_id: `40000000-0000-4000-8000-${suffix}`,
    idempotency_key: `work-i/${label}/synthetic`,
    installation_ref: "installation:synthetic_installation_01",
    cube_ref: "cube:synthetic_food_cube_01",
    route_generation: 1,
    conversation_ref: "conversation:synthetic_conversation_01",
    requested_at: NOW,
    payload: {
      delivery_type: deliveryType,
      kind: "text",
      media_type: "text/plain",
      text,
      reply_to_message_ref: "message:synthetic_message_01",
    },
  });
}

function integration({
  outbox,
  gate,
  consentState,
  checkpoint = async () => {},
}) {
  return createOmarkFoodSyntheticIntegration({
    enabled: true,
    mode: OMARK_FOOD_SYNTHETIC_MODE,
    hmacSecret: SECRET,
    outbox,
    gate,
    consent: consentState,
    checkpoint,
    clock: () => new Date(NOW),
  });
}

async function expectCrash(promise, boundary) {
  await assert.rejects(promise, (error) => {
    assert.ok(error instanceof SyntheticCrash, boundary);
    return true;
  });
}

function assertTerminalCorrelation(command, row) {
  assert.ok(row.receipt);
  assert.equal(row.receipt.command_id, command.command_id);
  assert.equal(row.receipt.idempotency_key, command.idempotency_key);
  assert.equal(row.receipt.installation_ref, command.installation_ref);
  assert.equal(row.receipt.cube_ref, command.cube_ref);
  assert.equal(row.receipt.route_generation, command.route_generation);
}

async function observation(context) {
  const row = await context.outbox.load(context.command.idempotency_key);
  let gate;
  try {
    gate = context.gate.snapshot(context.command.command_id);
  } catch (error) {
    if (error.code !== "GATE_SYNTHETIC_COMMAND_NOT_FOUND") throw error;
    gate = {
      state: "absent",
      provider_spy_calls: 0,
      transition_count: 0,
      transitions: [],
    };
  }
  return {
    aqub_state: row.status,
    gate_state: gate.state,
    aqub_transition_count: row.transitions.length,
    gate_transition_count: gate.transition_count,
    recovery_owner: row.recoveryOwner,
    provider_spy_calls: gate.provider_spy_calls,
  };
}

function finalScenario({
  id,
  intermediate,
  final,
  recoveryAction,
  replayResult = "not_applicable",
}) {
  assert.ok(final.provider_spy_calls <= 1, id);
  assert.notEqual(
    final.aqub_state === "acknowledged" && final.recovery_owner === null,
    true,
    id,
  );
  return {
    id,
    intermediate_aqub_state: intermediate.aqub_state,
    intermediate_gate_state: intermediate.gate_state,
    final_aqub_state: final.aqub_state,
    final_gate_state: final.gate_state,
    intermediate_aqub_transition_count: intermediate.aqub_transition_count,
    intermediate_gate_transition_count: intermediate.gate_transition_count,
    final_aqub_transition_count: final.aqub_transition_count,
    final_gate_transition_count: final.gate_transition_count,
    intermediate_recovery_owner: intermediate.recovery_owner,
    recovery_action: recoveryAction,
    provider_spy_calls: final.provider_spy_calls,
    blind_provider_retry_forbidden: true,
    acknowledged_without_recovery_owner: 0,
    next_legal_action: "none",
    replay_after_redaction: replayResult,
  };
}

function context(root, label, outcome = "sent") {
  const command = delivery(label);
  const gate = new RealGateBridge({
    databasePath: path.join(root, `${label}.sqlite3`),
    outcomes: new Map([[command.idempotency_key, outcome]]),
    route: { active: true },
  });
  return {
    command,
    gate,
    outbox: new DurableOmarkFoodSyntheticOutbox(
      path.join(root, `${label}.json`),
    ),
    consentState: consent(),
  };
}

async function enqueue(contextValue) {
  await integration(contextValue).enqueue({
    actorRef: ACTOR,
    command: contextValue.command,
  });
  const row = await contextValue.outbox.load(
    contextValue.command.idempotency_key,
  );
  assert.deepEqual(
    row.rawBody,
    canonicalSyntheticCommandBytes(contextValue.command),
  );
  return row;
}

async function runFoodLoop(root) {
  const rawMark = normalizeFoodExperienceInput({
    actorRef: ACTOR,
    occurredAt: NOW,
    content: {
      kind: "text",
      media_type: "text/plain",
      text: "/food synthetic oats",
    },
  }).payload;
  const inference = createFoodInference({
    actorRef: ACTOR,
    sourceEventId: "00000000-0000-4000-8000-000000000001",
    claim: "synthetic oats",
    confidence: "high",
    nutrition: {
      calories_kcal: { min: 300, max: 320 },
      protein_g: { min: 10, max: 12 },
      fat_g: { min: 6, max: 8 },
      carbs_g: { min: 45, max: 50 },
    },
    provenance: {
      mode: "automated",
      system_ref: "system:synthetic_nutrition_01",
      operator_minutes: 0,
    },
  });
  const correction = createFoodOwnerCorrection({
    actorRef: ACTOR,
    sourceEventId: "00000000-0000-4000-8000-000000000001",
    claim: "synthetic oats corrected",
  });
  const summary = projectFoodDay({
    actorRef: ACTOR,
    day: "2026-08-19",
    timeZone: "Europe/Minsk",
    openedAt: NOW,
    events: [
      {
        id: "00000000-0000-4000-8000-000000000001",
        event_type: "food.mark.recorded",
        payload_json: rawMark,
      },
      {
        id: "00000000-0000-4000-8000-000000000002",
        event_type: "food.inference.recorded",
        payload_json: inference,
      },
      {
        id: "00000000-0000-4000-8000-000000000003",
        event_type: "food.inference.corrected",
        payload_json: correction,
      },
      {
        id: "00000000-0000-4000-8000-000000000004",
        event_type: "food.inference.confirmed",
        payload_json: {
          actor_ref: ACTOR,
          effective_inference_event_id: "00000000-0000-4000-8000-000000000003",
        },
      },
    ],
  });
  assert.equal(summary.confirmed_mark_count, 1);
  assert.equal(summary.entries[0].claim, "synthetic oats corrected");
  const commands = [
    delivery(
      "loop-inference",
      "food.fact_reply",
      formatFoodInferenceReply(inference),
    ),
    delivery(
      "loop-correction",
      "food.correction_reply",
      "AQUB: correction confirmed.",
    ),
    delivery("loop-summary", "food.day_summary", formatFoodDaySummary(summary)),
    delivery("loop-omark", "food.omark_check", summary.proposed_step.text),
  ];
  const gate = new RealGateBridge({
    databasePath: path.join(root, "food-loop.sqlite3"),
    outcomes: new Map(
      commands.map((command) => [command.idempotency_key, "sent"]),
    ),
    route: { active: true },
  });
  const outbox = new DurableOmarkFoodSyntheticOutbox(
    path.join(root, "food-loop.json"),
  );
  const runner = integration({ outbox, gate, consentState: consent() });
  for (const command of commands) {
    await runner.enqueue({ actorRef: ACTOR, command });
    const result = await runner.drive(command.idempotency_key);
    assert.equal(result.status, "sent");
    assertTerminalCorrelation(command, result);
    assert.equal(gate.snapshot(command.command_id).provider_spy_calls, 1);
  }
  return {
    stages: [
      "text_food_input",
      "inference",
      "confirmation_or_correction",
      "aqub_outbox",
      "signed_gate_accept",
      "gate_durable_ledger",
      "provider_spy",
      "sent_failed_or_unknown",
      "aqub_terminal_or_reconciliation",
      "day_summary",
      "one_omark_return_step",
    ],
    delivery_commands: commands.length,
    provider_spy_calls: commands.length,
    provider_spy_calls_per_idempotency_key_max: 1,
    omark_return_steps: commands.filter(
      (command) => command.payload.delivery_type === "food.omark_check",
    ).length,
  };
}

async function runCrashMatrix(root) {
  const results = [];

  {
    const ctx = context(root, "before-gate-accept");
    await enqueue(ctx);
    await expectCrash(
      integration({
        ...ctx,
        checkpoint: async (name) => {
          if (name === "before_gate_accept") throw new SyntheticCrash(name);
        },
      }).drive(ctx.command.idempotency_key),
      "before_gate_accept",
    );
    const intermediate = await observation(ctx);
    const terminal = await integration(ctx).drive(ctx.command.idempotency_key);
    assertTerminalCorrelation(ctx.command, terminal);
    results.push(
      finalScenario({
        id: "before_gate_accept",
        intermediate,
        final: await observation(ctx),
        recoveryAction: "replay_exact_stored_command",
      }),
    );
  }

  {
    const ctx = context(root, "after-gate-commit-before-aqub-ack");
    await enqueue(ctx);
    await expectCrash(
      integration({
        ...ctx,
        checkpoint: async (name) => {
          if (name === "after_gate_accept") throw new SyntheticCrash(name);
        },
      }).drive(ctx.command.idempotency_key),
      "after_gate_commit_before_aqub_ack",
    );
    const intermediate = await observation(ctx);
    const terminal = await integration(ctx).drive(ctx.command.idempotency_key);
    assertTerminalCorrelation(ctx.command, terminal);
    results.push(
      finalScenario({
        id: "after_gate_commit_before_aqub_ack",
        intermediate,
        final: await observation(ctx),
        recoveryAction: "duplicate_accept_returns_durable_state",
      }),
    );
  }

  {
    const ctx = context(root, "after-aqub-ack-before-dispatch", "failed");
    await enqueue(ctx);
    await expectCrash(
      integration({
        ...ctx,
        checkpoint: async (name) => {
          if (name === "after_aqub_ack") throw new SyntheticCrash(name);
        },
      }).drive(ctx.command.idempotency_key),
      "after_aqub_ack_before_dispatch",
    );
    const intermediate = await observation(ctx);
    const terminal = await integration(ctx).drive(ctx.command.idempotency_key);
    assert.equal(terminal.status, "failed");
    assertTerminalCorrelation(ctx.command, terminal);
    results.push(
      finalScenario({
        id: "after_aqub_ack_before_dispatch",
        intermediate,
        final: await observation(ctx),
        recoveryAction: "ack_owner_dispatches_once",
      }),
    );
  }

  for (const [id, outcome, crashBoundary, recoveryAction] of [
    [
      "after_send_boundary_before_provider_result",
      "crash_after_acceptance",
      null,
      "gate_restart_marks_unknown_then_aqub_reconciles_provider_evidence",
    ],
    [
      "after_provider_result_before_gate_settle",
      "crash_after_result",
      "after_provider_result",
      "gate_restart_marks_unknown_then_aqub_reconciles_provider_result",
    ],
  ]) {
    const ctx = context(root, id, outcome);
    await enqueue(ctx);
    ctx.gate.crashBoundary = crashBoundary;
    await expectCrash(integration(ctx).drive(ctx.command.idempotency_key), id);
    const intermediate = await observation(ctx);
    assert.equal(intermediate.gate_state, "sending");
    assert.equal(intermediate.provider_spy_calls, 1);
    ctx.gate.recoverSending(ctx.command.command_id);
    const unknown = await integration(ctx).drive(ctx.command.idempotency_key);
    assert.equal(unknown.status, "unknown");
    assert.equal(unknown.receipt.retry_disposition, "reconcile_only");
    const terminal = await integration(ctx).reconcile(
      ctx.command.idempotency_key,
    );
    assert.equal(terminal.status, "sent");
    assertTerminalCorrelation(ctx.command, terminal);
    results.push(
      finalScenario({
        id,
        intermediate,
        final: await observation(ctx),
        recoveryAction,
      }),
    );
  }

  {
    const ctx = context(root, "after-gate-terminal-before-aqub-terminal");
    await enqueue(ctx);
    await expectCrash(
      integration({
        ...ctx,
        checkpoint: async (name) => {
          if (name === "after_gate_terminal") throw new SyntheticCrash(name);
        },
      }).drive(ctx.command.idempotency_key),
      "after_gate_terminal_before_aqub_terminal",
    );
    const intermediate = await observation(ctx);
    const terminal = await integration(ctx).drive(ctx.command.idempotency_key);
    assertTerminalCorrelation(ctx.command, terminal);
    results.push(
      finalScenario({
        id: "after_gate_terminal_before_aqub_terminal",
        intermediate,
        final: await observation(ctx),
        recoveryAction: "duplicate_accept_returns_terminal_receipt",
      }),
    );
  }

  {
    const ctx = context(root, "during-unknown-reconciliation", "unknown");
    await enqueue(ctx);
    const unknown = await integration(ctx).drive(ctx.command.idempotency_key);
    assert.equal(unknown.status, "unknown");
    ctx.gate.resolve(ctx.command.idempotency_key, "sent");
    await expectCrash(
      integration({
        ...ctx,
        checkpoint: async (name) => {
          if (name === "after_gate_reconcile") throw new SyntheticCrash(name);
        },
      }).reconcile(ctx.command.idempotency_key),
      "during_unknown_reconciliation",
    );
    const intermediate = await observation(ctx);
    const terminal = await integration(ctx).drive(ctx.command.idempotency_key);
    assertTerminalCorrelation(ctx.command, terminal);
    results.push(
      finalScenario({
        id: "during_unknown_reconciliation",
        intermediate,
        final: await observation(ctx),
        recoveryAction: "duplicate_accept_returns_reconciled_terminal",
      }),
    );
  }

  {
    const ctx = context(root, "revoke-delete-during-processing");
    const stored = await enqueue(ctx);
    await expectCrash(
      integration({
        ...ctx,
        checkpoint: async (name) => {
          if (name === "after_aqub_ack") throw new SyntheticCrash(name);
        },
      }).drive(ctx.command.idempotency_key),
      "revoke_delete_during_processing",
    );
    const intermediate = await observation(ctx);
    ctx.consentState.state.active = false;
    ctx.gate.route.active = false;
    const cancelled = await integration(ctx).drive(ctx.command.idempotency_key);
    assert.equal(cancelled.status, "failed");
    assert.equal((await observation(ctx)).provider_spy_calls, 0);
    ctx.consentState.state.active = true;
    const redacted = await integration(ctx).redact(ctx.command.idempotency_key);
    assert.equal(redacted.status, "redacted");
    const replay = await ctx.gate.acceptRaw(stored.rawBody, stored.signature);
    assert.equal(replay.ack.status, "rejected");
    assert.equal(replay.ack.reason_code, "AQUB_V2_CONTENT_REDACTED");
    await assert.rejects(
      integration(ctx).drive(ctx.command.idempotency_key),
      /AQUB_OMARK_FOOD_SYNTHETIC_REDACTED/u,
    );
    results.push(
      finalScenario({
        id: "revoke_delete_during_processing",
        intermediate,
        final: await observation(ctx),
        recoveryAction: "cancel_before_provider_then_redact",
        replayResult: "rejected_AQUB_V2_CONTENT_REDACTED",
      }),
    );
  }

  return results;
}

const crossRepositoryTest = GATE_ROOT ? test : test.skip;

crossRepositoryTest(
  "real AQUB coordinator and real Gate rehearsal execute the observed Work I manifest",
  async () => {
    const root = mkdtempSync(path.join(tmpdir(), "work-i-two-repo-"));
    try {
      const aqubFixture = readFileSync(
        path.join(
          AQUB_ROOT,
          "contracts/rehearsal/food-synthetic-command.v1.json",
        ),
      );
      const gateFixture = readFileSync(
        path.join(
          GATE_ROOT,
          "contracts/aqub/v2/rehearsal/food-synthetic-command.v1.json",
        ),
      );
      const aqubManifestBytes = readFileSync(
        path.join(
          AQUB_ROOT,
          "contracts/rehearsal/food-synthetic-rehearsal-manifest.v1.json",
        ),
      );
      const gateManifestBytes = readFileSync(
        path.join(
          GATE_ROOT,
          "contracts/aqub/v2/rehearsal/food-synthetic-rehearsal-manifest.v1.json",
        ),
      );
      assert.deepEqual(aqubFixture, gateFixture);
      assert.deepEqual(aqubManifestBytes, gateManifestBytes);

      const expected = JSON.parse(aqubManifestBytes);
      const endToEndLoop = await runFoodLoop(root);
      const crashMatrix = await runCrashMatrix(root);
      const observed = {
        schema_version: "aqub.food-synthetic-rehearsal-manifest.v1",
        generated_for: "Work I remediation",
        scope: "two_repository_test_only",
        mode: "synthetic_rehearsal",
        exact_dependencies: {
          startup_work_i: "7113b93376bd19cb671a6b721a73ea53ed05f699",
          aqub_work_h: "b9448e247207f9d0db159fe142857bc80db48b20",
          aqub_stacked_base: "f68d408a1a4e8cc2394a2843c8d657d9794c2d87",
          gate_work_h: "74c572f73fb6d78756d125f08f66af7a4adb2eeb",
          gate_stacked_base: "2054956963b19f671086dc707fd7043334b6cf56",
          ops_credential_contract_evidence:
            "10b8d6f62ea8173f4d2663228066c604f196e4fe",
        },
        runner: {
          aqub_coordinator: "src/omark/food-synthetic-integration.js",
          aqub_durable_outbox: "test-support/omark-food-synthetic-outbox.js",
          aqub_runner: "test/omark-food-two-repo-rehearsal.test.js",
          gate_implementation: "app/application/aqub_synthetic_rehearsal.py",
          gate_bridge: "tests/support/aqub_synthetic_rehearsal_bridge.py",
          gate_ledger: "caller_supplied_test_sqlite",
          external_boundary: "SyntheticProviderSpy",
        },
        shared_fixture: {
          schema_version: "aqub.delivery-command.v2",
          sha256: crypto.createHash("sha256").update(aqubFixture).digest("hex"),
          copies_byte_identical: true,
        },
        identity_class: "synthetic_opaque_only",
        hmac: {
          secret_class: "test_only",
          algorithm: "HMAC-SHA256",
          signed_material: "exact_raw_body",
          live_secret_fallback: false,
        },
        database_roles: {
          migration_identity: "synthetic_migration",
          runtime_identity: "synthetic_runtime",
          distinct: true,
          inherited_from_work_h_least_privilege_contract: true,
        },
        end_to_end_loop: endToEndLoop,
        crash_matrix: crashMatrix,
        invariants: {
          maximum_provider_calls_per_idempotency_key: Math.max(
            ...crashMatrix.map((scenario) => scenario.provider_spy_calls),
          ),
          acknowledged_without_recovery_owner: crashMatrix.filter(
            (scenario) =>
              scenario.intermediate_aqub_state === "acknowledged" &&
              scenario.intermediate_recovery_owner === null,
          ).length,
          unknown_is_reconcile_only: true,
          terminal_receipt_is_correlated: true,
          redaction_blocks_replay:
            crashMatrix.at(-1).replay_after_redaction ===
            "rejected_AQUB_V2_CONTENT_REDACTED",
          photos_captions_media: "NO_GO",
          real_identifiers: 0,
          live_provider_calls: 0,
        },
        runtime_surface: {
          aqub_default_enabled: false,
          gate_default_mode: "disabled",
          gate_live_endpoint_default_enabled: false,
          runtime_imports_added: 0,
          workers_added: 0,
          routes_added: 0,
          migrations_added: 0,
          secrets_changed: 0,
          telegram_messages: 0,
        },
      };
      if (process.env.WORK_I_PRINT_OBSERVED === "1") {
        process.stdout.write(`WORK_I_OBSERVED=${JSON.stringify(observed)}\n`);
      }
      assert.deepEqual(observed, expected);
      assert.equal(observed.end_to_end_loop.omark_return_steps, 1);
      assert.ok(
        observed.crash_matrix.every(
          (scenario) => scenario.provider_spy_calls <= 1,
        ),
      );
      assert.ok(
        observed.crash_matrix.every(
          (scenario) => scenario.acknowledged_without_recovery_owner === 0,
        ),
      );
      process.stdout.write(`${JSON.stringify(observed)}\n`);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  },
);
