import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { verifyMaintenanceReceipt } from "../../.github/actions/gate-deploy/maintenance-receipt.mjs";
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
const input = (f) => ({ ...envelope(f.r), admissionRequest: f.admissionRequest, originalHeld: f.originalHeld });
const reject = (i) => assert.throws(() => verifyMaintenanceReceipt(i),
  (e) => e.message === "GATE_MAINTENANCE_RECEIPT_REJECTED");
function badReceipt(change, result) { const f = fixture(result); change(f.r, f); reject(input(f)); }
function rehashFleet(f) { const { sha256: ignored, ...rest } = f; f.sha256 = sha256(encode(rest)); }

test("actual bytes distinguish HELD, NOOP and valid non-success FAILED/UNKNOWN without granting authority", () => {
  for (const name of ["HELD_STOPPED", "NOOP_ALREADY_HELD", "FAILED", "UNKNOWN_STOP"]) {
    const f = fixture(name), i = input(f), output = verifyMaintenanceReceipt(i);
    assert.equal(output.result, name); assert.equal(output.schemaValid, true);
    assert.equal(output.success, name === "HELD_STOPPED" || name === "NOOP_ALREADY_HELD");
    assert.equal(output.receiptSha256, sha256(i.receiptBytes)); assert.equal(output.sourceMatches, true);
    assert.ok(Object.isFrozen(output.receipt.counters)); assert.equal(Object.hasOwn(output, "releaseReady"), false);
    i.receiptBytes.fill(0); assert.equal(output.receipt.operationId, op);
    assert.throws(() => { output.receipt.afterFleets[0] = null; });
  }
});

test("historical raw source stays explicit, false and bound to actual PR821 admission", () => {
  const f = fixture("NOOP_ALREADY_HELD", "AlphaCenter/gate"), out = verifyMaintenanceReceipt(input(f));
  assert.equal(out.sourceMatches, false); assert.equal(out.sourceRepoState, "HISTORICAL_GATE_ALIAS");
  assert.equal(out.receipt.afterFleets[0].source.repo, "AlphaCenter/gate");
  badReceipt((r) => { r.sourceRepository = "AlphaCenter/gate"; });
  f.r.sourceMatches = true; reject(input(f));
});

test("NOOP requires separately validated exact original HELD, never empty fleet or failed/unknown proof", () => {
  const f = fixture("NOOP_ALREADY_HELD"); reject({ ...input(f), originalHeld: null });
  for (const status of ["FAILED", "UNKNOWN_STOP", "NOOP_ALREADY_HELD"]) {
    const p = fixture(status), candidate = fixture("NOOP_ALREADY_HELD");
    candidate.originalHeld = envelope(p.r); candidate.r.originalReceiptSha256 = sha256(candidate.originalHeld.receiptBytes);
    reject(input(candidate));
  }
  const mutations = { operationId: "33333333-3333-4333-8333-333333333333", grantDigest: "d".repeat(64),
    target: "all", serviceId: op, expectedDeploymentId: op, observedDeploymentId: op,
    expectedSha: executor, observedSha: executor, sourceRepository: "AlphaCenter/gate", executorSha: runtime, clientSha: runtime };
  for (const [key, value] of Object.entries(mutations)) {
    const candidate = fixture("NOOP_ALREADY_HELD"), original = JSON.parse(candidate.originalHeld.receiptBytes);
    original[key] = value; candidate.originalHeld = envelope(original);
    candidate.r.originalReceiptSha256 = sha256(candidate.originalHeld.receiptBytes); reject(input(candidate));
  }
  badReceipt((r) => { r.originalReceiptSha256 = "d".repeat(64); }, "NOOP_ALREADY_HELD");
  badReceipt((r) => { r.startedAt = at(9); }, "NOOP_ALREADY_HELD");
  badReceipt((r) => { r.stopAttempted = true; r.counters.stop = 1; }, "NOOP_ALREADY_HELD");
  badReceipt((r) => { r.beforeFleets = [fleet(true, 21, "markmalina/gate"), null]; }, "NOOP_ALREADY_HELD");
});

