// Pure consistency predicate. A future trusted caller authenticates artifact
// origin/context and admission evidence. This module grants no operation.
import { createHash } from "node:crypto";
import { admitMaintenanceRequest, MAINTENANCE_TARGET, MAINTENANCE_CONTRACT_SHA256,
  PROPOSED_WORKFLOW_REF } from "./maintenance-admission.mjs";

export const MAINTENANCE_RECEIPT_NAME = "maintenance-hold-receipt.json";
const SHA = /^[0-9a-f]{40}$/u, DIGEST = /^[0-9a-f]{64}$/u;
const RUN = /^[1-9][0-9]{0,19}$/u;
const RESULTS = ["HELD_STOPPED", "NOOP_ALREADY_HELD", "FAILED", "UNKNOWN_STOP"];
const STATUS = ["SUCCESS", "FAILED", "CRASHED", "REMOVED", "SKIPPED", "SLEEPING"];
const PHASES = ["input", "authenticate", "fleet", "inspect", "stop", "wait-stopped", "readback", "complete"];
const CODES = ["E_INPUT", "E_AUTH", "E_FLEET", "E_DEPLOYMENT_SCOPE", "E_STATE_DRIFT", "E_NOT_RUNNING",
  "E_STOP_ACK", "E_STOP_UNCERTAIN", "E_TIMEOUT", "E_NETWORK", "E_HTTP", "E_GRAPHQL", "E_BODY", "E_NOT_STOPPED", "E_BUDGET"];
const COUNTER_KEYS = ["providerRequests", "stop", "restart", "deploy", "config", "stt", "messages",
  "retries", "redirects", "polls", "maxHttpMs", "maxBodyBytes", "pollIntervalMs"];
const TARGET_KEYS = Object.keys(MAINTENANCE_TARGET);
const DEPLOYMENT_KEYS = ["id", "sha", "status", "stopped"];
const FLEET_KEYS = [...TARGET_KEYS, "complete", "repository", "source", "sourceRepoState", "sourceMatches",
  "autoDeploy", "pendingChanges", "numReplicas", "activeDeployments", "latestDeployment", "observedAt", "sha256"];
const RECEIPT_KEYS = ["schemaVersion", "operation", "operationId", "grantDigest", "repository", "repositoryId",
  "ownerId", "target", ...TARGET_KEYS, "expectedSha", "observedSha", "expectedDeploymentId", "observedDeploymentId",
  "sourceRepository", "sourceRepoState", "sourceMatches", "actorId", "workflowRef", "workflowSha", "runId",
  "runAttempt", "executorSha", "clientSha", "evidenceSha256", "contractSha256", "counters", "stopAttempted",
  "ack", "beforeFleets", "afterFleets", "deployment", "stopped", "observedAt", "result", "phase", "diagnostic",
  "recovery", "startedAt", "finishedAt", "originalReceiptSha256"];
const CONTEXT_KEYS = ["repository", "repositoryId", "ownerId", "actorId", "workflowRef", "workflowSha",
  "executorSha", "clientSha", "runId", "runAttempt", "artifactId", "artifactName", "outputDigest", "outputResult"];
