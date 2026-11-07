import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { verifyMaintenanceReceipt, verifyMaintenancePayload } from "../../.github/actions/gate-deploy/maintenance-receipt.mjs";
import { MAINTENANCE_TARGET as target, MAINTENANCE_CAPS, MAINTENANCE_CONTRACT_SHA256 as contract,
  PROPOSED_WORKFLOW_REF as workflow } from "../../.github/actions/gate-deploy/maintenance-admission.mjs";

const executor = "a".repeat(40), runtime = "b".repeat(40), evidence = "c".repeat(64);
const id = "11111111-1111-4111-8111-111111111111", op = "22222222-2222-4222-8222-222222222222";
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
// Independent JSON replacer implements the public sorted, compact byte contract.
const encode = (value) => Buffer.from(JSON.stringify(value, (key, item) =>
  item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((name) => [name, item[name]])) : item));
const at = (seconds) => new Date(Date.UTC(2030, 0, 1, 0, 0, seconds)).toISOString();
const clone = (value) => structuredClone(value);
function admission(sourceRepository = "markmalina/gate") {
  const grant = { schemaVersion: "gate.maintenance-owner-request/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
    operationId: op, repositoryId: 1293746267, ownerId: 23397294, repository: "markmalina/gate", target: "gate",
    ...target, expectedDeploymentId: id, expectedSha: runtime, sourceRepository, expectedExecutorSha: executor,
    evidenceSha256: evidence, fullGateDowntime: true, contractSha256: contract,
    notBefore: at(0), expiresAt: at(900), caps: { ...MAINTENANCE_CAPS } };
  const grantBytes = encode(grant), command = `/ops-gate-maintenance hold expected-source-sha=${runtime} expected-deployment-id=${id} grant-digest=${sha256(grantBytes)}`;
  const context = { repository: "markmalina/gate", repositoryId: 1293746267, ownerId: 23397294,
    actor: "markmalina", actorId: 23397294, sender: "markmalina", senderId: 23397294,
    eventName: "issue_comment", action: "created", issueNumber: 815, isPullRequest: false,
    ref: "refs/heads/main", runAttempt: 1, workflowRef: workflow, sourceContextSha: executor,
    currentMainSha: executor, checkedOutSha: executor, workflowSha: executor, clientSha: executor,
    executorSha: executor, runId: "33000000000", commentBody: command, evidenceSha256: evidence };
  return { grantBytes, command, context, now: at(700) };
}
function fleet(stopped, seconds, repo) {
  const d = { id, sha: runtime, stopped, status: "SUCCESS" };
  const f = { ...target, complete: true, repository: repo, source: { repo, image: null },
    sourceRepoState: repo === "markmalina/gate" ? "CANONICAL_GATE" : "HISTORICAL_GATE_ALIAS",
    sourceMatches: repo === "markmalina/gate", autoDeploy: false, pendingChanges: false,
    numReplicas: 1, activeDeployments: stopped ? [] : [d], latestDeployment: d, observedAt: at(seconds) };
  return { ...f, sha256: sha256(encode(f)) };
}
function envelope(r, runId = r.runId) {
  const receiptBytes = encode(r), digest = sha256(receiptBytes);
  return { receiptBytes, digestBytes: Buffer.from(`sha256:${digest}  maintenance-hold-receipt.json\n`),
    context: { repository: "markmalina/gate", repositoryId: 1293746267, ownerId: 23397294,
      actorId: 23397294, workflowRef: workflow, workflowSha: executor, executorSha: executor,
      clientSha: executor, runId, runAttempt: 1, artifactId: "40000000000",
      artifactName: "maintenance-hold-receipt.json", outputDigest: `sha256:${digest}`, outputResult: r.result } };
}
function fixture(result = "HELD_STOPPED", repo = "markmalina/gate") {
  const admissionRequest = admission(repo), held = result === "HELD_STOPPED", noop = result === "NOOP_ALREADY_HELD";
  const attempted = held || result === "UNKNOWN_STOP";
  const r = { schemaVersion: "gate.maintenance-hold/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
    operationId: op, grantDigest: sha256(admissionRequest.grantBytes), repository: "markmalina/gate",
    repositoryId: 1293746267, ownerId: 23397294, target: "gate", ...target, expectedSha: runtime,
    observedSha: held || noop ? runtime : null, expectedDeploymentId: id,
    observedDeploymentId: held || noop ? id : null, sourceRepository: repo,
    sourceRepoState: repo === "markmalina/gate" ? "CANONICAL_GATE" : "HISTORICAL_GATE_ALIAS",
    sourceMatches: repo === "markmalina/gate", actorId: 23397294, workflowRef: workflow,
    workflowSha: executor, runId: noop ? "33000000001" : "33000000000", runAttempt: 1,
    executorSha: executor, clientSha: executor, evidenceSha256: evidence, contractSha256: contract,
    counters: { providerRequests: held ? 10 : 4, stop: attempted ? 1 : 0, restart: 0, deploy: 0,
      config: 0, stt: 0, messages: 0, retries: 0, redirects: 0, polls: held ? 1 : 0,
      maxHttpMs: 100, maxBodyBytes: 1024, pollIntervalMs: 5000 }, stopAttempted: attempted,
    ack: held, beforeFleets: held ? [fleet(false, 1, repo), fleet(false, 2, repo)] : [null, null],
    afterFleets: held || noop ? [fleet(true, noop ? 22 : 4, repo), fleet(true, noop ? 24 : 6, repo)] : [null, null],
    deployment: held || noop ? { id, sha: runtime, status: "SUCCESS", stopped: true, ...target, observedAt: at(noop ? 23 : 5) } : null,
    stopped: held || noop ? true : null, observedAt: held || noop ? at(noop ? 23 : 5) : null,
    result, phase: held || noop ? "complete" : "stop",
    diagnostic: held || noop ? null : { code: "E_TIMEOUT", category: "TIMEOUT", httpStatus: null },
    recovery: "HOLD_NO_AUTORESTORE", startedAt: at(noop ? 20 : 0), finishedAt: at(noop ? 30 : 10), originalReceiptSha256: null };
  let originalHeld = null;
  if (noop) {
    const original = fixture("HELD_STOPPED", repo);
    originalHeld = envelope(original.r); r.originalReceiptSha256 = sha256(originalHeld.receiptBytes);
  }
  return { r, admissionRequest, originalHeld };
}

