import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import { fastDeploy, persistReceipt } from "../../.github/actions/gate-deploy/gate-deploy.mjs";
import {
  verifyReleaseReceipt, verifyReceiptFiles, verifySource,
} from "../../.github/actions/gate-deploy/release-checks.mjs";

const sha = "a".repeat(40);
const id = "11111111-1111-4111-8111-111111111111";
const context = {
  approvedSourceSha: sha, executorSha: sha, workflowSha: sha, checkedOutSha: sha,
  runId: "33000000000",
};
const modulePath = resolve(".github/actions/gate-deploy/release-checks.mjs");
const workflow = await readFile(".github/workflows/ops-deploy.yml", "utf8");

function stepScript(name) {
  const start = workflow.indexOf(`      - name: ${name}\n`);
  assert.ok(start >= 0);
  const after = workflow.indexOf("\n      - ", start + 1);
  const block = workflow.slice(start, after < 0 ? undefined : after);
  const runStart = block.indexOf("        run: |\n");
  assert.ok(runStart >= 0);
  const code = block.slice(runStart + "        run: |\n".length);
  return code.split("\n").map((line) => line.startsWith("          ") ? line.slice(10) : line).join("\n");
}

async function realReceipt() {
  const calls = [];
  const receipt = await fastDeploy({
    now: () => "2026-11-06T12:00:00.000Z",
    github: { async resolveMain(repo) { calls.push(["read", repo]); return sha; } },
    railway: {
      async authenticate() { calls.push(["auth"]); },
      async deploy(service, source) { calls.push(["deploy", service, source]); return id; },
      async wait(service, deployment, source) { calls.push(["wait", service, deployment, source]); },
    },
  }, { expectedSourceSha: sha, executorSha: sha, target: "gate", runId: context.runId });
  assert.equal(receipt.result, "SUCCEEDED");
  assert.equal(calls.length, 4); // Only synthetic dependencies; no fetch/provider client.
  return receipt;
}

function encoded(receipt) {
  const receiptBytes = Buffer.from(JSON.stringify(receipt, null, 2) + "\n");
  return encodedBytes(receiptBytes);
}

function encodedBytes(receiptBytes) {
  const outputDigest = "sha256:" + createHash("sha256").update(receiptBytes).digest("hex");
  return { receiptBytes, outputDigest, outputResult: "SUCCEEDED", context,
    digestBytes: Buffer.from(`${outputDigest}  ops-fast-deploy-receipt.json\n`) };
}

function environment(directory, input) {
  return {
    GITHUB_WORKSPACE: directory, APPROVED_SOURCE_SHA: context.approvedSourceSha,
    EXECUTOR_SHA: context.executorSha, WORKFLOW_SHA: context.workflowSha,
    CHECKED_OUT_SHA: context.checkedOutSha, RUN_ID: context.runId,
    RESULT: input.outputResult, RECEIPT: input.outputDigest,
  };
}

