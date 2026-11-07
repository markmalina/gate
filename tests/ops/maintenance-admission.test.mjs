import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import test from "node:test";
import { admitMaintenanceRequest, MAINTENANCE_CAPS, MAINTENANCE_TARGET,
  MAINTENANCE_CONTRACT_SHA256, PROPOSED_WORKFLOW_REF } from "../../.github/actions/gate-deploy/maintenance-admission.mjs";
import { makeFixedGateMaintenance } from "../../.github/actions/gate-deploy/gate-deploy.mjs";

const executor = "a".repeat(40), runtime = "b".repeat(40), evidence = "c".repeat(64);
const deploymentId = "11111111-1111-4111-8111-111111111111";
const operationId = "22222222-2222-4222-8222-222222222222";
// Independent encoder for the documented compact sorted-key JSON contract.
const encode = (value) => Buffer.from(JSON.stringify(value, function (key, item) {
  return item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((name) => [name, item[name]])) : item;
}));
const digest = (value) => createHash("sha256").update(value).digest("hex");
function fixture(patch = {}) {
  const grant = { schemaVersion: "gate.maintenance-owner-request/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
    operationId, repositoryId: 1293746267, ownerId: 23397294, repository: "markmalina/gate", target: "gate",
    ...MAINTENANCE_TARGET, expectedDeploymentId: deploymentId, expectedSha: runtime,
    sourceRepository: "markmalina/gate", expectedExecutorSha: executor, evidenceSha256: evidence,
    fullGateDowntime: true, contractSha256: MAINTENANCE_CONTRACT_SHA256,
    notBefore: "2030-01-01T00:00:00.000Z", expiresAt: "2030-01-01T00:15:00.000Z",
    caps: { ...MAINTENANCE_CAPS }, ...patch };
  const grantBytes = encode(grant);
  const command = `/ops-gate-maintenance hold expected-source-sha=${grant.expectedSha} expected-deployment-id=${grant.expectedDeploymentId} grant-digest=${digest(grantBytes)}`;
  const context = { repository: "markmalina/gate", repositoryId: 1293746267, ownerId: 23397294,
    actor: "markmalina", actorId: 23397294, sender: "markmalina", senderId: 23397294,
    eventName: "issue_comment", action: "created", issueNumber: 815, isPullRequest: false,
    ref: "refs/heads/main", runAttempt: 1, workflowRef: PROPOSED_WORKFLOW_REF,
    sourceContextSha: executor, currentMainSha: executor, checkedOutSha: executor,
    workflowSha: executor, clientSha: executor, executorSha: executor,
    runId: "33000000000", commentBody: command, evidenceSha256: evidence };
  return { grant, request: { grantBytes, command, context, now: "2030-01-01T00:05:00.000Z" } };
}
const deny = (request) => assert.throws(() => admitMaintenanceRequest(request),
  (error) => error.message === "GATE_MAINTENANCE_ADMISSION_REJECTED");

test("canonical grant binds exact owner command bytes, source and immutable result", () => {
  const { grant, request } = fixture();
  const result = admitMaintenanceRequest(request);
  assert.equal(result.state, "SOURCE_PREDICATE_READY");
  assert.equal(result.ownerGrantSha256, digest(request.grantBytes));
  assert.equal(result.operationId, operationId);
  assert.equal(result.recordedSourceBinding, null);
  assert.equal(result.sourceMatches, true);
  assert.deepEqual(result.grant, grant);
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.grant) && Object.isFrozen(result.grant.caps));
  request.grantBytes.fill(0); request.context.executorSha = "d".repeat(40);
  assert.equal(result.expectedExecutorSha, executor);
  assert.equal(result.grant.caps.stop, 1);
  assert.throws(() => { result.grant.caps.stop = 2; });
});

