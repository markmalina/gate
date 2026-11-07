// Two-phase source interface only; no default network, upload or effect adapter.
// The fixed trusted caller installs authenticated GitHub readers/downloader as
// executable capabilities, never as model/user JSON. Their origin is a caller
// installation prerequisite; fake offline adapters prove consistency, not live auth.
import { createHash } from "node:crypto";
import { inflateRawSync } from "node:zlib";
import { verifyMaintenancePayload, MAINTENANCE_RECEIPT_NAME } from "./maintenance-receipt.mjs";
import { PROPOSED_WORKFLOW_REF } from "./maintenance-admission.mjs";

const REPO = "markmalina/gate", REPO_ID = 1293746267;
const WORKFLOW = ".github/workflows/ops-maintenance.yml";
const ID = /^[1-9][0-9]{0,19}$/u, DIGEST = /^sha256:[0-9a-f]{64}$/u;
const need = ok => { if (!ok) throw Error("E_ARTIFACT_BINDING"); };
const hash = bytes => `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
const identifier = value => {
  need(typeof value === "string" || Number.isSafeInteger(value));
  const text = String(value); need(ID.test(text)); return text;
};
const denied = reason => Object.freeze({ provenanceState: "NOT_BOUND", reason, retryable: false });
function copyBytes(value, max) {
  need((Buffer.isBuffer(value) || value instanceof Uint8Array) && value.byteLength > 0 && value.byteLength <= max);
  return Buffer.from(value);
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
// Narrow supported ZIP subset: exactly two fixed files, stored/deflate, no
// encryption, ZIP64, directories, aliases or trailing payload. Hash the archive
// itself against GitHub's digest; detached receipt digest is a different hash.
export function unpackMaintenanceArtifact(input) {
  const zip = copyBytes(input, 131072);
  need(zip.length >= 22);
  const end = zip.length - 22;
  need(zip.readUInt32LE(end) === 0x06054b50 && zip.readUInt16LE(end + 20) === 0
    && zip.readUInt16LE(end + 4) === 0 && zip.readUInt16LE(end + 6) === 0
    && zip.readUInt16LE(end + 8) === 2 && zip.readUInt16LE(end + 10) === 2);
  const size = zip.readUInt32LE(end + 12), start = zip.readUInt32LE(end + 16);
  need(start + size === end);
  let cursor = start, localEnd = 0;
  const files = new Map();
  for (let i = 0; i < 2; i++) {
    need(cursor + 46 <= end && zip.readUInt32LE(cursor) === 0x02014b50);
    const flags = zip.readUInt16LE(cursor + 8), method = zip.readUInt16LE(cursor + 10);
    const crc = zip.readUInt32LE(cursor + 16), compressed = zip.readUInt32LE(cursor + 20), length = zip.readUInt32LE(cursor + 24);
    const n = zip.readUInt16LE(cursor + 28), extra = zip.readUInt16LE(cursor + 30), comment = zip.readUInt16LE(cursor + 32);
    const local = zip.readUInt32LE(cursor + 42), attrs = zip.readUInt32LE(cursor + 38) >>> 16;
    need((flags & ~0x808) === 0 && [0, 8].includes(method) && extra === 0 && comment === 0
      && zip.readUInt16LE(cursor + 34) === 0 && (attrs & 0xf000) !== 0xa000 && local === localEnd
      && cursor + 46 + n <= end && length > 0 && compressed > 0);
    const nameBytes = zip.subarray(cursor + 46, cursor + 46 + n), name = nameBytes.toString("utf8");
    need([MAINTENANCE_RECEIPT_NAME, `${MAINTENANCE_RECEIPT_NAME}.sha256`].includes(name) && !files.has(name));
    const max = name === MAINTENANCE_RECEIPT_NAME ? 32768 : 128;
    need(length <= max && local + 30 <= start && zip.readUInt32LE(local) === 0x04034b50
      && zip.readUInt16LE(local + 6) === flags && zip.readUInt16LE(local + 8) === method
      && zip.readUInt16LE(local + 26) === n && zip.readUInt16LE(local + 28) === 0
      && zip.subarray(local + 30, local + 30 + n).equals(nameBytes));
    const dataStart = local + 30 + n, dataEnd = dataStart + compressed;
    need(dataEnd <= start);
    const raw = method === 0 ? Buffer.from(zip.subarray(dataStart, dataEnd))
      : inflateRawSync(zip.subarray(dataStart, dataEnd), { maxOutputLength: max });
    need(raw.length === length && crc32(raw) === crc);
    localEnd = dataEnd;
    if (flags & 8) {
      need(localEnd + 16 <= start && zip.readUInt32LE(localEnd) === 0x08074b50
        && zip.readUInt32LE(localEnd + 4) === crc && zip.readUInt32LE(localEnd + 8) === compressed
        && zip.readUInt32LE(localEnd + 12) === length);
      localEnd += 16;
    } else need(zip.readUInt32LE(local + 14) === crc && zip.readUInt32LE(local + 18) === compressed
      && zip.readUInt32LE(local + 22) === length);
    files.set(name, raw); cursor += 46 + n;
  }
  need(cursor === end && localEnd === start);
  return { receiptBytes: files.get(MAINTENANCE_RECEIPT_NAME), digestBytes: files.get(`${MAINTENANCE_RECEIPT_NAME}.sha256`) };
}

export function createMaintenanceArtifactBinding({ readRun, readWorkflow, readArtifact, downloadArchive } = {}) {
  const enabled = [readRun, readWorkflow, readArtifact, downloadArchive].every(f => typeof f === "function");
  return Object.freeze({ async bind({ payload, admissionRequest, originalHeld = null, upload } = {}) {
    if (!enabled) return denied("MISSING_TRUSTED_READERS");
    try {
      // Snapshot bytes/context before any await; caller mutation cannot switch
      // the receipt, grant or expected origin while readers are in flight.
      const captured = { receiptBytes: copyBytes(payload.receiptBytes, 32768), digestBytes: copyBytes(payload.digestBytes, 128),
        context: { ...payload.context } };
      const request = { ...admissionRequest, grantBytes: Buffer.from(admissionRequest.grantBytes), context: { ...admissionRequest.context } };
      const checked = verifyMaintenancePayload({ ...captured, admissionRequest: request, originalHeld });
      const c = captured.context, artifactId = identifier(upload.artifactId), archiveDigest = upload.archiveDigest;
      need(typeof archiveDigest === "string" && DIGEST.test(archiveDigest));
      const runId = c.runId, expectedSha = c.executorSha;
      // Only fixed repository and identifiers reach trusted adapters, no URLs,
      // alternate target/repo or arbitrary user payload selectors.
      // readRun reads the current run, never an archived attempt endpoint.
      const run = await readRun(REPO, runId);
      need(identifier(run.id) === runId && run.run_attempt === 1 && run.head_sha === expectedSha
        && run.head_branch === "main" && run.event === "issue_comment" && run.path === WORKFLOW
        && run.repository?.id === REPO_ID && run.repository.full_name === REPO
        && run.head_repository?.id === REPO_ID && run.head_repository.full_name === REPO
        && run.actor?.id === 23397294 && run.triggering_actor?.id === 23397294
        && run.status === "completed" && run.conclusion === (checked.effectSucceeded ? "success" : "failure")
        && c.workflowRef === PROPOSED_WORKFLOW_REF && c.workflowSha === expectedSha && c.clientSha === expectedSha);
      const workflowId = identifier(run.workflow_id), workflow = await readWorkflow(REPO, workflowId);
      need(identifier(workflow.id) === workflowId && workflow.path === WORKFLOW);
      const artifact = await readArtifact(REPO, artifactId);
      need(identifier(artifact.id) === artifactId && artifact.name === MAINTENANCE_RECEIPT_NAME && artifact.expired === false
        && artifact.digest === archiveDigest && Number.isSafeInteger(artifact.size_in_bytes) && artifact.size_in_bytes > 0
        && artifact.size_in_bytes <= 131072 && identifier(artifact.workflow_run?.id) === runId
        && artifact.workflow_run.repository_id === REPO_ID && artifact.workflow_run.head_repository_id === REPO_ID
        && artifact.workflow_run.head_branch === "main" && artifact.workflow_run.head_sha === expectedSha);
      const archive = copyBytes(await downloadArchive(REPO, artifactId), 131072);
      need(archive.length === artifact.size_in_bytes && hash(archive) === archiveDigest);
      const originals = unpackMaintenanceArtifact(archive);
      need(originals.receiptBytes.equals(captured.receiptBytes) && originals.digestBytes.equals(captured.digestBytes));
      // Revalidate actual extracted originals, not metadata or workflow outputs.
      verifyMaintenancePayload({ ...originals, context: c, admissionRequest: request, originalHeld });
      return Object.freeze({ provenanceState: "BOUND", effectState: checked.result, effectSucceeded: checked.effectSucceeded,
        artifactId, archiveDigest, receiptSha256: checked.receiptSha256, retryable: false });
    } catch { return denied("INVALID_OR_INCOMPLETE_ARTIFACT"); }
  } });
}