test("strict verifier accepts actual unchanged producer and persistReceipt artifacts", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gate-receipt-check-"));
  try {
    const receipt = await realReceipt();
    const persisted = await persistReceipt(directory, receipt);
    const input = encoded(receipt);
    assert.equal(persisted.digest, input.outputDigest);
    assert.deepEqual(verifyReleaseReceipt(input), receipt);
    assert.deepEqual(verifyReceiptFiles(environment(directory, input)), receipt);
    const cli = spawnSync(process.execPath, [modulePath], {
      env: environment(directory, input), encoding: "utf8",
    });
    assert.equal(cli.status, 0, cli.stderr);
    assert.equal(cli.stdout.trim(), "GATE_RELEASE_RECEIPT_ACCEPTED");
    assert.equal(cli.stderr, "");
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("source predicate rejects every missing, malformed and unequal SHA", () => {
  assert.equal(verifySource(context), sha);
  for (const invalid of [undefined, null, [], "source", 1]) {
    assert.throws(() => verifySource(invalid), /REJECTED/);
  }
  for (const key of ["approvedSourceSha", "executorSha", "workflowSha", "checkedOutSha"]) {
    for (const value of [undefined, "", "main", "a".repeat(39), "A".repeat(40), "b".repeat(40)]) {
      assert.throws(() => verifySource({ ...context, [key]: value }), /REJECTED/);
    }
  }
});

test("actual workflow source step prevents the next action sentinel on wrong SHA", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gate-source-check-"));
  try {
    const actual = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    const expected = { EXPECTED_SHA: actual, WORKFLOW_SHA: actual, APPROVED_SOURCE_SHA: actual };
    const cases = [
      {}, { APPROVED_SOURCE_SHA: "" }, { EXPECTED_SHA: "" }, { WORKFLOW_SHA: "" },
      { APPROVED_SOURCE_SHA: "b".repeat(40) }, { EXPECTED_SHA: "b".repeat(40) },
      { WORKFLOW_SHA: "b".repeat(40) }, { APPROVED_SOURCE_SHA: "main" },
      { EXPECTED_SHA: "c".repeat(40), WORKFLOW_SHA: "c".repeat(40),
        APPROVED_SOURCE_SHA: "c".repeat(40) }, // All forged equal, real checkout differs.
    ];
    for (let i = 0; i < cases.length; i++) {
      const sentinel = join(directory, `action-${i}`);
      const output = join(directory, `output-${i}`);
      const child = spawnSync("bash", ["-c", stepScript("Verify executor source")
        + '\nprintf "called" > "$SENTINEL"\n'], {
        cwd: process.cwd(), encoding: "utf8",
        env: { PATH: process.env.PATH, ...expected, ...cases[i],
          GITHUB_OUTPUT: output, SENTINEL: sentinel },
      });
      assert.equal(child.status === 0, i === 0, child.stderr);
      assert.equal(existsSync(sentinel), i === 0);
      if (i === 0) assert.equal(await readFile(output, "utf8"), `sha=${actual}\n`);
    }
    assert.ok(workflow.indexOf("name: Verify executor source") < workflow.indexOf("- id: deploy"));
    assert.ok(workflow.includes("APPROVED_SOURCE_SHA: ${{ steps.request.outputs.source_sha }}"));
  } finally { await rm(directory, { recursive: true, force: true }); }
});

const topMutations = [
  ["schemaVersion", "agent-ops.fast-deploy/v1"], ["sourceContract", "other-contract"],
  ["operation", "READ"], ["environment", "staging"], ["target", "aqub"],
  ["expectedSourceSha", "b".repeat(40)], ["executorSha", "b".repeat(40)],
  ["executorRepository", "markmalina/ops"], ["executorPath", "other.mjs"],
  ["upstreamRepository", "other/repo"], ["upstreamSha", "c".repeat(40)],
  ["runId", "33000000001"], ["runId", 33000000000],
  ["result", "FAILED"], ["result", "UNKNOWN"], ["diagnostic", { code: "E_AUTH" }],
  ["startedAt", "invalid"], ["finishedAt", "2026-01-01T00:00:00.000Z"],
  ["deployments", []], ["deployments", null], ["extra", "not-in-contract"],
];
for (const [key, value] of topMutations) {
  test(`receipt rejects ${key}=${JSON.stringify(value)} even with matching digest`, async () => {
    const receipt = await realReceipt();
    receipt[key] = value;
    assert.throws(() => verifyReleaseReceipt(encoded(receipt)), /REJECTED/);
  });
}

for (const [key, value] of [
  ["name", "other"], ["repository", "foreign/repo"], ["serviceId", id],
  ["sha", "b".repeat(40)], ["status", "FAILED"], ["status", "UNKNOWN"],
  ["deploymentId", null], ["deploymentId", ""], ["deploymentId", "not-uuid"],
  ["extra", "unexpected"],
]) {
  test(`receipt rejects deployment ${key}=${JSON.stringify(value)}`, async () => {
    const receipt = await realReceipt();
    receipt.deployments[0][key] = value;
    assert.throws(() => verifyReleaseReceipt(encoded(receipt)), /REJECTED/);
  });
}

test("missing fields and two deployment entries cannot be accepted", async () => {
  const original = await realReceipt();
  for (const key of Object.keys(original)) {
    const receipt = structuredClone(original);
    delete receipt[key];
    assert.throws(() => verifyReleaseReceipt(encoded(receipt)), /REJECTED/);
  }
  for (const key of Object.keys(original.deployments[0])) {
    const receipt = structuredClone(original);
    delete receipt.deployments[0][key];
    assert.throws(() => verifyReleaseReceipt(encoded(receipt)), /REJECTED/);
  }
  original.deployments.push({ ...original.deployments[0] });
  assert.throws(() => verifyReleaseReceipt(encoded(original)), /REJECTED/);
});

test("digest, byte, output, source and run mismatches fail closed", async () => {
  const good = encoded(await realReceipt());
  for (const patch of [
    { outputResult: "FAILED" }, { outputResult: "UNKNOWN" }, { outputResult: undefined },
    { outputDigest: "sha256:" + "0".repeat(64) }, { outputDigest: good.outputDigest + "\n" },
    { receiptBytes: Buffer.concat([good.receiptBytes, Buffer.from(" ")]) },
    { digestBytes: Buffer.from(good.digestBytes.toString().replace("  ops-", " ops-")) },
    { digestBytes: Buffer.from(good.digestBytes.toString().trimEnd()) },
    { digestBytes: Buffer.from(good.digestBytes.toString().replace("receipt.json", "other.json")) },
    { context: { ...context, runId: "" } },
    { context: { ...context, runId: "33000000001" } },
    { context: { ...context, workflowSha: "b".repeat(40) } },
  ]) assert.throws(() => verifyReleaseReceipt({ ...good, ...patch }), /REJECTED/);
});

test("malformed, truncated, non-object, duplicate-key and non-UTF8 JSON reject safely", async () => {
  const valid = encoded(await realReceipt()).receiptBytes;
  const duplicate = Buffer.from(valid.toString().replace("{\n",
    '{\n  "schemaVersion": "synthetic-discarded-duplicate",\n'));
  for (const bytes of [Buffer.from("{"), Buffer.from("[]\n"), Buffer.from("null\n"),
    Buffer.from('{"schemaVersion":"secret-synthetic","schemaVersion":"x"}\n'),
    Buffer.from([0xff, 0xfe]), Buffer.alloc(65537, 32), duplicate,
    Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), valid]),
    Buffer.concat([valid, Buffer.from(" ")])]) {
    assert.throws(() => verifyReleaseReceipt(encodedBytes(bytes)), /^Error: GATE_RELEASE_RECEIPT_REJECTED$/);
  }
});