test("historical result produces exactly the PR819 binding consumed by actual transport", async () => {
  const { request } = fixture({ sourceRepository: "AlphaCenter/gate" });
  const result = admitMaintenanceRequest(request), binding = result.recordedSourceBinding;
  assert.deepEqual(binding, { schemaVersion: "gate.recorded-source-binding/v1", repositoryId: 1293746267,
    ownerId: 23397294, ...MAINTENANCE_TARGET, repository: "AlphaCenter/gate",
    expectedDeploymentId: deploymentId, expectedSha: runtime, evidenceSha256: evidence,
    ownerGrantSha256: digest(request.grantBytes) });
  assert.equal(result.sourceMatches, false);
  assert.equal(result.sourceRepoState, "HISTORICAL_GATE_ALIAS");
  assert.ok(Object.isFrozen(binding));
  assert.throws(() => { binding.repository = "arbitrary/gate"; });
  const kinds = [];
  const item = { ...MAINTENANCE_TARGET, id: deploymentId, status: "SUCCESS",
    meta: { commitSha: runtime }, deploymentStopped: true };
  const provider = makeFixedGateMaintenance({ token: "synthetic-project-token-not-secret", recordedSourceBinding: binding,
    fetchFn: async (url, options) => {
      const { query } = JSON.parse(options.body), kind = query.match(/query GateMaintenance(\w+)/u)[1];
      kinds.push(kind);
      let data;
      if (kind === "Auth") data = { projectToken: { id: operationId,
        project: { id: MAINTENANCE_TARGET.projectId }, environment: { id: MAINTENANCE_TARGET.environmentId } },
        project: { id: MAINTENANCE_TARGET.projectId, services: { edges: [{ node: { id: MAINTENANCE_TARGET.serviceId } }] } } };
      else if (kind === "Deployment") data = { deployment: item };
      else { assert.equal(kind, "Fleet"); data = {
        environment: { id: MAINTENANCE_TARGET.environmentId, unmergedChangesCount: 0 },
        environmentStagedChanges: { environmentId: MAINTENANCE_TARGET.environmentId, status: "STAGED", patch: {} },
        serviceInstance: { serviceId: MAINTENANCE_TARGET.serviceId, environmentId: MAINTENANCE_TARGET.environmentId,
          numReplicas: 1, source: { repo: "AlphaCenter/gate", image: null }, latestDeployment: item, activeDeployments: [] },
        serviceInstanceAutoDeployStatus: { enabled: false } }; }
      return Response.json({ data });
    } });
  const observed = await provider.inspectFixedGate(runtime, deploymentId);
  assert.equal(observed.fleet.sourceMatches, false);
  assert.equal(observed.fleet.source.repo, "AlphaCenter/gate");
  assert.ok(kinds.length > 0 && !kinds.includes("Stop"));
});

test("every trusted fixed identity/event/ref field denies drift and coercion", () => {
  const { request } = fixture();
  for (const input of [undefined, null, [], { ...request, extra: true }]) deny(input);
  let outerReads = 0;
  const outer = { ...request };
  Object.defineProperty(outer, "context", { enumerable: true, get() { outerReads++; return request.context; } });
  deny(outer); assert.equal(outerReads, 0);
  const changes = { repository: "other/gate", repositoryId: "1293746267", ownerId: 1,
    actor: "other", actorId: "23397294", sender: "other", senderId: 1,
    eventName: "pull_request_target", action: "edited", issueNumber: 751, isPullRequest: true,
    ref: "refs/heads/feature", runAttempt: 2, workflowRef: "markmalina/gate/.github/workflows/ops-deploy.yml@refs/heads/main",
    sourceContextSha: "d".repeat(40), currentMainSha: "d".repeat(40), checkedOutSha: "d".repeat(40),
    workflowSha: "d".repeat(40), clientSha: "d".repeat(40), executorSha: "d".repeat(40),
    runId: 33000000000, evidenceSha256: "d".repeat(64), commentBody: "hello" };
  for (const [key, value] of Object.entries(changes)) {
    deny({ ...request, context: { ...request.context, [key]: value } });
    const missing = { ...request.context }; delete missing[key];
    deny({ ...request, context: missing });
  }
  for (const context of [null, [], Object.create(request.context), { ...request.context, extra: 1 },
    { ...request.context, [Symbol("extra")]: 1 }]) deny({ ...request, context });
  let reads = 0;
  const accessor = { ...request.context };
  Object.defineProperty(accessor, "actorId", { enumerable: true, get() { reads++; return 23397294; } });
  deny({ ...request, context: accessor }); assert.equal(reads, 0);
});

test("every fixed grant target, type, source, UUID, digest and downtime field is strict", () => {
  const changes = { schemaVersion: "other", operation: "deploy", operationId: "new-per-run",
    repositoryId: "1293746267", ownerId: "23397294", repository: "AlphaCenter/gate", target: "all",
    projectId: operationId, environmentId: operationId, serviceId: operationId,
    expectedDeploymentId: "not-uuid", expectedSha: "B".repeat(40), expectedExecutorSha: [executor],
    evidenceSha256: "short", sourceRepository: "alphacenter/gate", fullGateDowntime: false,
    contractSha256: "d".repeat(64), ownerGrantSha256: "d".repeat(64), extra: "PRIVATE" };
  for (const [key, value] of Object.entries(changes)) deny(fixture({ [key]: value }).request);
  const { grant } = fixture();
  for (const key of Object.keys(grant)) {
    const missing = { ...grant }; delete missing[key];
    const { request } = fixture(); request.grantBytes = encode(missing); deny(request);
    deny(fixture({ [key]: null }).request);
  }
  for (const value of [null, [], "1", { ...MAINTENANCE_CAPS, extra: 0 }]) deny(fixture({ caps: value }).request);
  for (const [key, value] of Object.entries(MAINTENANCE_CAPS)) {
    const missing = { ...MAINTENANCE_CAPS }; delete missing[key];
    deny(fixture({ caps: missing }).request);
    for (const changed of [value + 1, String(value), null, false])
      deny(fixture({ caps: { ...MAINTENANCE_CAPS, [key]: changed } }).request);
  }
});

