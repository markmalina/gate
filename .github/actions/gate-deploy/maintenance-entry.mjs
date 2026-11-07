// Default-OFF hosted entry candidate. It authenticates and packs only the fixed
// #815 command/carrier. The operation remains unreachable until its artifact
// lifecycle can be split without inventing an artifact id.
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import { admitMaintenanceRequest } from "./maintenance-admission.mjs";
import { createMaintenanceOperation } from "./maintenance-operation.mjs";
import { createMaintenanceArtifactBinding, unpackMaintenanceArtifact } from "./maintenance-artifact.mjs";
import { createMaintenanceGitHub } from "./maintenance-github.mjs";
export const HOSTED_SOURCE_GUARD = "OFF";
export const MAINTENANCE_WORKFLOW_NAME = "Gate Maintenance Caller Candidate (OFF)";

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

// Runtime values come only from the fixed workflow environment/event file.
// Explicit fake dependencies permit offline ARMED tests; the real CLI remains OFF.
function runtimeEvent(event, env, now) {
  need(env.GITHUB_REPOSITORY === REPOSITORY && env.GITHUB_REPOSITORY_ID === String(REPOSITORY_ID)
    && env.GITHUB_ACTOR === "markmalina" && env.GITHUB_ACTOR_ID === String(OWNER_ID)
    && env.GITHUB_WORKFLOW_REF === "markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main");
  const sha = env.GITHUB_SHA;
  const evidence = JSON.parse(carrier(event.comment.body).grantBytes.toString("utf8")).evidenceSha256;
  return { event: { repository: env.GITHUB_REPOSITORY, repositoryId: Number(env.GITHUB_REPOSITORY_ID), ownerId: event.repository?.owner?.id,
    actor: env.GITHUB_ACTOR, actorId: Number(env.GITHUB_ACTOR_ID), sender: event.sender?.login, senderId: event.sender?.id,
    eventName: env.GITHUB_EVENT_NAME, action: event.action, issueNumber: event.issue?.number, isPullRequest: !!event.issue?.pull_request,
    ref: env.GITHUB_REF, runAttempt: Number(env.GITHUB_RUN_ATTEMPT), runId: env.GITHUB_RUN_ID, commentBody: event.comment?.body },
    source: { sourceContextSha: sha, currentMainSha: sha, checkedOutSha: env.CHECKED_OUT_SHA,
      workflowSha: env.GITHUB_WORKFLOW_SHA, executorSha: sha, clientSha: sha, evidenceSha256: evidence }, now };
}
const off = () => Object.freeze({ state: "OFF", success: false, reason: "LITERAL_OFF", secretReads: 0, effects: 0 });
export async function prepareHostedMaintenance({ guard = "OFF", event, env, now } = {}, deps = {}) {
  if (guard === "OFF") return off(); need(guard === "ARMED");
  const { request, admission } = packMaintenanceRequest(runtimeEvent(event, env, now));
  need(event.comment?.user?.id === OWNER_ID && event.comment.user.login === "markmalina"
    && event.comment.created_at === event.comment.updated_at);
  const main = await deps.github.readMain(); need(main.object?.sha === admission.expectedExecutorSha);
  if (deps.preflightOnly) return Object.freeze({ state: "ADMITTED", operationId: admission.operationId });
  // No protected Railway secret is read until actual pack/admission/current-main.
  const token = deps.readSecret(); need(typeof token === "string" && token.length > 0);
  const operation = (deps.createOperation ?? createMaintenanceOperation)({ ...deps.operationDeps, providerToken: token });
  return operation.prepare({ admissionRequest: request });
}
export async function verifyHostedMaintenance({ guard = "OFF", event, env, now } = {}, deps = {}) {
  if (guard === "OFF") return off(); need(guard === "ARMED");
  need(env.GITHUB_REPOSITORY === REPOSITORY && env.GITHUB_REPOSITORY_ID === String(REPOSITORY_ID)
    && env.GITHUB_ACTOR === "markmalina" && env.GITHUB_ACTOR_ID === String(OWNER_ID)
    && env.GITHUB_WORKFLOW_REF === "markmalina/gate/.github/workflows/ops-maintenance-verify.yml@refs/heads/main"
    && env.GITHUB_EVENT_NAME === "workflow_run" && env.GITHUB_REF === "refs/heads/main" && env.GITHUB_RUN_ATTEMPT === "1"
    && env.GITHUB_SHA === env.CHECKED_OUT_SHA && env.GITHUB_SHA === env.GITHUB_WORKFLOW_SHA && SHA.test(env.GITHUB_SHA)
    && event.action === "completed" && event.workflow_run?.name === MAINTENANCE_WORKFLOW_NAME
    && RUN.test(String(event.workflow_run.id)) && event.workflow_run.id.toString() !== env.GITHUB_RUN_ID);
  const github = deps.github, source = env.GITHUB_SHA, runId = String(event.workflow_run.id);
  need((await github.readMain()).object?.sha === source);
  const run = await github.readRun(REPOSITORY, runId);
  need(String(run.id) === runId && run.name === MAINTENANCE_WORKFLOW_NAME && run.status === "completed"
    && run.run_attempt === 1 && run.head_sha === source && run.head_branch === "main" && run.event === "issue_comment"
    && run.path === ".github/workflows/ops-maintenance.yml" && run.repository?.id === REPOSITORY_ID
    && run.repository.full_name === REPOSITORY && run.head_repository?.id === REPOSITORY_ID
    && run.head_repository.full_name === REPOSITORY && run.actor?.id === OWNER_ID && run.triggering_actor?.id === OWNER_ID);
  need(Number.isFinite(Date.parse(now)) && Number.isFinite(Date.parse(run.updated_at))
    && Date.parse(now) >= Date.parse(run.updated_at) && Date.parse(now) - Date.parse(run.updated_at) <= 120000);
  const carrierId = /^Gate maintenance #([1-9][0-9]{0,19})$/u.exec(run.display_title); need(carrierId !== null);
  const comment = await github.readComment(carrierId[1]);
  need(String(comment.id) === carrierId[1] && comment.issue_url === "https://api.github.com/repos/markmalina/gate/issues/815"
    && comment.user?.id === OWNER_ID && comment.user.login === "markmalina" && comment.created_at === comment.updated_at);
  const list = await github.listArtifacts(runId);
  need(list.total_count === 1 && Array.isArray(list.artifacts) && list.artifacts.length === 1);
  const artifact = list.artifacts[0]; need(artifact.name === "maintenance-hold-receipt.json" && RUN.test(String(artifact.id)));
  const archive = await github.downloadArchive(REPOSITORY, String(artifact.id));
  // Untrusted originals are parsed only as data; never extracted into checkout.
  const payload = unpackMaintenanceArtifact(archive), receipt = JSON.parse(payload.receiptBytes.toString("utf8"));
  need(Date.parse(run.updated_at) >= Date.parse(receipt.finishedAt));
  const originalEnv = { ...env, GITHUB_EVENT_NAME: "issue_comment", GITHUB_RUN_ID: runId,
    GITHUB_ACTOR: "markmalina", GITHUB_ACTOR_ID: String(OWNER_ID),
    GITHUB_WORKFLOW_REF: "markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main" };
  const originalEvent = { repository: { owner: { id: OWNER_ID } }, sender: { login: "markmalina", id: OWNER_ID },
    issue: { number: 815 }, action: "created", comment };
  const packed = packMaintenanceRequest(runtimeEvent(originalEvent, originalEnv, receipt.startedAt));
  const context = { repository: REPOSITORY, repositoryId: REPOSITORY_ID, ownerId: OWNER_ID, actorId: OWNER_ID,
    workflowRef: "markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main", workflowSha: source, executorSha: source,
    clientSha: source, runId, runAttempt: 1, artifactName: "maintenance-hold-receipt.json", outputDigest: `sha256:${createHash("sha256").update(payload.receiptBytes).digest("hex")}`, outputResult: receipt.result };
  // Binder independently rereads run/workflow/artifact and downloads originals;
  // list/event/carrier observations alone never authenticate provenance.
  return createMaintenanceArtifactBinding(github).bind({ payload: { ...payload, context },
    admissionRequest: { ...packed.request, now }, originalHeld: null,
    upload: { artifactId: String(artifact.id), archiveDigest: artifact.digest } });
}
async function cli() {
  if (process.argv.length === 5 && process.argv[2] === "preflight") {
    const input = JSON.parse(await readFile(process.argv[3], "utf8"));
    await writeFile(process.argv[4], `${JSON.stringify(runMaintenanceEntry(input))}\n`, { flag: "wx", mode: 0o600 }); return;
  }
  const mode = process.argv[2], output = process.argv[3];
  need(["preflight", "prepare", "verify"].includes(mode) && typeof output === "string" && process.argv.length === 4);
  // Literal source OFF precedes event/credential/environment access.
  if (HOSTED_SOURCE_GUARD === "OFF") { await writeFile(output, `${JSON.stringify(off())}\n`, { flag: "wx", mode: 0o600 }); return; }
  const event = JSON.parse(await readFile(process.env.GITHUB_EVENT_PATH, "utf8")), env = process.env;
  const githubToken = env.GH_TOKEN, github = createMaintenanceGitHub({ token: githubToken, fetchFn: fetch });
  const input = { guard: HOSTED_SOURCE_GUARD, event, env, now: new Date().toISOString() };
  const result = mode === "verify" ? await verifyHostedMaintenance(input, { github }) : await prepareHostedMaintenance(input,
    { github, preflightOnly: mode === "preflight", readSecret: () => env.RAILWAY_TOKEN,
      operationDeps: { githubToken, githubFetch: fetch, providerFetch: fetch, clock: () => new Date().toISOString(), sleep: ms => new Promise(resolve => setTimeout(resolve, ms)) } });
  if (mode === "prepare" && result.payload) {
    await mkdir(output, { recursive: false, mode: 0o700 });
    await writeFile(`${output}/maintenance-hold-receipt.json`, result.payload.receiptBytes, { flag: "wx", mode: 0o600 });
    await writeFile(`${output}/maintenance-hold-receipt.json.sha256`, result.payload.digestBytes, { flag: "wx", mode: 0o600 });
  } else await writeFile(output, `${JSON.stringify(result)}\n`, { flag: "wx", mode: 0o600 });
  const ready = mode === "prepare" && !!result.payload;
  if (env.GITHUB_OUTPUT && mode !== "verify") {
    const { appendFile } = await import("node:fs/promises");
    await appendFile(env.GITHUB_OUTPUT, `admitted=${result.state === "ADMITTED"}\npayload_ready=${ready}\neffect_result=${["HELD_STOPPED", "FAILED", "UNKNOWN_STOP"].includes(result.effectState) ? result.effectState : "DENIED"}\n`);
  }
  if (mode === "verify") console.log(JSON.stringify(result));
  if (mode === "verify" && env.GITHUB_STEP_SUMMARY) {
    const { appendFile } = await import("node:fs/promises");
    await appendFile(env.GITHUB_STEP_SUMMARY, `${JSON.stringify(result)}\n`);
  }
  if (mode === "verify" && result.provenanceState !== "BOUND") process.exitCode = 1;
}
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  cli().catch(() => { process.exitCode = 1; });
}