test("real terminal workflow script rejects bad artifacts without logging or rewriting them", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gate-terminal-check-"));
  try {
    const terminal = workflow.slice(workflow.indexOf("      - name: Accept terminal result"));
    for (const [key, expression] of [
      ["RESULT", "steps.deploy.outputs.result"], ["RECEIPT", "steps.deploy.outputs.receipt-digest"],
      ["APPROVED_SOURCE_SHA", "steps.request.outputs.source_sha"],
      ["EXECUTOR_SHA", "github.sha"], ["WORKFLOW_SHA", "github.workflow_sha"],
      ["CHECKED_OUT_SHA", "steps.source.outputs.sha"], ["RUN_ID", "github.run_id"],
    ]) assert.ok(terminal.includes(`${key}: \u0024{{ ${expression} }}`));
    const good = encoded(await realReceipt());
    const file = join(directory, "ops-fast-deploy-receipt.json");
    const digestFile = join(directory, "ops-fast-deploy-receipt.sha256");
    const env = { PATH: process.env.PATH, ...environment(directory, good) };
    // Script uses the checked-out helper; fixed artifact paths use GITHUB_WORKSPACE.
    const invoke = () => spawnSync("bash", ["-c", stepScript("Accept terminal result")],
      { cwd: process.cwd(), encoding: "utf8", env });
    assert.notEqual(invoke().status, 0); // missing both files
    await writeFile(file, good.receiptBytes);
    assert.notEqual(invoke().status, 0); // missing digest
    await writeFile(digestFile, good.digestBytes);
    assert.equal(invoke().status, 0);
    const invalid = Buffer.from('{"synthetic-private-body":"do-not-print"');
    await writeFile(file, invalid);
    const child = invoke();
    assert.notEqual(child.status, 0);
    assert.equal(child.stderr.trim(), "GATE_RELEASE_RECEIPT_REJECTED");
    assert.ok(!child.stderr.includes("do-not-print") && !child.stdout.includes("do-not-print"));
    assert.deepEqual(await readFile(file), invalid);
    assert.deepEqual(await readFile(digestFile), good.digestBytes);
    await rm(file);
    await symlink(digestFile, file);
    assert.notEqual(invoke().status, 0); // never follow an artifact symlink
  } finally { await rm(directory, { recursive: true, force: true }); }
});