import { deflateRawSync } from "node:zlib";
import { createMaintenanceArtifactBinding, unpackMaintenanceArtifact } from "../../.github/actions/gate-deploy/maintenance-artifact.mjs";
const name = "maintenance-hold-receipt.json";
function crc(bytes) { let n = 0xffffffff; for (const b of bytes) { n ^= b; for (let j = 0; j < 8; j++) n = (n >>> 1) ^ (n & 1 ? 0xedb88320 : 0); } return (n ^ 0xffffffff) >>> 0; }
// Independent ZIP writer supplies actual original bytes (both compression modes).
function zipFiles(files, method = 8, descriptor = false) {
  const local = [], central = []; let offset = 0;
  for (const [key, raw] of Object.entries(files)) {
    const filename = Buffer.from(key), data = method === 8 ? deflateRawSync(raw) : raw, sum = crc(raw);
    const h = Buffer.alloc(30); h.writeUInt32LE(0x04034b50); h.writeUInt16LE(20, 4); h.writeUInt16LE(descriptor ? 8 : 0, 6); h.writeUInt16LE(method, 8);
    if (!descriptor) { h.writeUInt32LE(sum, 14); h.writeUInt32LE(data.length, 18); h.writeUInt32LE(raw.length, 22); }
    h.writeUInt16LE(filename.length, 26);
    const c = Buffer.alloc(46); c.writeUInt32LE(0x02014b50); c.writeUInt16LE(20, 4); c.writeUInt16LE(20, 6); c.writeUInt16LE(descriptor ? 8 : 0, 8); c.writeUInt16LE(method, 10);
    c.writeUInt32LE(sum, 16); c.writeUInt32LE(data.length, 20); c.writeUInt32LE(raw.length, 24); c.writeUInt16LE(filename.length, 28); c.writeUInt32LE(offset, 42);
    const d = descriptor ? Buffer.alloc(16) : Buffer.alloc(0);
    if (descriptor) { d.writeUInt32LE(0x08074b50); d.writeUInt32LE(sum, 4); d.writeUInt32LE(data.length, 8); d.writeUInt32LE(raw.length, 12); }
    local.push(h, filename, data, d); central.push(c, filename); offset += h.length + filename.length + data.length + d.length;
  }
  const directory = Buffer.concat(central), e = Buffer.alloc(22); e.writeUInt32LE(0x06054b50); e.writeUInt16LE(2, 8); e.writeUInt16LE(2, 10); e.writeUInt32LE(directory.length, 12); e.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, e]);
}
function bindingFixture(result = "HELD_STOPPED", method = 8, descriptor = false) {
  const f = fixture(result), payload = envelope(f.r); delete payload.context.artifactId;
  const archive = zipFiles({ [name]: payload.receiptBytes, [`${name}.sha256`]: payload.digestBytes }, method, descriptor);
  const upload = { artifactId: "44000000000", archiveDigest: `sha256:${sha256(archive)}` };
  const run = { id: f.r.runId, run_attempt: 1, head_sha: executor, head_branch: "main", event: "issue_comment", path: ".github/workflows/ops-maintenance.yml", workflow_id: 123, actor: { id: 23397294 }, triggering_actor: { id: 23397294 },
    repository: { id: 1293746267, full_name: "markmalina/gate" }, head_repository: { id: 1293746267, full_name: "markmalina/gate" }, status: "completed", conclusion: ["HELD_STOPPED", "NOOP_ALREADY_HELD"].includes(result) ? "success" : "failure" };
  const workflowMetadata = { id: 123, path: run.path };
  const artifact = { id: upload.artifactId, name, expired: false, digest: upload.archiveDigest, size_in_bytes: archive.length,
    workflow_run: { id: run.id, repository_id: 1293746267, head_repository_id: 1293746267, head_branch: "main", head_sha: executor } };
  const calls = [], deps = {
    readRun: async (...args) => { calls.push(["run", ...args]); return run; },
    readWorkflow: async (...args) => { calls.push(["workflow", ...args]); return workflowMetadata; },
    readArtifact: async (...args) => { calls.push(["artifact", ...args]); return artifact; },
    downloadArchive: async (...args) => { calls.push(["download", ...args]); return archive; }
  };
  return { input: { payload, admissionRequest: f.admissionRequest, originalHeld: f.originalHeld, upload }, run, artifact, workflowMetadata, archive, deps, calls };
}
for (const result of ["HELD_STOPPED", "NOOP_ALREADY_HELD", "FAILED", "UNKNOWN_STOP"]) test(`bind actual originals and metadata for ${result}, separately from effect success`, async () => {
  const f = bindingFixture(result), out = await createMaintenanceArtifactBinding(f.deps).bind(f.input);
  assert.equal(out.provenanceState, "BOUND"); assert.equal(out.effectState, result); assert.equal(out.retryable, false);
  assert.equal(out.effectSucceeded, ["HELD_STOPPED", "NOOP_ALREADY_HELD"].includes(result));
  assert.equal(f.calls.length, 4); assert.ok(f.calls.every(c => c[1] === "markmalina/gate"));
});
for (const [label, change] of Object.entries({
  actor: f => { f.run.actor.id = 1; }, triggeringActor: f => { f.run.triggering_actor.id = 1; },
  artifactID: f => { f.artifact.id = "1"; }, name: f => { f.artifact.name = "other"; },
  archiveDigest: f => { f.artifact.digest = `sha256:${"d".repeat(64)}`; }, run: f => { f.run.id = "1"; },
  attempt: f => { f.run.run_attempt = 2; }, head: f => { f.run.head_sha = "d".repeat(40); },
  workflow: f => { f.run.path = ".github/workflows/evil.yml"; }, workflowID: f => { f.workflowMetadata.id = 999; },
  repo: f => { f.run.repository.id = 1; }, fork: f => { f.run.head_repository.full_name = "other/gate"; },
  result: f => { f.run.conclusion = "failure"; }, incompleteRun: f => { f.run.status = "in_progress"; },
  artifactRun: f => { f.artifact.workflow_run.id = "1"; }, artifactHead: f => { f.artifact.workflow_run.head_sha = "d".repeat(40); },
  artifactRepo: f => { f.artifact.workflow_run.repository_id = 1; }, expired: f => { f.artifact.expired = true; },
  uploadRunID: f => { f.input.upload.artifactId = f.run.id; },
  workflowRef: f => { f.input.payload.context.workflowRef = "other"; },
  workflowSHA: f => { f.input.payload.context.workflowSha = "d".repeat(40); },
  clientSHA: f => { f.input.payload.context.clientSha = "d".repeat(40); }
})) test(`deny wrong ${label} without effect/ledger capability`, async () => {
  const f = bindingFixture(); change(f); const out = await createMaintenanceArtifactBinding(f.deps).bind(f.input);
  assert.equal(out.provenanceState, "NOT_BOUND"); assert.equal(out.retryable, false);
});
test("archive bytes, not metadata/output hashes alone, authenticate payload membership", async () => {
  for (const bad of [Buffer.from("metadata-only"), Buffer.alloc(0)]) {
    const f = bindingFixture(); f.deps.downloadArchive = async () => bad;
    assert.equal((await createMaintenanceArtifactBinding(f.deps).bind(f.input)).provenanceState, "NOT_BOUND");
  }
  const f = bindingFixture();
  const wrong = zipFiles({ [name]: Buffer.from("{}"), [`${name}.sha256`]: f.input.payload.digestBytes });
  f.input.upload.archiveDigest = f.artifact.digest = `sha256:${sha256(wrong)}`; f.artifact.size_in_bytes = wrong.length;
  f.deps.downloadArchive = async () => wrong;
  assert.equal((await createMaintenanceArtifactBinding(f.deps).bind(f.input)).provenanceState, "NOT_BOUND");
});
test("stored/deflate/data-descriptor ZIP originals accepted; truncation/CRC/path/suffix rejected", async () => {
  for (const method of [0, 8]) for (const descriptor of [false, true]) {
    const f = bindingFixture("HELD_STOPPED", method, descriptor);
    assert.equal((await createMaintenanceArtifactBinding(f.deps).bind(f.input)).provenanceState, "BOUND");
    assert.throws(() => unpackMaintenanceArtifact(f.archive.subarray(0, -1)));
  }
  const f = bindingFixture("HELD_STOPPED", 0); f.archive[40] ^= 1;
  assert.throws(() => unpackMaintenanceArtifact(f.archive));
  assert.throws(() => unpackMaintenanceArtifact(zipFiles({ "../receipt": Buffer.from("x"), [`${name}.sha256`]: Buffer.from("y") })));
  assert.throws(() => unpackMaintenanceArtifact(Buffer.concat([bindingFixture().archive, Buffer.from("x")])));
});
test("interruptions before upload/after upload/before binding stay NOT_BOUND and never gain effect retry", async () => {
  const absent = bindingFixture(); delete absent.input.upload;
  assert.equal((await createMaintenanceArtifactBinding(absent.deps).bind(absent.input)).provenanceState, "NOT_BOUND"); assert.equal(absent.calls.length, 0);
  for (const step of ["readRun", "readWorkflow", "readArtifact", "downloadArchive"]) {
    const f = bindingFixture("UNKNOWN_STOP"); f.deps[step] = async () => { throw Error("lost ACK"); };
    const out = await createMaintenanceArtifactBinding(f.deps).bind(f.input);
    assert.equal(out.provenanceState, "NOT_BOUND"); assert.equal(out.retryable, false);
  }
});
test("construction/import and incomplete dependencies inert; user JSON cannot provide reader capabilities", async () => {
  const f = bindingFixture(); const binder = createMaintenanceArtifactBinding(f.deps); assert.equal(f.calls.length, 0);
  assert.equal((await createMaintenanceArtifactBinding({ readRun: f.run }).bind(f.input)).provenanceState, "NOT_BOUND");
  const first = await binder.bind(f.input), again = await binder.bind(f.input);
  assert.deepEqual(again, first); // repeat verification has no effect/ledger interface
  const child = spawnSync(process.execPath, ["--input-type=module", "-e", 'globalThis.fetch=()=>{throw Error("NETWORK")}; await import("./.github/actions/gate-deploy/maintenance-artifact.mjs");'], { cwd: process.cwd() });
  assert.equal(child.status, 0, child.stderr.toString());
});


test("post-upload provenance verification may finish after grant expiry without new effects", async () => {
  const f = bindingFixture(); f.input.admissionRequest.now = at(1000);
  assert.equal((await createMaintenanceArtifactBinding(f.deps).bind(f.input)).provenanceState, "BOUND");
});
test("changing grant/context/payload while metadata awaits cannot change captured originals", async () => {
  const f = bindingFixture(); const read = f.deps.readRun;
  f.deps.readRun = async (...args) => { f.input.payload.receiptBytes.fill(0); f.input.payload.digestBytes.fill(0); f.input.payload.context.executorSha = "d".repeat(40); f.input.admissionRequest.grantBytes.fill(0); return read(...args); };
  assert.equal((await createMaintenanceArtifactBinding(f.deps).bind(f.input)).provenanceState, "BOUND");
});