test("lost ACK/incomplete readback cannot classify HELD; FAILED/UNKNOWN never become success", () => {
  for (const change of [r => { r.ack = false; }, r => { r.stopAttempted = false; }, r => { r.counters.stop = 0; },
    r => { r.deployment = null; r.stopped = null; r.observedAt = null; }, r => { r.stopped = false; },
    r => { r.deployment.stopped = false; r.stopped = false; }, r => { r.afterFleets[1] = null; },
    r => { r.afterFleets = [null, null]; }, r => { r.beforeFleets[1] = null; }, r => { r.phase = "stop"; },
    r => { r.diagnostic = { code: "E_TIMEOUT", category: "TIMEOUT", httpStatus: 200 }; }]) badReceipt(change);
  for (const status of ["FAILED", "UNKNOWN_STOP"]) {
    badReceipt(r => { r.phase = "complete"; }, status);
    badReceipt(r => { r.diagnostic = null; }, status);
  }
  badReceipt(r => { r.stopAttempted = true; r.counters.stop = 1; }, "FAILED");
  badReceipt(r => { r.stopAttempted = false; r.counters.stop = 0; }, "UNKNOWN_STOP");
  // Even a complete stopped observation does not rewrite an uncertain original.
  const f = fixture("UNKNOWN_STOP"), success = fixture();
  for (const key of ["afterFleets", "deployment", "stopped", "observedAt", "observedSha", "observedDeploymentId"]) f.r[key] = success.r[key];
  assert.equal(verifyMaintenanceReceipt(input(f)).success, false);
});

test("failure before first read keeps unknown observed identifiers null; copied expectations and partial null deny", () => {
  const f = fixture("FAILED");
  Object.assign(f.r.counters, { providerRequests: 0, maxHttpMs: 0, maxBodyBytes: 0 });
  f.r.phase = "authenticate";
  f.r.diagnostic = { code: "E_AUTH", category: "AUTH", httpStatus: null };
  const result = verifyMaintenanceReceipt(input(f));
  assert.equal(result.success, false);
  assert.equal(result.receipt.observedSha, null); assert.equal(result.receipt.observedDeploymentId, null);
  for (const patch of [{ observedSha: runtime }, { observedDeploymentId: id },
    { observedSha: runtime, observedDeploymentId: id }]) {
    const changed = clone(f); Object.assign(changed.r, patch); reject(input(changed));
  }
  // A received HTTP authentication denial also gives no verified deployment.
  f.r.counters.providerRequests = 1; f.r.counters.maxHttpMs = 100; f.r.counters.maxBodyBytes = 128;
  f.r.diagnostic.httpStatus = 401;
  assert.equal(verifyMaintenanceReceipt(input(f)).result, "FAILED");
  for (const status of ["HELD_STOPPED", "NOOP_ALREADY_HELD"])
    for (const key of ["observedSha", "observedDeploymentId"])
      badReceipt(r => { r[key] = null; }, status);
});

test("complete two fleets, exact latest and target/source drift checks use parsed rehashed bytes", () => {
  const changes = [f => { f.complete = false; }, f => { f.activeDeployments = [f.latestDeployment]; },
    f => { f.activeDeployments = [f.latestDeployment, f.latestDeployment]; }, f => { f.latestDeployment = null; },
    f => { f.latestDeployment.status = "DEPLOYING"; }, f => { f.latestDeployment.id = op; },
    f => { f.latestDeployment.sha = executor; }, f => { f.latestDeployment.stopped = false; },
    f => { f.source.repo = "AlphaCenter/gate"; }, f => { f.source.image = "image"; },
    f => { f.sourceMatches = false; }, f => { f.serviceId = op; }, f => { f.pendingChanges = true; },
    f => { f.autoDeploy = true; }, f => { f.numReplicas = 0; }, f => { f.numReplicas = 9; },
    f => { f.observedAt = at(11); }, f => { f.extra = "RAW_PROVIDER"; },
    f => { delete f.complete; }];
  for (const change of changes) for (const result of ["HELD_STOPPED", "NOOP_ALREADY_HELD"])
    badReceipt(r => { change(r.afterFleets[1]); rehashFleet(r.afterFleets[1]); }, result);
  badReceipt(r => { r.afterFleets[0].sha256 = "d".repeat(64); });
  badReceipt(r => { r.afterFleets[1].numReplicas = 2; rehashFleet(r.afterFleets[1]); });
  badReceipt(r => { r.afterFleets[0].observedAt = at(6); rehashFleet(r.afterFleets[0]); });
  badReceipt(r => { r.beforeFleets[1] = fleet(true, 2, "markmalina/gate"); });
  for (const key of [...Object.keys(target), "id", "sha", "status", "stopped", "observedAt"])
    badReceipt(r => { delete r.deployment[key]; });
});

