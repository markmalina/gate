// Unregistered source coordinator. The future caller authenticates the event,
// current main, grant, credentials and artifact provenance. No activation or I/O
// on construction; every dependency is explicitly injected. No automatic resume.
import { createHash } from "node:crypto";
import { admitMaintenanceRequest, MAINTENANCE_TARGET, MAINTENANCE_CONTRACT_SHA256,
  PROPOSED_WORKFLOW_REF } from "./maintenance-admission.mjs";
import { createMaintenanceLedger } from "./maintenance-ledger.mjs";
import { makeFixedGateMaintenance } from "./gate-deploy.mjs";
import { verifyMaintenanceReceipt, verifyMaintenancePayload, MAINTENANCE_RECEIPT_NAME } from "./maintenance-receipt.mjs";
const canon = (x) => Array.isArray(x) ? `[${x.map(canon).join(",")}]` : x !== null && typeof x === "object"
  ? `{${Object.keys(x).sort().map(k => `${JSON.stringify(k)}:${canon(x[k])}`).join(",")}}` : JSON.stringify(x);
const hash = (x) => createHash("sha256").update(x).digest("hex");
const requireValue = (ok) => { if (!ok) throw Error("E_INPUT"); };
const refusal = (reason) => Object.freeze({ state: "DENIED", success: false, reason, receipt: null, retryable: false });
export function createMaintenanceOperation({ githubToken, providerToken, githubFetch, providerFetch, clock, sleep } = {}) {
  const enabled = [githubFetch, providerFetch, clock, sleep].every(x => typeof x === "function")
    && [githubToken, providerToken].every(x => typeof x === "string" && x.length > 0);
  let used = false;
  async function execute({ admissionRequest, artifactId, originalHeld = null } = {}, payloadOnly = false) {
    if (!enabled || used) return refusal("DISABLED_OR_USED");
    used = true;
    let admission, request, startedAt, start, deadline, lastTime;
    const now = () => {
      const value = clock(), ms = Date.parse(value);
      requireValue(typeof value === "string" && new Date(ms).toISOString() === value && (lastTime === undefined || ms >= lastTime));
      lastTime = ms; return value;
    };
    const guard = () => { const value = now(); requireValue(Date.parse(value) < deadline); return value; };
    try {
      startedAt = now(); start = Date.parse(startedAt);
      admission = admitMaintenanceRequest({ ...admissionRequest, now: startedAt });
      request = { grantBytes: Buffer.from(admissionRequest.grantBytes), command: admissionRequest.command,
        context: { ...admissionRequest.context }, now: startedAt };
      requireValue(payloadOnly ? artifactId === undefined : typeof artifactId === "string" && /^[1-9][0-9]{0,19}$/u.test(artifactId));
      deadline = Math.min(start + 600000, Date.parse(admission.grant.expiresAt));
    } catch { return refusal("ADMISSION"); }
    let githubRequests = 0;
    const ledger = createMaintenanceLedger({ token: githubToken, clock: now, fetchFn: (url, options) => {
      guard(); requireValue(githubRequests < 10); githubRequests++; return githubFetch(url, options);
    } });
    const identity = { operationId: admission.operationId, grantDigest: admission.ownerGrantSha256,
      executorSha: admission.expectedExecutorSha, runtimeSha: admission.expectedSha,
      deploymentId: admission.expectedDeploymentId };
    let snapshot, owned = false, replay = false, originalDigest = null;
    try {
      guard(); const read = await ledger.observe(); requireValue(read.status === "OBSERVED"); snapshot = read.snapshot;
      const doc = JSON.parse(snapshot.content);
      if (snapshot.state === "HELD_STOPPED") {
        requireValue(canon(doc.identity) === canon(identity) && originalHeld !== null);
        const original = verifyMaintenanceReceipt({ ...originalHeld, admissionRequest: { ...request, now: now() }, originalHeld: null });
        requireValue(original.result === "HELD_STOPPED" && original.success);
        originalDigest = original.receiptSha256; replay = true;
        // Snapshot bytes now: later caller mutation cannot change the proof.
        originalHeld = { receiptBytes: Buffer.from(originalHeld.receiptBytes), digestBytes: Buffer.from(originalHeld.digestBytes),
          context: { ...originalHeld.context } };
      } else {
        requireValue(snapshot.state === "EMPTY" && originalHeld === null);
        guard(); const claim = await ledger.transition({ expected: snapshot, identity, to: "CLAIMED" });
        requireValue(claim.status === "COMMITTED"); snapshot = claim.snapshot; owned = true;
      }
    } catch { return refusal("LEDGER_OR_ORIGINAL_PROOF"); }
    const transport = makeFixedGateMaintenance({ token: providerToken, fetchFn: providerFetch, clock: () => Date.parse(now()), sleep: async (ms) => { guard(); await sleep(ms); guard(); },
    absoluteDeadline: deadline, ...(admission.recordedSourceBinding ? { recordedSourceBinding: admission.recordedSourceBinding } : {}) });
    const args = { deploymentId: admission.expectedDeploymentId, expectedSha: admission.expectedSha };
    const exactMatches = (e) => e.fleets.every(f => f.latestDeployment !== null && canon(f.latestDeployment) ===
      canon(Object.fromEntries(["id", "sha", "status", "stopped"].map(k => [k, e.deployment[k]]))));
    let result, failure = null, phase = "inspect";
    try {
      guard(); await transport.inspectFixedGate(args.expectedSha, args.deploymentId);
      if (replay) {
        const e = transport.evidence();
        requireValue(exactMatches(e.inspection));
        requireValue(e.inspection.fleets.every(f => f.activeDeployments.length === 0 && f.latestDeployment?.stopped === true)
          && e.inspection.deployment.stopped === true);
        result = "NOOP_ALREADY_HELD";
      } else {
        const e = transport.evidence();
        requireValue(exactMatches(e.inspection));
        requireValue(e.inspection.fleets.every(f => f.activeDeployments.length === 1 && f.latestDeployment?.stopped === false)
          && e.inspection.deployment.stopped === false && e.inspection.deployment.status === "SUCCESS");
        guard(); const intent = await ledger.transition({ expected: snapshot, identity, to: "STOP_INTENT" });
        requireValue(intent.status === "COMMITTED" && intent.durableIntent === true);
        snapshot = intent.snapshot;
        guard(); requireValue(ledger.consumeStopCapability(intent.stopCapability));
        phase = "stop"; await transport.stopExactDeployment({ ...args, expectedFleetFingerprint: e.inspection.fingerprint });
        phase = "wait-stopped"; guard(); await transport.waitStopped({ ...args, deadline });
        requireValue(exactMatches(transport.evidence().stoppedChain));
        result = "HELD_STOPPED";
      }
      guard();
    } catch (e) {
      result = transport.evidence().stopAttempted ? "UNKNOWN_STOP" : "FAILED";
      failure = e; phase = ["authenticate", "fleet", "inspect", "stop", "wait-stopped"].includes(e?.stage) ? e.stage : phase;
    }
    const produce = () => {
      const e = transport.evidence(), success = ["HELD_STOPPED", "NOOP_ALREADY_HELD"].includes(result);
      const chain = replay ? e.inspection : e.stoppedChain;
      const observed = e.deployment;
      const receipt = { schemaVersion: "gate.maintenance-hold/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
        operationId: admission.operationId, grantDigest: admission.ownerGrantSha256, repository: "markmalina/gate",
        repositoryId: 1293746267, ownerId: 23397294, target: "gate", ...MAINTENANCE_TARGET,
        expectedSha: admission.expectedSha, observedSha: observed?.sha ?? null,
        expectedDeploymentId: admission.expectedDeploymentId, observedDeploymentId: observed?.id ?? null,
        sourceRepository: admission.sourceRepository, sourceRepoState: admission.sourceRepoState, sourceMatches: admission.sourceMatches,
        actorId: 23397294, workflowRef: PROPOSED_WORKFLOW_REF, workflowSha: admission.expectedExecutorSha,
        runId: request.context.runId, runAttempt: 1, executorSha: admission.expectedExecutorSha,
        clientSha: admission.expectedExecutorSha, evidenceSha256: admission.grant.evidenceSha256,
        contractSha256: MAINTENANCE_CONTRACT_SHA256, counters: e.counters, stopAttempted: e.stopAttempted,
        ack: e.acknowledged, beforeFleets: replay ? [null, null] : e.inspection?.fleets ?? [null, null],
        afterFleets: chain?.fleets ?? [null, null], deployment: observed, stopped: observed?.stopped ?? null,
        observedAt: observed?.observedAt ?? null, result, phase: success ? "complete" : phase,
        diagnostic: success ? null : { code: e.counters.maxBodyBytes > 524288 ? "E_BODY" : failure?.code === "E_TIMEOUT" ? "E_TIMEOUT" : e.stopAttempted ? "E_STOP_UNCERTAIN" : "E_INPUT",
          category: e.counters.maxBodyBytes > 524288 ? "TRANSPORT" : failure?.code === "E_TIMEOUT" ? "TIMEOUT" : "READBACK",
          httpStatus: Number.isInteger(failure?.httpStatus) && failure.httpStatus >= 100 && failure.httpStatus <= 599 ? failure.httpStatus : null },
        recovery: "HOLD_NO_AUTORESTORE", startedAt, finishedAt: now(), originalReceiptSha256: result === "NOOP_ALREADY_HELD" ? originalDigest : null };
      const receiptBytes = Buffer.from(canon(receipt)), digest = hash(receiptBytes);
      const envelope = { receiptBytes, digestBytes: Buffer.from(`sha256:${digest}  ${MAINTENANCE_RECEIPT_NAME}\n`),
        context: { repository: "markmalina/gate", repositoryId: 1293746267, ownerId: 23397294, actorId: 23397294,
          workflowRef: PROPOSED_WORKFLOW_REF, workflowSha: admission.expectedExecutorSha, executorSha: admission.expectedExecutorSha,
          clientSha: admission.expectedExecutorSha, runId: request.context.runId, runAttempt: 1, ...(payloadOnly ? {} : { artifactId }),
          artifactName: MAINTENANCE_RECEIPT_NAME, outputDigest: `sha256:${digest}`, outputResult: result } };
      const verified = (payloadOnly ? verifyMaintenancePayload : verifyMaintenanceReceipt)({ ...envelope, admissionRequest: { ...request, now: now() },
        originalHeld: result === "NOOP_ALREADY_HELD" ? originalHeld : null });
      return { envelope, verified };
    };
    let output;
    try { output = produce(); }
    catch {
      // Never turn a receipt construction/validation failure into success.
      result = transport.evidence().stopAttempted ? "UNKNOWN_STOP" : "FAILED"; failure = null; phase = "readback";
      try { output = produce(); } catch { output = null; }
    }
    let terminal = null, persistenceAccepted = false;
    if (owned) {
      try { guard(); terminal = await ledger.transition({ expected: snapshot, identity, to: result }); guard(); persistenceAccepted = terminal.status === "COMMITTED"; }
      catch { /* expired grant: no new persistence request */ }
      if (!persistenceAccepted) {
        result = transport.evidence().stopAttempted ? "UNKNOWN_STOP" : "FAILED"; failure = null; phase = "readback";
        try { output = produce(); } catch { output = null; }
      }
    }
    if (payloadOnly) {
      // Capture private byte copies. Every getter returns a copy, so a caller
      // cannot mutate the frozen receipt between upload and binding.
      const e = output?.envelope, raw = e && Buffer.from(e.receiptBytes), detached = e && Buffer.from(e.digestBytes);
      const payload = e ? Object.freeze({ get receiptBytes() { return Buffer.from(raw); },
        get digestBytes() { return Buffer.from(detached); }, context: Object.freeze({ ...e.context }) }) : null;
      return Object.freeze({ effectState: result, effectSucceeded: output?.verified.effectSucceeded === true && (replay || persistenceAccepted),
        ledgerState: terminal?.snapshot?.state ?? snapshot.state, ledgerCommitted: terminal?.status === "COMMITTED",
        provenanceState: payload ? "PENDING_UPLOAD" : "PAYLOAD_INVALID", payload, githubRequests, retryable: false });
    }
    return Object.freeze({ state: result, success: output?.verified.success === true && (replay || persistenceAccepted),
      receipt: output?.envelope ?? null, githubRequests, lastAcknowledgedLedgerState: terminal?.snapshot?.state ?? snapshot.state,
      ledgerCommitted: terminal?.status === "COMMITTED", retryable: false });
  }
  return Object.freeze({ run: input => execute(input, false), prepare: input => execute(input, true) });
}

