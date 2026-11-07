// Default-OFF hosted entry candidate. It authenticates and packs only the fixed
// #815 command/carrier. The operation remains unreachable until its artifact
// lifecycle can be split without inventing an artifact id.
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { admitMaintenanceRequest } from "./maintenance-admission.mjs";

export const MAINTENANCE_ENTRY_OFF = "OFF";
export const ARTIFACT_LIFECYCLE_GAP = "ARTIFACT_ID_EXISTS_ONLY_AFTER_UPLOAD";
const REPOSITORY = "markmalina/gate";
const OWNER_ID = 23397294;
const REPOSITORY_ID = 1293746267;
const SHA = /^[0-9a-f]{40}$/u;
const RUN = /^[1-9][0-9]{0,19}$/u;
const EVENT_KEYS = ["repository", "repositoryId", "ownerId", "actor", "actorId", "sender", "senderId",
  "eventName", "action", "issueNumber", "isPullRequest", "ref", "runAttempt", "runId", "commentBody"];
const SOURCE_KEYS = ["sourceContextSha", "currentMainSha", "checkedOutSha", "workflowSha", "clientSha",
  "executorSha", "evidenceSha256"];
const deny = () => { throw new Error("GATE_MAINTENANCE_ENTRY_REJECTED"); };
const need = (ok) => { if (!ok) deny(); };
function record(value, keys) {
  need(value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype);
  const descriptors = Object.getOwnPropertyDescriptors(value);
  need(Reflect.ownKeys(descriptors).length === keys.length && keys.every((key) =>
    Object.hasOwn(descriptors, key) && Object.hasOwn(descriptors[key], "value") && descriptors[key].enumerable));
  return Object.fromEntries(keys.map((key) => [key, descriptors[key].value]));
}
function carrier(body) {
  need(typeof body === "string" && !body.includes("\r") && body.length <= 12288);
  const match = /^([^\n]+)\n\nmaintenance-grant-base64:([A-Za-z0-9+/]+={0,2})$/u.exec(body);
  need(match !== null && match[2].length <= 10924 && match[2].length % 4 === 0);
  const bytes = Buffer.from(match[2], "base64");
  need(bytes.length > 0 && bytes.length <= 8192 && bytes.toString("base64") === match[2]);
  return { command: match[1], grantBytes: bytes };
}

export function packMaintenanceRequest(input) {
  const { event: eventValue, source: sourceValue, now } = record(input, ["event", "source", "now"]);
  const event = record(eventValue, EVENT_KEYS), source = record(sourceValue, SOURCE_KEYS);
  need(event.repository === REPOSITORY && event.repositoryId === REPOSITORY_ID && event.ownerId === OWNER_ID
    && event.actor === "markmalina" && event.actorId === OWNER_ID
    && event.sender === "markmalina" && event.senderId === OWNER_ID
    && event.eventName === "issue_comment" && event.action === "created"
    && event.issueNumber === 815 && event.isPullRequest === false
    && event.ref === "refs/heads/main" && event.runAttempt === 1 && RUN.test(event.runId));
  need(SOURCE_KEYS.slice(0, 6).every((key) => SHA.test(source[key]))
    && SOURCE_KEYS.slice(0, 6).every((key) => source[key] === source.executorSha)
    && /^[0-9a-f]{64}$/u.test(source.evidenceSha256));
  const fixed = carrier(event.commentBody);
  const context = { repository: event.repository, repositoryId: event.repositoryId, ownerId: event.ownerId,
    actor: event.actor, actorId: event.actorId, sender: event.sender, senderId: event.senderId,
    eventName: event.eventName, action: event.action, issueNumber: event.issueNumber,
    isPullRequest: event.isPullRequest, ref: event.ref, runAttempt: event.runAttempt,
    workflowRef: "markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main",
    ...source, runId: event.runId, commentBody: fixed.command };
  const request = { context, grantBytes: fixed.grantBytes, command: fixed.command, now };
  const admission = admitMaintenanceRequest(request);
  return Object.freeze({ request, admission });
}

export function runMaintenanceEntry(input, { readSecret = () => deny(), invokeOperation = () => deny() } = {}) {
  const { sourceGuard, request } = record(input, ["sourceGuard", "request"]);
  const packed = packMaintenanceRequest(request);
  if (sourceGuard === MAINTENANCE_ENTRY_OFF) return Object.freeze({ state: "OFF", success: false,
    reason: "LITERAL_OFF", operationId: packed.admission.operationId, secretReads: 0, effects: 0 });
  need(sourceGuard === "ARMED");
  // Do not read the protected token or invoke the coordinator: its current API
  // requires artifactId before receipt bytes exist, while GitHub assigns that id
  // only after upload. runId or a placeholder is not artifact provenance.
  void readSecret; void invokeOperation;
  return Object.freeze({ state: "BLOCKED", success: false, reason: ARTIFACT_LIFECYCLE_GAP,
    operationId: packed.admission.operationId, secretReads: 0, effects: 0 });
}

async function cli() {
  need(process.argv.length === 5 && process.argv[2] === "preflight");
  const input = JSON.parse(await readFile(process.argv[3], "utf8"));
  const result = runMaintenanceEntry(input);
  await writeFile(process.argv[4], `${JSON.stringify(result)}\n`, { flag: "wx", mode: 0o600 });
}
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  cli().catch(() => { process.exitCode = 1; });
}
