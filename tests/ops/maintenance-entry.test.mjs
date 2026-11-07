import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { describe, it } from "node:test";
import { ARTIFACT_LIFECYCLE_GAP, MAINTENANCE_ENTRY_OFF, packMaintenanceRequest,
  runMaintenanceEntry } from "../../.github/actions/gate-deploy/maintenance-entry.mjs";
import { MAINTENANCE_CAPS, MAINTENANCE_CONTRACT_SHA256,
  MAINTENANCE_TARGET } from "../../.github/actions/gate-deploy/maintenance-admission.mjs";

const sha = "a".repeat(40), evidence = "b".repeat(64);
const canonical = (x) => Array.isArray(x) ? `[${x.map(canonical).join(",")}]` : x !== null && typeof x === "object"
  ? `{${Object.keys(x).sort().map((k) => `${JSON.stringify(k)}:${canonical(x[k])}`).join(",")}}` : JSON.stringify(x);
function fixture() {
  const grant = { schemaVersion: "gate.maintenance-owner-request/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
    repositoryId: 1293746267, ownerId: 23397294, repository: "markmalina/gate", target: "gate",
    ...MAINTENANCE_TARGET, fullGateDowntime: true, contractSha256: MAINTENANCE_CONTRACT_SHA256,
    operationId: randomUUID(), expectedDeploymentId: randomUUID(), expectedSha: sha,
    sourceRepository: "markmalina/gate", expectedExecutorSha: sha, evidenceSha256: evidence,
    notBefore: "2030-01-01T00:00:00.000Z", expiresAt: "2030-01-01T00:15:00.000Z", caps: MAINTENANCE_CAPS };
  const grantBytes = Buffer.from(canonical(grant));
  const digest = createHash("sha256").update(grantBytes).digest("hex");
  const command = `/ops-gate-maintenance hold expected-source-sha=${sha} expected-deployment-id=${grant.expectedDeploymentId} grant-digest=${digest}`;
  return { sourceGuard: MAINTENANCE_ENTRY_OFF, request: { now: "2030-01-01T00:01:00.000Z",
    event: { repository: "markmalina/gate", repositoryId: 1293746267, ownerId: 23397294,
      actor: "markmalina", actorId: 23397294, sender: "markmalina", senderId: 23397294,
      eventName: "issue_comment", action: "created", issueNumber: 815, isPullRequest: false,
      ref: "refs/heads/main", runAttempt: 1, runId: "12345",
      commentBody: `${command}\n\nmaintenance-grant-base64:${grantBytes.toString("base64")}` },
    source: { sourceContextSha: sha, currentMainSha: sha, checkedOutSha: sha, workflowSha: sha,
      clientSha: sha, executorSha: sha, evidenceSha256: evidence } } };
}

describe("default-OFF maintenance hosted entry", () => {
  it("uses the real admission parser and remains inert before secrets/effects", () => {
    let secrets = 0, effects = 0;
    const input = fixture();
    assert.equal(packMaintenanceRequest(input.request).admission.operationId.length, 36);
    const result = runMaintenanceEntry(input, { readSecret: () => secrets++, invokeOperation: () => effects++ });
    assert.deepEqual({ state: result.state, reason: result.reason, secretReads: result.secretReads, effects: result.effects },
      { state: "OFF", reason: "LITERAL_OFF", secretReads: 0, effects: 0 });
    assert.equal(secrets, 0); assert.equal(effects, 0);
  });

  it("stops an armed candidate at the real pre-upload artifact-id gap", () => {
    let secrets = 0, effects = 0;
    const input = fixture(); input.sourceGuard = "ARMED";
    const result = runMaintenanceEntry(input, { readSecret: () => secrets++, invokeOperation: () => effects++ });
    assert.equal(result.reason, ARTIFACT_LIFECYCLE_GAP);
    assert.equal(result.secretReads, 0); assert.equal(result.effects, 0);
    assert.equal(secrets, 0); assert.equal(effects, 0);
  });

  for (const [name, mutate] of [
    ["owner", (x) => { x.request.event.actorId = 1; }], ["repository", (x) => { x.request.event.repositoryId = 1; }],
    ["issue", (x) => { x.request.event.issueNumber = 816; }], ["pull request", (x) => { x.request.event.isPullRequest = true; }],
    ["action", (x) => { x.request.event.action = "edited"; }], ["ref", (x) => { x.request.event.ref = "refs/heads/other"; }],
    ["attempt", (x) => { x.request.event.runAttempt = 2; }], ["sha", (x) => { x.request.source.currentMainSha = "c".repeat(40); }],
    ["command", (x) => { x.request.event.commentBody = x.request.event.commentBody.replace(" hold ", " stop "); }],
    ["grant", (x) => { x.request.event.commentBody += "="; }], ["time", (x) => { x.request.now = "2030-01-01T00:15:00.000Z"; }],
  ]) it(`denies wrong ${name} before effects`, () => {
    const input = fixture(); mutate(input);
    let touched = false;
    assert.throws(() => runMaintenanceEntry(input, { readSecret: () => { touched = true; },
      invokeOperation: () => { touched = true; } }), /GATE_MAINTENANCE_(ENTRY|ADMISSION)_REJECTED/u);
    assert.equal(touched, false);
  });

  it("executes the CLI file packing boundary without environment credentials", async () => {
    const dir = await mkdtemp(join(tmpdir(), "gate-maintenance-entry-"));
    try {
      const input = join(dir, "input.json"), output = join(dir, "output.json");
      await writeFile(input, JSON.stringify(fixture()), { mode: 0o600 });
      const run = spawnSync(process.execPath,
        [".github/actions/gate-deploy/maintenance-entry.mjs", "preflight", input, output],
        { cwd: process.cwd(), encoding: "utf8", env: {} });
      assert.equal(run.status, 0, `${run.stderr}${run.stdout}`);
      assert.equal(JSON.parse(await readFile(output, "utf8")).reason, "LITERAL_OFF");
    } finally { await rm(dir, { recursive: true, force: true }); }
  });
});
