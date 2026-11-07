// Unregistered source predicate. Supplied context/evidence must be authenticated
// by a future trusted caller. Consistency here grants no authority or runtime I/O.
import { createHash } from "node:crypto";

const SHA = /^[0-9a-f]{40}$/u;
const DIGEST = /^[0-9a-f]{64}$/u;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;
const REPOSITORY = "markmalina/gate";
const HISTORICAL = "AlphaCenter/gate";
export const PROPOSED_WORKFLOW_REF = "markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main";
// UTF-8 body of issue815 comment6699598571, C1; no added final newline.
export const MAINTENANCE_CONTRACT_SHA256 = "08965380aa92e4ab19e5f1a57aa0d436cf365f73d3c2219ba1e80f9ebfb16a67";
export const MAINTENANCE_TARGET = Object.freeze({
  projectId: "972b6ab7-988a-421c-abfa-425451ad714f",
  environmentId: "a148b138-44b3-4349-9d4b-292880503055",
  serviceId: "099cbf8c-1d94-43fe-8714-90725bf93998",
});
export const MAINTENANCE_CAPS = Object.freeze({
  stop: 1, restart: 0, deploy: 0, config: 0, stt: 0, messages: 0,
  providerRequests: 20, totalMs: 600000, httpMs: 15000, bodyBytes: 524288,
  retries: 0, redirects: 0, polls: 12, pollIntervalMs: 5000,
});
const FIXED_GRANT = Object.freeze({
  schemaVersion: "gate.maintenance-owner-request/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
  repositoryId: 1293746267, ownerId: 23397294, repository: REPOSITORY, target: "gate",
  ...MAINTENANCE_TARGET, fullGateDowntime: true, contractSha256: MAINTENANCE_CONTRACT_SHA256,
});
const GRANT_KEYS = [...Object.keys(FIXED_GRANT), "operationId", "expectedDeploymentId", "expectedSha",
  "sourceRepository", "expectedExecutorSha", "evidenceSha256", "notBefore", "expiresAt", "caps"];
const FIXED_CONTEXT = Object.freeze({
  repository: REPOSITORY, repositoryId: 1293746267, ownerId: 23397294,
  actor: "markmalina", actorId: 23397294, sender: "markmalina", senderId: 23397294,
  eventName: "issue_comment", action: "created", issueNumber: 815, isPullRequest: false,
  ref: "refs/heads/main", runAttempt: 1, workflowRef: PROPOSED_WORKFLOW_REF,
});
const SOURCE_KEYS = ["sourceContextSha", "currentMainSha", "checkedOutSha", "workflowSha", "clientSha", "executorSha"];
const CONTEXT_KEYS = [...Object.keys(FIXED_CONTEXT), ...SOURCE_KEYS, "runId", "commentBody", "evidenceSha256"];
const reject = () => { throw new Error("GATE_MAINTENANCE_ADMISSION_REJECTED"); };
const requireValue = (condition) => { if (!condition) reject(); };

// Snapshot plain enumerable data fields without invoking accessor values.
function record(value, keys) {
  requireValue(value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype);
  const descriptors = Object.getOwnPropertyDescriptors(value);
  requireValue(Reflect.ownKeys(descriptors).length === keys.length && keys.every((key) =>
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
function time(value) {
  requireValue(typeof value === "string" && value.length === 24 && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value);
  return Date.parse(value);
}
const matches = (pattern, value) => typeof value === "string" && pattern.test(value);
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");

function parseGrant(bytes) {
  requireValue(Buffer.isBuffer(bytes) || bytes instanceof Uint8Array);
  requireValue(bytes.byteLength > 0 && bytes.byteLength <= 8192);
  // Copy first: later caller mutation cannot change the parsed/hash preimage.
  const snapshot = Buffer.from(bytes);
  const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(snapshot);
  const parsed = JSON.parse(text);
  // Exact sorted-key compact serialization rejects duplicate keys, whitespace,
  // BOM, alternate escapes/number spelling and noncanonical UTF-8/JSON bytes.
  requireValue(canonical(parsed) === text);
  const grant = record(parsed, GRANT_KEYS);
  requireValue(Object.entries(FIXED_GRANT).every(([key, value]) => grant[key] === value));
  const caps = record(grant.caps, Object.keys(MAINTENANCE_CAPS));
  requireValue(Object.entries(MAINTENANCE_CAPS).every(([key, value]) => caps[key] === value));
  requireValue(matches(UUID, grant.operationId) && matches(UUID, grant.expectedDeploymentId)
    && matches(SHA, grant.expectedSha) && matches(SHA, grant.expectedExecutorSha)
    && matches(DIGEST, grant.evidenceSha256)
    && [REPOSITORY, HISTORICAL].includes(grant.sourceRepository));
  grant.caps = Object.freeze(caps);
  return { grant: Object.freeze(grant), digest: hash(snapshot) };
}

// now is a caller-supplied canonical UTC instant; no wall-clock, env, FS or
// provider reads. Throw one fixed safe error for every denial, without payload.
export function admitMaintenanceRequest(input) {
  try {
    const { context, grantBytes, command, now } = record(input, ["context", "grantBytes", "command", "now"]);
    const { grant, digest } = parseGrant(grantBytes);
    const trusted = record(context, CONTEXT_KEYS);
    requireValue(Object.entries(FIXED_CONTEXT).every(([key, value]) => trusted[key] === value));
    requireValue(SOURCE_KEYS.every((key) => matches(SHA, trusted[key]) && trusted[key] === grant.expectedExecutorSha));
    requireValue(matches(/^[1-9][0-9]{0,19}$/u, trusted.runId)
      && trusted.evidenceSha256 === grant.evidenceSha256);
    const current = time(now), start = time(grant.notBefore), end = time(grant.expiresAt);
    requireValue(end > start && end - start <= 900000 && current >= start && current < end);
    const exactCommand = `/ops-gate-maintenance hold expected-source-sha=${grant.expectedSha} expected-deployment-id=${grant.expectedDeploymentId} grant-digest=${digest}`;
    requireValue(typeof command === "string" && command === exactCommand && trusted.commentBody === command);
    const historical = grant.sourceRepository === HISTORICAL;
    const recordedSourceBinding = historical ? Object.freeze({
      schemaVersion: "gate.recorded-source-binding/v1", repositoryId: grant.repositoryId, ownerId: grant.ownerId,
      ...MAINTENANCE_TARGET, repository: grant.sourceRepository,
      expectedDeploymentId: grant.expectedDeploymentId, expectedSha: grant.expectedSha,
      evidenceSha256: grant.evidenceSha256, ownerGrantSha256: digest,
    }) : null;
    return Object.freeze({ state: "SOURCE_PREDICATE_READY", operationId: grant.operationId,
      ownerGrantSha256: digest, expectedExecutorSha: grant.expectedExecutorSha,
      expectedSha: grant.expectedSha, expectedDeploymentId: grant.expectedDeploymentId,
      sourceRepository: grant.sourceRepository, sourceRepoState: historical ? "HISTORICAL_GATE_ALIAS" : "CANONICAL_GATE",
      sourceMatches: !historical, recordedSourceBinding, grant });
  } catch { reject(); }
}