test("fixed receipt fields and every missing/null/extra key deny coercion and substitutions", () => {
  const changes = { schemaVersion: "gate.local-deploy/v1", operation: "DEPLOY_CURRENT_MAIN", operationId: op.toUpperCase(),
    grantDigest: "d".repeat(64), repository: "AlphaCenter/gate", repositoryId: "1293746267", ownerId: "23397294",
    target: "all", projectId: op, environmentId: op, serviceId: op, expectedSha: executor, observedSha: executor,
    expectedDeploymentId: op, observedDeploymentId: op, actorId: "23397294", workflowRef: "ops-deploy.yml",
    workflowSha: runtime, runId: 33000000000, runAttempt: 2, executorSha: runtime, clientSha: runtime,
    evidenceSha256: "d".repeat(64), contractSha256: "d".repeat(64), recovery: "AUTO_RESTART", result: "SUCCEEDED",
    phase: "unknown", ack: "true", stopped: 1, stopAttempted: "true", sourceMatches: "true", sourceRepoState: "CANONICAL" };
  // UUID digits-only fixture has no case difference; replace with a distinct valid UUID.
  changes.operationId = "33333333-3333-4333-8333-333333333333";
  for (const [key, value] of Object.entries(changes)) badReceipt(r => { r[key] = value; });
  for (const key of Object.keys(fixture().r)) badReceipt(r => { delete r[key]; });
  badReceipt(r => { r.rawProviderError = "PRIVATE"; });
  badReceipt(r => { r.diagnostic = { code: "E_TIMEOUT", category: "TIMEOUT", httpStatus: null, raw: "PRIVATE" }; }, "UNKNOWN_STOP");
  for (const d of [{ code: "PRIVATE", category: "TIMEOUT", httpStatus: null },
    { code: "E_TIMEOUT", category: "PRIVATE", httpStatus: null }, { code: "E_TIMEOUT", category: "TIMEOUT", httpStatus: "200" },
    { code: "E_TIMEOUT", category: "TIMEOUT", httpStatus: 600 }]) badReceipt(r => { r.diagnostic = d; }, "UNKNOWN_STOP");
});

test("request counters and time/body/poll/attempt bounds are finite strict integers", () => {
  const changes = { providerRequests: 21, stop: 2, restart: 1, deploy: 1, config: 1, stt: 1,
    messages: 1, retries: 1, redirects: 1, polls: 13, maxHttpMs: 15001, maxBodyBytes: 524289, pollIntervalMs: 4999 };
  for (const [key, value] of Object.entries(changes)) {
    badReceipt(r => { r.counters[key] = value; });
    for (const bad of ["0", null, false, -1, 0.1]) badReceipt(r => { r.counters[key] = bad; });
    badReceipt(r => { delete r.counters[key]; });
  }
  badReceipt(r => { r.counters.providerRequests = 8; });
  badReceipt(r => { r.counters.maxBodyBytes = 0; });
  badReceipt(r => { r.counters.maxHttpMs = 0; });
  badReceipt(r => { r.counters.polls = 4; });
  badReceipt(r => { r.counters.maxHttpMs = 10001; });
  badReceipt(r => { r.counters.extra = 0; });
  for (const value of ["2030-01-01T00:00:00Z", "2030-01-01T03:00:00.000+03:00", "2030-02-30T00:00:00.000Z", null, 0])
    badReceipt(r => { r.startedAt = value; });
  for (const change of [r => { r.finishedAt = at(-1); }, r => { r.finishedAt = at(601); },
    r => { r.startedAt = at(-1); }, r => { r.observedAt = at(20); }, r => { r.finishedAt = at(701); }]) badReceipt(change);
});