const deny = () => { throw new Error("GATE_MAINTENANCE_RECEIPT_REJECTED"); };
const need = (value) => { if (!value) deny(); };
const match = (pattern, value) => typeof value === "string" && pattern.test(value);
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
function record(value, keys) {
  need(value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype);
  const descriptors = Object.getOwnPropertyDescriptors(value);
  need(Reflect.ownKeys(descriptors).length === keys.length && keys.every((key) =>
    Object.hasOwn(descriptors, key) && Object.hasOwn(descriptors[key], "value") && descriptors[key].enumerable));
  return Object.fromEntries(keys.map((key) => [key, descriptors[key].value]));
}
function canonical(value) {
  if (value !== null && typeof value === "object") {
    if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
function instant(value) {
  need(typeof value === "string" && value.length === 24 && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value);
  return Date.parse(value);
}
function bytes(value, limit) {
  need((Buffer.isBuffer(value) || value instanceof Uint8Array) && value.byteLength > 0 && value.byteLength <= limit);
  return Buffer.from(value);
}
function freeze(value) {
  if (value !== null && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function deployment(value, admission) {
  const d = record(value, DEPLOYMENT_KEYS);
  need(d.id === admission.expectedDeploymentId && d.sha === admission.expectedSha
    && STATUS.includes(d.status) && typeof d.stopped === "boolean");
  return d;
}
function source(value, admission) {
  need(value.sourceRepoState === admission.sourceRepoState && value.sourceMatches === admission.sourceMatches);
}
function fleet(value, admission, start, end) {
  if (value === null) return null;
  const f = record(value, FLEET_KEYS), raw = record(f.source, ["repo", "image"]);
  need(TARGET_KEYS.every((key) => f[key] === MAINTENANCE_TARGET[key]) && f.complete === true
    && f.repository === admission.sourceRepository && raw.repo === admission.sourceRepository && raw.image === null
    && f.autoDeploy === false && f.pendingChanges === false
    && Number.isSafeInteger(f.numReplicas) && f.numReplicas >= 1 && f.numReplicas <= 8
    && Array.isArray(f.activeDeployments) && f.activeDeployments.length <= 1);
  source(f, admission);
  const observed = instant(f.observedAt); need(observed >= start && observed <= end);
  f.activeDeployments.forEach((d) => { const item = deployment(d, admission); need(item.stopped === false && item.status === "SUCCESS"); });
  if (f.latestDeployment !== null) {
    const latest = deployment(f.latestDeployment, admission);
    need(latest.stopped || f.activeDeployments.some((d) => canonical(d) === canonical(latest)));
    if (f.activeDeployments.length) need(canonical(latest) === canonical(f.activeDeployments[0]));
  }
  const { sha256, ...preimage } = f;
  need(match(DIGEST, sha256) && hash(canonical(preimage)) === sha256);
  return f;
}
function parse(envelope, admission, now) {
  const e = record(envelope, ["receiptBytes", "digestBytes", "context"]);
  const raw = bytes(e.receiptBytes, 32768), detached = bytes(e.digestBytes, 128);
  const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw);
  const parsed = JSON.parse(text); need(canonical(parsed) === text);
  const r = record(parsed, RECEIPT_KEYS), c = record(e.context, CONTEXT_KEYS), digest = hash(raw);
  need(detached.equals(Buffer.from(`sha256:${digest}  ${MAINTENANCE_RECEIPT_NAME}\n`)));
  need(c.repository === "markmalina/gate" && c.repositoryId === 1293746267 && c.ownerId === 23397294
    && c.actorId === 23397294 && c.workflowRef === PROPOSED_WORKFLOW_REF
    && match(RUN, c.runId) && c.runAttempt === 1 && match(RUN, c.artifactId)
    && c.artifactName === MAINTENANCE_RECEIPT_NAME && c.outputDigest === `sha256:${digest}`
    && c.outputResult === r.result && RESULTS.includes(r.result));
  need(r.schemaVersion === "gate.maintenance-hold/v1" && r.operation === "GATE_MAINTENANCE_HOLD_V1"
    && r.operationId === admission.operationId && r.grantDigest === admission.ownerGrantSha256
    && r.repository === c.repository && r.repositoryId === c.repositoryId && r.ownerId === c.ownerId
    && r.target === "gate" && TARGET_KEYS.every((key) => r[key] === MAINTENANCE_TARGET[key])
    && r.expectedSha === admission.expectedSha && r.expectedDeploymentId === admission.expectedDeploymentId
    && r.sourceRepository === admission.sourceRepository && r.actorId === c.actorId
    && r.workflowRef === c.workflowRef && r.runId === c.runId && r.runAttempt === c.runAttempt
    && r.evidenceSha256 === admission.grant.evidenceSha256 && r.contractSha256 === MAINTENANCE_CONTRACT_SHA256
    && r.recovery === "HOLD_NO_AUTORESTORE" && PHASES.includes(r.phase)
    && typeof r.stopAttempted === "boolean" && typeof r.ack === "boolean"
    && (r.stopped === null || typeof r.stopped === "boolean"));
  source(r, admission);
  for (const key of ["executorSha", "clientSha", "workflowSha"])
    need(match(SHA, c[key]) && c[key] === admission.expectedExecutorSha && r[key] === c[key]);
  const start = instant(r.startedAt), end = instant(r.finishedAt);
  // A bounded in-flight request may finish after authority expires. Accept its
  // truthful non-success evidence retrospectively; this never grants new I/O.
  const nonSuccess = r.result === "FAILED" || r.result === "UNKNOWN_STOP";
  const overrun = nonSuccess ? 15000 : 0;
  need(end >= start && end - start <= 600000 + overrun && start >= instant(admission.grant.notBefore)
    && start < instant(admission.grant.expiresAt)
    && end <= instant(admission.grant.expiresAt) + overrun && end <= instant(now));
  if (r.observedAt !== null) need(instant(r.observedAt) >= start && instant(r.observedAt) <= end);
  const count = record(r.counters, COUNTER_KEYS);
  need(COUNTER_KEYS.every((key) => Number.isSafeInteger(count[key]) && count[key] >= 0)
    && count.providerRequests <= 20 && count.stop <= 1 && count.polls <= 12
    && count.providerRequests >= count.stop + count.polls
    && count.maxHttpMs <= 15000
    // A rejected chunk can itself cross the read limit. Preserve its actual
    // finite safe integer count only as explicit body-limit failure evidence.
    && (count.maxBodyBytes <= 524288 || (nonSuccess && count.providerRequests > 0
      && r.diagnostic?.code === "E_BODY" && r.diagnostic?.category === "TRANSPORT"))
    && count.pollIntervalMs === 5000
    && count.maxHttpMs <= end - start && Math.max(0, count.polls - 1) * 5000 <= end - start
    && (count.providerRequests > 0 || (count.maxHttpMs === 0 && count.maxBodyBytes === 0 && count.polls === 0))
    && ["restart", "deploy", "config", "stt", "messages", "retries", "redirects"].every((key) => count[key] === 0)
    && r.stopAttempted === (count.stop === 1) && (!r.ack || r.stopAttempted));
  need(Array.isArray(r.beforeFleets) && r.beforeFleets.length === 2
    && Array.isArray(r.afterFleets) && r.afterFleets.length === 2);
  const before = r.beforeFleets.map((f) => fleet(f, admission, start, end));
  const after = r.afterFleets.map((f) => fleet(f, admission, start, end));
  let observed = null;
  if (r.deployment !== null) {
    observed = record(r.deployment, [...DEPLOYMENT_KEYS, ...TARGET_KEYS, "observedAt"]);
    deployment(Object.fromEntries(DEPLOYMENT_KEYS.map((key) => [key, observed[key]])), admission);
    need(r.observedSha === r.expectedSha && r.observedDeploymentId === r.expectedDeploymentId
      && TARGET_KEYS.every((key) => observed[key] === MAINTENANCE_TARGET[key]) && observed.stopped === r.stopped
      && observed.observedAt === r.observedAt && instant(observed.observedAt) >= start && instant(observed.observedAt) <= end);
  } else need(r.observedSha === null && r.observedDeploymentId === null && r.stopped === null && r.observedAt === null);
  if (r.diagnostic !== null) {
    const d = record(r.diagnostic, ["code", "category", "httpStatus"]);
    need(CODES.includes(d.code) && ["INPUT", "AUTH", "SCOPE", "TRANSPORT", "PROVIDER", "TIMEOUT", "READBACK", "BUDGET"].includes(d.category)
      && (d.httpStatus === null || (Number.isSafeInteger(d.httpStatus) && d.httpStatus >= 100 && d.httpStatus <= 599)));
  }
  const success = ["HELD_STOPPED", "NOOP_ALREADY_HELD"].includes(r.result);
  if (success) {
    need(r.phase === "complete" && r.diagnostic === null && observed?.stopped === true
      && after.every((f) => f !== null && f.activeDeployments.length === 0 && f.latestDeployment?.stopped === true)
      && instant(after[0].observedAt) <= instant(observed.observedAt)
      && instant(observed.observedAt) <= instant(after[1].observedAt) && count.providerRequests >= 4
      && count.maxHttpMs > 0 && count.maxBodyBytes > 0);
    // Two complete observations must describe the same fixed stopped fleet.
    const core = ({ observedAt, sha256, ...f }) => canonical(f);
    need(core(after[0]) === core(after[1]));
    if (r.result === "HELD_STOPPED") {
      need(r.stopAttempted && r.ack && count.stop === 1 && r.originalReceiptSha256 === null
        && count.providerRequests >= 9 && before.every((f) => f !== null && f.activeDeployments.length === 1
          && f.latestDeployment?.stopped === false) && core(before[0]) === core(before[1])
        && instant(before[0].observedAt) <= instant(before[1].observedAt)
        && instant(before[1].observedAt) <= instant(after[0].observedAt));
    } else need(!r.stopAttempted && !r.ack && count.stop === 0 && match(DIGEST, r.originalReceiptSha256)
      && before.every((f) => f === null));
  } else {
    need(r.phase !== "complete" && r.diagnostic !== null && r.originalReceiptSha256 === null);
    if (r.result === "FAILED") need(!r.stopAttempted && !r.ack && count.stop === 0);
    else need(r.stopAttempted && count.stop === 1);
  }
  return { receipt: r, digest, success };
}

export function verifyMaintenanceReceipt(input) {
  try {
    const { receiptBytes, digestBytes, context, admissionRequest, originalHeld } = record(input,
      ["receiptBytes", "digestBytes", "context", "admissionRequest", "originalHeld"]);
    // Calls the real admission parser, not a user-supplied imitation of its output.
    // Read only enough to choose retrospective validation. parse() below still
    // checks canonical bytes, digest, exact keys and every failure constraint.
    const preview = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes(receiptBytes, 32768)));
    const nonSuccess = preview.result === "FAILED" || preview.result === "UNKNOWN_STOP";
    const admission = admitMaintenanceRequest(nonSuccess
      ? { ...admissionRequest, now: preview.startedAt } : admissionRequest);
    const current = parse({ receiptBytes, digestBytes, context }, admission, admissionRequest.now);
    if (current.receipt.result === "NOOP_ALREADY_HELD") {
      const original = parse(originalHeld, admission, admissionRequest.now);
      need(original.receipt.result === "HELD_STOPPED" && original.success
        && current.receipt.originalReceiptSha256 === original.digest
        && instant(current.receipt.startedAt) >= instant(original.receipt.finishedAt));
    } else need(originalHeld === null);
    return freeze({ schemaValid: true, result: current.receipt.result, success: current.success,
      receiptSha256: current.digest, sourceMatches: admission.sourceMatches,
      sourceRepoState: admission.sourceRepoState, receipt: current.receipt });
  } catch { deny(); }
}
