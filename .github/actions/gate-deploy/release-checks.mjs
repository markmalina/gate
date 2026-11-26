// Read-only acceptance of the existing Gate-local executor's exact v1 artifacts.
import { createHash } from "node:crypto";
import { closeSync, constants, fstatSync, openSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SHA = /^[0-9a-f]{40}$/u;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/u;
const RUN_ID = /^[1-9][0-9]{0,19}$/u;
const RECEIPT_NAME = "ops-fast-deploy-receipt.json";
const DIGEST_NAME = "ops-fast-deploy-receipt.sha256";
const RECEIPT_KEYS = [
  "schemaVersion", "operation", "environment", "target", "expectedSourceSha", "runId",
  "executorSha", "executorRepository", "executorPath", "upstreamRepository", "upstreamSha",
  "sourceContract", "startedAt", "finishedAt", "deployments", "result", "diagnostic",
];
const DEPLOYMENT_KEYS = ["name", "repository", "serviceId", "sha", "deploymentId", "status"];
const MAX_RECEIPT_BYTES = 64 * 1024;

function requireCheck(condition) {
  if (!condition) throw new Error("GATE_RELEASE_RECEIPT_REJECTED");
}

function exactKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function canonicalTime(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
}

export function verifySource(context) {
  requireCheck(context !== null && typeof context === "object" && !Array.isArray(context));
  const { approvedSourceSha, executorSha, workflowSha, checkedOutSha } = context;
  requireCheck([approvedSourceSha, executorSha, workflowSha, checkedOutSha]
    .every((value) => typeof value === "string" && SHA.test(value)));
  requireCheck(approvedSourceSha === executorSha && workflowSha === executorSha
    && checkedOutSha === executorSha);
  return approvedSourceSha;
}

export function verifyReleaseReceipt({ receiptBytes, digestBytes, outputDigest, outputResult, context }) {
  const approved = verifySource(context);
  requireCheck(typeof context.runId === "string" && RUN_ID.test(context.runId));
  requireCheck(Buffer.isBuffer(receiptBytes) && receiptBytes.length > 0
    && receiptBytes.length <= MAX_RECEIPT_BYTES);
  requireCheck(Buffer.isBuffer(digestBytes) && digestBytes.length > 0 && digestBytes.length <= 256);
  const digest = "sha256:" + createHash("sha256").update(receiptBytes).digest("hex");
  requireCheck(outputDigest === digest
    && digestBytes.equals(Buffer.from(`${digest}  ${RECEIPT_NAME}\n`, "utf8")));
  let receipt;
  try {
    receipt = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(receiptBytes));
  } catch {
    throw new Error("GATE_RELEASE_RECEIPT_REJECTED");
  }
  requireCheck(exactKeys(receipt, RECEIPT_KEYS));
  // This is persistReceipt's existing serialization, not a new receipt version.
  // Round-trip equality also rejects duplicate JSON keys and malformed encodings.
  requireCheck(receiptBytes.equals(Buffer.from(`${JSON.stringify(receipt, null, 2)}\n`, "utf8")));
  requireCheck(receipt.schemaVersion === "gate.local-deploy/v1"
    && receipt.sourceContract === "gate.public-local-executor.v1"
    && receipt.operation === "DEPLOY_CURRENT_MAIN" && receipt.environment === "production"
    && receipt.target === "gate" && receipt.result === "SUCCEEDED"
    && outputResult === receipt.result && receipt.diagnostic === null
    && receipt.expectedSourceSha === approved && receipt.executorSha === context.executorSha
    && receipt.runId === context.runId && receipt.executorRepository === "markmalina/gate"
    && receipt.executorPath === ".github/actions/gate-deploy/gate-deploy.mjs"
    && receipt.upstreamRepository === "markmalina/ops"
    && receipt.upstreamSha === "02ef5f1c6f4273ca2a3194643383d646dbf37648");
  requireCheck(canonicalTime(receipt.startedAt) && canonicalTime(receipt.finishedAt)
    && Date.parse(receipt.finishedAt) >= Date.parse(receipt.startedAt));
  requireCheck(Array.isArray(receipt.deployments) && receipt.deployments.length === 1);
  const deployment = receipt.deployments[0];
  requireCheck(exactKeys(deployment, DEPLOYMENT_KEYS)
    && deployment.name === "gate" && deployment.repository === "markmalina/gate"
    && deployment.serviceId === "099cbf8c-1d94-43fe-8714-90725bf93998"
    && deployment.sha === approved && deployment.status === "SUCCESS"
    && typeof deployment.deploymentId === "string" && UUID.test(deployment.deploymentId));
  return receipt;
}

function readArtifact(directory, name, limit) {
  const fd = openSync(join(directory, name), constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(fd);
    requireCheck(stat.isFile() && stat.size > 0 && stat.size <= limit);
    const bytes = readFileSync(fd);
    requireCheck(bytes.length === stat.size && bytes.length <= limit);
    return bytes;
  } finally {
    closeSync(fd);
  }
}

export function verifyReceiptFiles(environment = process.env) {
  const directory = resolve(environment.GITHUB_WORKSPACE ?? process.cwd());
  return verifyReleaseReceipt({
    receiptBytes: readArtifact(directory, RECEIPT_NAME, MAX_RECEIPT_BYTES),
    digestBytes: readArtifact(directory, DIGEST_NAME, 256),
    outputDigest: environment.RECEIPT,
    outputResult: environment.RESULT,
    context: {
      approvedSourceSha: environment.APPROVED_SOURCE_SHA,
      executorSha: environment.EXECUTOR_SHA,
      workflowSha: environment.WORKFLOW_SHA,
      checkedOutSha: environment.CHECKED_OUT_SHA,
      runId: environment.RUN_ID,
    },
  });
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  try {
    verifyReceiptFiles();
    console.log("GATE_RELEASE_RECEIPT_ACCEPTED");
  } catch {
    // Do not log parse errors, filesystem details or artifact contents.
    console.error("GATE_RELEASE_RECEIPT_REJECTED");
    process.exitCode = 1;
  }
}