test("trusted artifact context exact fields and original provenance are independently required", () => {
  const changes = { repository: "other/gate", repositoryId: "1293746267", ownerId: 0, actorId: 0,
    workflowRef: "ops-deploy", workflowSha: runtime, executorSha: runtime, clientSha: runtime,
    runId: "33000000001", runAttempt: 2, artifactId: 40000000000, artifactName: "other.json",
    outputDigest: "sha256:" + "d".repeat(64), outputResult: "FAILED" };
  for (const [key, value] of Object.entries(changes)) {
    const i = input(fixture()); i.context[key] = value; reject(i);
    const missing = input(fixture()); delete missing.context[key]; reject(missing);
    const f = fixture("NOOP_ALREADY_HELD"); f.originalHeld.context[key] = value; reject(input(f));
  }
  const f = input(fixture()); f.context.extra = "PRIVATE"; reject(f);
  let reads = 0;
  const accessor = input(fixture()); Object.defineProperty(accessor.context, "runId", { enumerable: true, get() { reads++; return "33000000000"; } });
  reject(accessor); assert.equal(reads, 0);
  for (const value of [undefined, null, [], {}, Object.create(input(fixture()))]) reject(value);
  const outer = input(fixture()); outer.extra = true; reject(outer);
});

test("actual PR821 parser denies fabricated admission output, invalid grant and source rebind", () => {
  const i = input(fixture()); reject({ ...i, admissionRequest: { state: "SOURCE_PREDICATE_READY" } });
  const f = fixture(); f.admissionRequest.context.actorId = 1; reject(input(f));
  const g = fixture(); const grant = JSON.parse(g.admissionRequest.grantBytes); grant.expectedSha = executor;
  g.admissionRequest.grantBytes = encode(grant); reject(input(g));
  const h = fixture(); h.admissionRequest.now = at(901); reject(input(h));
});

test("raw UTF8 original byte boundary rejects duplicates, BOM, extra/coerced fields and malleability", () => {
  const i = input(fixture()), raw = i.receiptBytes.toString("utf8");
  const altered = [raw + "\n", " " + raw, JSON.stringify(JSON.parse(raw), null, 2),
    raw.replace('"ack":true', '"ack":true,"ack":true'), raw.replace('"stop":1', '"stop":1e0'),
    raw.replace('"restart":0', '"restart":-0'), raw.replace("markmalina", "markmalin\\u0061"),
    raw.replace("markmalina", "\\ud800"), "null", "[]", "{", "", " ".repeat(32769)];
  for (const value of altered) {
    const b = Buffer.from(value), digest = sha256(b);
    reject({ ...i, receiptBytes: b, digestBytes: Buffer.from(`sha256:${digest}  maintenance-hold-receipt.json\n`),
      context: { ...i.context, outputDigest: `sha256:${digest}` } });
  }
  for (const b of [Buffer.from([255]), Buffer.concat([Buffer.from([239, 187, 191]), i.receiptBytes])]) reject({ ...i, receiptBytes: b });
  for (const receiptBytes of [raw, null, {}, [1, 2]]) reject({ ...i, receiptBytes });
  reject({ ...i, digestBytes: Buffer.from("wrong") }); reject({ ...i, digestBytes: Buffer.alloc(129) });
  reject({ ...i, digestBytes: Buffer.concat([i.digestBytes, Buffer.from("\n")]) });
  assert.equal(verifyMaintenanceReceipt({ ...i, receiptBytes: new Uint8Array(i.receiptBytes), digestBytes: new Uint8Array(i.digestBytes) }).success, true);
});