test("exact grammar rejects arbitrary owner text and command/grant swaps", () => {
  const { request } = fixture();
  for (const command of ["hello", request.command + "\n", " " + request.command, request.command + " target=all",
    request.command.replace(runtime, executor), request.command.replace(deploymentId, operationId),
    request.command.replace(/grant-digest=.*/u, `grant-digest=${"d".repeat(64)}`), [request.command]]) {
    deny({ ...request, command, context: { ...request.context, commentBody: command } });
  }
  const changed = fixture({ operationId: "33333333-3333-4333-8333-333333333333" }).request;
  deny({ ...request, grantBytes: changed.grantBytes });
  deny({ ...request, context: { ...request.context, commentBody: changed.command } });
});

test("canonical UTC window is finite, positive, at most15min, inclusive start/exclusive expiry", () => {
  const { request } = fixture();
  assert.ok(admitMaintenanceRequest({ ...request, now: "2030-01-01T00:00:00.000Z" }));
  for (const now of ["2029-12-31T23:59:59.999Z", "2030-01-01T00:15:00.000Z", "2030-01-01T00:15:00.001Z",
    "2030-01-01T00:05:00Z", "2030-01-01T03:05:00.000+03:00", Infinity, NaN, null]) deny({ ...request, now });
  for (const patch of [{ expiresAt: "2030-01-01T00:15:00.001Z" },
    { expiresAt: "2030-01-01T00:00:00.000Z" }, { notBefore: "2030-01-01T00:06:00.000Z" },
    { notBefore: "2030-02-30T00:00:00.000Z" }, { expiresAt: "forever" },
    { notBefore: "2030-01-01T00:00:00Z" }, { expiresAt: null }]) deny(fixture(patch).request);
});

test("raw byte boundary denies duplicates, malformed UTF8, BOM, escapes, size and serialization malleability", () => {
  const { request } = fixture(), text = request.grantBytes.toString("utf8");
  const altered = [Buffer.from(text + "\n"), Buffer.from(" " + text), Buffer.from(JSON.stringify(JSON.parse(text), null, 2)),
    Buffer.from(text.replace('"stop":1', '"stop":1,"stop":1')),
    Buffer.from(text.replace('"ownerId":23397294', '"ownerId":1,"ownerId":23397294')),
    Buffer.from(text.replace('"stop":1', '"stop":1e0')),
    Buffer.from(text.replace('"restart":0', '"restart":-0')),
    Buffer.from(text.replace("markmalina", "markmalin\\u0061")),
    Buffer.concat([Buffer.from([239, 187, 191]), request.grantBytes]), Buffer.from([255]),
    Buffer.from(text.replace("markmalina", "\\ud800")), Buffer.alloc(8193, 32), Buffer.alloc(0),
    Buffer.from("null"), Buffer.from("[]"), Buffer.from("{"), text, null, {}, [1, 2]];
  for (const grantBytes of altered) deny({ ...request, grantBytes });
  assert.ok(admitMaintenanceRequest({ ...request, grantBytes: new Uint8Array(request.grantBytes) }));
});

test("import and actual validation require no fetch, wall clock, env or filesystem capabilities", () => {
  const { request } = fixture(), moduleUrl = pathToFileURL(resolve(".github/actions/gate-deploy/maintenance-admission.mjs")).href;
  const child = spawnSync(process.execPath, ["--input-type=module", "-e", `
    import * as fs from 'node:fs';
    import { syncBuiltinESMExports } from 'node:module';
    const source=fs.readFileSync(new URL(${JSON.stringify(moduleUrl)}),'utf8');
    const dataUrl='data:text/javascript;base64,'+Buffer.from(source).toString('base64');
    const original=fs.default;
    for(const key of ['readFileSync','openSync','statSync']) original[key]=()=>{throw Error('FS forbidden')};
    syncBuiltinESMExports();
    globalThis.fetch=()=>{throw Error('network forbidden')}; Date.now=()=>{throw Error('clock forbidden')};
    const m=await import(dataUrl);
    const input=${JSON.stringify({ ...request, grantBytes: undefined })};
    input.grantBytes=Buffer.from(${JSON.stringify(request.grantBytes.toString("base64"))},'base64');
    process.env=new Proxy({}, {get(){throw Error('env forbidden')}});
    m.admitMaintenanceRequest(input);
  `], { encoding: "utf8", env: { PATH: process.env.PATH } });
  assert.equal(child.status, 0, child.stderr);
});