test("import and actual HELD/NOOP validation work with FS/env/fetch/clock capabilities denied", () => {
  const admissionSource = readFileSync(new URL("../../.github/actions/gate-deploy/maintenance-admission.mjs", import.meta.url), "utf8");
  const admissionUrl = "data:text/javascript;base64," + Buffer.from(admissionSource).toString("base64");
  const receiptSource = readFileSync(new URL("../../.github/actions/gate-deploy/maintenance-receipt.mjs", import.meta.url), "utf8")
    .replace('"./maintenance-admission.mjs"', JSON.stringify(admissionUrl));
  const moduleUrl = "data:text/javascript;base64," + Buffer.from(receiptSource).toString("base64");
  const samples = [input(fixture()), input(fixture("NOOP_ALREADY_HELD"))];
  function wire(i) { return { ...i, receiptBytes: i.receiptBytes.toString("base64"), digestBytes: i.digestBytes.toString("base64"),
    admissionRequest: { ...i.admissionRequest, grantBytes: i.admissionRequest.grantBytes.toString("base64") },
    originalHeld: i.originalHeld ? { ...i.originalHeld, receiptBytes: i.originalHeld.receiptBytes.toString("base64"), digestBytes: i.originalHeld.digestBytes.toString("base64") } : null }; }
  const script = `import fs from 'node:fs'; import {syncBuiltinESMExports} from 'node:module';
    for(const name of ['readFileSync','openSync','writeFileSync','statSync'])fs[name]=()=>{throw Error('FS forbidden')};
    syncBuiltinESMExports(); globalThis.fetch=()=>{throw Error('network forbidden')};
    Date.now=()=>{throw Error('clock forbidden')};
    const m=await import(${JSON.stringify(moduleUrl)});
    const samples=${JSON.stringify(samples.map(wire))};
    process.env=new Proxy({}, {get(){throw Error('env forbidden')}});
    for(const i of samples){i.receiptBytes=Buffer.from(i.receiptBytes,'base64');i.digestBytes=Buffer.from(i.digestBytes,'base64');
      i.admissionRequest.grantBytes=Buffer.from(i.admissionRequest.grantBytes,'base64');
      if(i.originalHeld){i.originalHeld.receiptBytes=Buffer.from(i.originalHeld.receiptBytes,'base64');i.originalHeld.digestBytes=Buffer.from(i.originalHeld.digestBytes,'base64')}
      if(!m.verifyMaintenanceReceipt(i).success)throw Error('validation failed');}`;
  const run = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8", env: {} });
  assert.equal(run.status, 0, run.stderr);
});

test("retrospective FAILED/UNKNOWN admits only truthful bounded failure evidence after grant expiry",()=>{
  for(const result of ["FAILED","UNKNOWN_STOP"]){const f=fixture(result);f.r.startedAt=at(899);f.r.finishedAt=at(901);
    f.r.beforeFleets=[null,null];f.r.afterFleets=[null,null];f.r.counters.polls=0;f.r.counters.maxHttpMs=100;
    f.admissionRequest.now=at(902);const out=verifyMaintenanceReceipt(input(f));assert.equal(out.success,false);assert.equal(out.result,result);
    f.r.finishedAt=at(916);f.admissionRequest.now=at(917);reject(input(f));
    f.r.startedAt=at(900);f.r.finishedAt=at(901);reject(input(f));
  }
  const held=fixture();held.r.finishedAt=at(901);held.admissionRequest.now=at(902);reject(input(held));
  const future=fixture("UNKNOWN_STOP");future.r.startedAt=at(899);future.r.finishedAt=at(901);future.admissionRequest.now=at(900);reject(input(future));
});

test("body-limit overruns are exact safe counters only for explicit FAILED/UNKNOWN evidence, never success",()=>{
  for(const result of ["FAILED","UNKNOWN_STOP"]){const f=fixture(result);f.r.counters.maxBodyBytes=524289;
    reject(input(f));f.r.diagnostic={code:"E_BODY",category:"TRANSPORT",httpStatus:200};
    const v=verifyMaintenanceReceipt(input(f));assert.equal(v.receipt.counters.maxBodyBytes,524289);assert.equal(v.success,false);
    f.r.counters.maxBodyBytes=Number.MAX_SAFE_INTEGER;assert.equal(verifyMaintenanceReceipt(input(f)).success,false);
    f.r.counters.maxBodyBytes=Number.MAX_SAFE_INTEGER+1;reject(input(f));
    f.r.counters.maxBodyBytes=524289;f.r.diagnostic.category="READBACK";reject(input(f));
    f.r.diagnostic.category="TRANSPORT";f.r.counters.providerRequests=0;reject(input(f));
  }
  for(const result of ["HELD_STOPPED","NOOP_ALREADY_HELD"]){const f=fixture(result);f.r.counters.maxBodyBytes=524289;reject(input(f));}
});
