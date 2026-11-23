// The byte loader has no credential, environment, provider or caller-URL access.
import {createHash} from 'node:crypto';
import {inflateRawSync} from 'node:zlib';

export class ControllerDenied extends Error {
  constructor(code) { super(code); this.name = 'ControllerDenied'; this.code = code; }
}
export const deny = code => { throw new ControllerDenied(code); };
export const sha256 = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');
export const SHA = /^[0-9a-f]{40}$/u;
export const DIGEST = /^sha256:[0-9a-f]{64}$/u;
export const ID = /^[1-9][0-9]{0,19}$/u;
export function canonical(value) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number' && Number.isSafeInteger(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && Object.getPrototypeOf(value) === Object.prototype) {
    const keys = Object.keys(value).sort();
    return '{' + keys.map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  }
  deny('E_CONTROLLER_CANONICAL');
}

export function exact(value, keys, code = 'E_CONTROLLER_SHAPE') {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) deny(code);
  const own = Reflect.ownKeys(value);
  if (own.length !== keys.length || own.some(k => typeof k !== 'string' || !keys.includes(k))) deny(code);
  for (const key of own) {
    const d = Object.getOwnPropertyDescriptor(value, key);
    if (!d || !Object.hasOwn(d, 'value') || d.enumerable !== true) deny(code);
  }
  return value;
}

export function parseObject(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length > 65536) deny('E_CONTROLLER_ARTIFACT_SIZE');
  let object;
  try { object = JSON.parse(bytes.toString('utf8')); } catch { deny('E_CONTROLLER_JSON'); }
  if (!object || Object.getPrototypeOf(object) !== Object.prototype) deny('E_CONTROLLER_JSON');
  return object;
}

export async function readBounded(response, maximum = 1048576) {
  if (!response.body) deny('E_CONTROLLER_GITHUB_SHAPE');
  const reader = response.body.getReader(), chunks = []; let length = 0;
  while (true) {
    const {done, value} = await reader.read(); if (done) break;
    length += value.byteLength;
    if (length > maximum) {await reader.cancel(); deny('E_CONTROLLER_GITHUB_SHAPE');}
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks);
}

export function assertOriginalRun(run, context, {now = Date.now()} = {}) {
  if (!run || run.id?.toString() !== context.runId || run.run_attempt !== 1 ||
      run.repository?.full_name !== 'AlphaCenter/gate' || run.repository?.id !== 1293746267 || run.repository?.id !== context.repositoryId ||
      run.actor?.id !== 23397294 || run.actor?.login !== 'markmalina' ||
      run.triggering_actor?.id !== 23397294 || run.head_sha !== context.clientSha ||
      run.event !== 'issue_comment' || run.head_branch !== 'main' ||
      run.path !== '.github/workflows/gate745-native-fixture.yml' ||
      run.status !== 'in_progress' || run.conclusion !== null ||
      !Number.isFinite(Date.parse(run.created_at)) || Date.parse(run.created_at) > now ||
      now - Date.parse(run.created_at) >= 120 * 60000) deny('E_CONTROLLER_ORIGINAL_RUN');
  return true;
}

// The fixed authenticated GitHub archive endpoint supplies actual ZIP bytes
// from an exact original API artifact id. API archive digest is authenticated
// before ZIP parsing. The detached raw JSON digest is independent of it.
export function authenticateArtifact({metadata, archiveBytes, name, filename, rawDigest, context}) {
  const sourceBundle = name === 'gate745-fixture-ops-source' && filename === 'fixture-ops-source.json';
  const archiveMaximum = sourceBundle ? 16777216 : 262144, rawMaximum = sourceBundle ? 12582912 : 65536;
  if (!ID.test(String(metadata?.id ?? '')) || metadata.name !== name || metadata.expired !== false ||
      metadata.workflow_run?.id?.toString() !== context.runId ||
      metadata.workflow_run?.head_sha !== context.clientSha || !DIGEST.test(metadata.digest ?? '') ||
      !Buffer.isBuffer(archiveBytes) || archiveBytes.length < 4 || archiveBytes.length > archiveMaximum ||
      !Number.isSafeInteger(metadata.size_in_bytes) || metadata.size_in_bytes <= 0 || metadata.size_in_bytes > archiveMaximum ||
      sha256(archiveBytes) !== metadata.digest || !DIGEST.test(rawDigest)) deny('E_CONTROLLER_ARTIFACT_ORIGIN');
  if (archiveBytes.readUInt32LE(0) !== 0x04034b50) deny('E_CONTROLLER_ZIP');
  const raw = singleFileZip(archiveBytes, filename, rawMaximum);
  if (sha256(raw) !== rawDigest) deny('E_CONTROLLER_ARTIFACT_DIGEST');
  return Object.freeze({id: String(metadata.id), name, rawDigest, archiveDigest: metadata.digest,
    value: sourceBundle ? JSON.parse(raw.toString('utf8')) : parseObject(raw), raw});
}

// Only one small, non-encrypted, regular JSON file is admitted. No filesystem
// extraction, directory traversal, symlinks or ZIP64. Ordinary UTF8 flags,
// timestamp extras and a bounded data descriptor are checked against central
// metadata and CRC, as emitted by GitHub's normal ZIP uploader.
function singleFileZip(bytes, filename, maximum = 65536) {
  if (bytes.length < 98) deny('E_CONTROLLER_ZIP');
  const end = bytes.length - 22;
  if (bytes.readUInt32LE(end) !== 0x06054b50 || bytes.readUInt16LE(end + 4) !== 0 ||
      bytes.readUInt16LE(end + 6) !== 0 || bytes.readUInt16LE(end + 8) !== 1 ||
      bytes.readUInt16LE(end + 10) !== 1 || bytes.readUInt16LE(end + 20) !== 0) deny('E_CONTROLLER_ZIP');
  const cSize = bytes.readUInt32LE(end + 12), c = bytes.readUInt32LE(end + 16);
  if (c + cSize !== end || c < 30 || cSize < 46 || bytes.readUInt32LE(c) !== 0x02014b50) deny('E_CONTROLLER_ZIP');
  const flags = bytes.readUInt16LE(6), method = bytes.readUInt16LE(8);
  const size = bytes.readUInt32LE(c + 20), outSize = bytes.readUInt32LE(c + 24);
  const n = bytes.readUInt16LE(26), extra = bytes.readUInt16LE(28);
  const cn = bytes.readUInt16LE(c + 28), ce = bytes.readUInt16LE(c + 30), cc = bytes.readUInt16LE(c + 32);
  const mode = bytes.readUInt32LE(c + 38) >>> 16;
  const descriptor = Boolean(flags & 8), data = 30 + n + extra;
  if (![0, 8, 2048, 2056].includes(flags) || ![0, 8].includes(method) || extra > 1024 || ce > 1024 || cc !== 0 || n !== cn ||
      cSize !== 46 + cn + ce || bytes.readUInt32LE(c + 42) !== 0 ||
      bytes.readUInt16LE(c + 8) !== flags || bytes.readUInt16LE(c + 10) !== method ||
      (!descriptor && (bytes.readUInt32LE(18) !== size || bytes.readUInt32LE(22) !== outSize ||
        bytes.readUInt32LE(c + 16) !== bytes.readUInt32LE(14))) ||
      (descriptor && [bytes.readUInt32LE(14), bytes.readUInt32LE(18), bytes.readUInt32LE(22)].some(x => x !== 0)) ||
      outSize > maximum || data + size > c ||
      (mode !== 0 && (mode & 0o170000) !== 0o100000) ||
      bytes.subarray(30, 30 + n).toString('utf8') !== filename ||
      bytes.subarray(c + 46, c + 46 + cn).toString('utf8') !== filename) deny('E_CONTROLLER_ZIP');
  if (descriptor) {
    let d = data + size;
    if (c - d === 16 && bytes.readUInt32LE(d) === 0x08074b50) d += 4;
    if (c - d !== 12 || bytes.readUInt32LE(d) !== bytes.readUInt32LE(c + 16) ||
        bytes.readUInt32LE(d + 4) !== size || bytes.readUInt32LE(d + 8) !== outSize) deny('E_CONTROLLER_ZIP');
  } else if (data + size !== c) deny('E_CONTROLLER_ZIP');
  for (const [offset, length] of [[30 + n, extra], [c + 46 + cn, ce]]) {
    let x = offset;
    while (x < offset + length) {
      if (x + 4 > offset + length || bytes.readUInt16LE(x) === 1) deny('E_CONTROLLER_ZIP');
      x += 4 + bytes.readUInt16LE(x + 2);
      if (x > offset + length) deny('E_CONTROLLER_ZIP');
    }
  }
  let out;
  try { out = method === 0 ? bytes.subarray(data, data + size) : inflateRawSync(bytes.subarray(data, data + size), {maxOutputLength: maximum}); }
  catch { deny('E_CONTROLLER_ZIP'); }
  let crc = 0xffffffff;
  for (const byte of out) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  if (out.length !== outSize || ((crc ^ 0xffffffff) >>> 0) !== bytes.readUInt32LE(c + 16)) deny('E_CONTROLLER_ZIP');
  return out;
}

export function makeOriginalGithub({token, context, fetchFn = fetch, now = Date.now}) {
  if (typeof token !== 'string' || token.length < 20 || /\s/u.test(token) ||
      !ID.test(context.runId) || !SHA.test(context.clientSha) || !Number.isSafeInteger(context.repositoryId)) deny('E_CONTROLLER_GITHUB_AUTH');
  const reads = new Set(), artifactIds = new Set(), archiveIds = new Set(), commentIds = new Set();
  let sourceArchiveUsed = false;
  async function get(path) {
    if (reads.has(path)) deny('E_CONTROLLER_DUPLICATE_READ');
    reads.add(path);
    const response = await fetchFn('https://api.github.com' + path, {method: 'GET', redirect: 'error',
      headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json'}, signal: AbortSignal.timeout(15000)});
    if (!response.ok) deny('E_CONTROLLER_GITHUB');
    try { return JSON.parse((await readBounded(response)).toString('utf8')); } catch { deny('E_CONTROLLER_GITHUB_SHAPE'); }
  }
  async function archive(id, maximum) {
      if (!ID.test(id)) deny('E_CONTROLLER_ARTIFACT_ID');
      if (archiveIds.size >= 7) deny('E_CONTROLLER_GITHUB_READ_CAP'); archiveIds.add(id);
      const path = `/repos/AlphaCenter/gate/actions/artifacts/${id}/zip`;
      if (reads.has(path)) deny('E_CONTROLLER_DUPLICATE_READ');
      reads.add(path);
      const redirect = await fetchFn('https://api.github.com' + path, {method: 'GET', redirect: 'manual',
        headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json'}, signal: AbortSignal.timeout(15000)});
      if (![302, 307].includes(redirect.status)) deny('E_CONTROLLER_ARCHIVE_REDIRECT');
      let url; try {url = new URL(redirect.headers.get('location'));} catch {deny('E_CONTROLLER_ARCHIVE_REDIRECT');}
      if (url.protocol !== 'https:' || url.username || url.password || url.hash || url.port || url.href.length > 32768 ||
          !(/^[a-z0-9-]+\.blob\.core\.windows\.net$/u.test(url.hostname) ||
            /^[a-z0-9.-]+\.s3\.amazonaws\.com$/u.test(url.hostname) ||
            /^[a-z0-9.-]+\.githubusercontent\.com$/u.test(url.hostname))) deny('E_CONTROLLER_ARCHIVE_REDIRECT');
      // The signed storage URL comes only from the authenticated fixed GitHub
      // API. Never forward its Authorization header or export its query string.
      const response = await fetchFn(url.href, {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(15000)});
      if (!response.ok || !response.body) deny('E_CONTROLLER_ARCHIVE');
      return await readBounded(response, maximum);
  }
  return Object.freeze({
    async archive(id) {return await archive(id, 262144);},
    async opsSourceArchive(id) {
      if (sourceArchiveUsed) deny('E_CONTROLLER_GITHUB_READ_CAP'); sourceArchiveUsed = true;
      return await archive(id, 16777216);
    },
    async originalRun() {
      const run = await get(`/repos/AlphaCenter/gate/actions/runs/${context.runId}`);
      assertOriginalRun(run, context, {now: now()}); return run;
    },
    async artifact(id) {
      if (!ID.test(id)) deny('E_CONTROLLER_ARTIFACT_ID');
      if (artifactIds.size >= 7) deny('E_CONTROLLER_GITHUB_READ_CAP'); artifactIds.add(id);
      return await get(`/repos/AlphaCenter/gate/actions/artifacts/${id}`);
    },
    async jobs() {
      const jobs = await get(`/repos/AlphaCenter/gate/actions/runs/${context.runId}/jobs?per_page=100`);
      if (!Number.isSafeInteger(jobs.total_count) || jobs.total_count > 100 || !Array.isArray(jobs.jobs) ||
          jobs.jobs.length !== jobs.total_count) deny('E_CONTROLLER_JOB_PAGE');
      return jobs.jobs;
    },
    async artifactList() {
      const list = await get(`/repos/AlphaCenter/gate/actions/runs/${context.runId}/artifacts?per_page=100`);
      if (!Number.isSafeInteger(list.total_count) || list.total_count > 100 || !Array.isArray(list.artifacts) ||
          list.artifacts.length !== list.total_count) deny('E_CONTROLLER_ARTIFACT_PAGE');
      return list.artifacts;
    },
    async ownerComment(id) {
      if (!ID.test(id)) deny('E_CONTROLLER_OWNER_RECORD');
      if (commentIds.size >= 3) deny('E_CONTROLLER_GITHUB_READ_CAP'); commentIds.add(id);
      return await get(`/repos/AlphaCenter/gate/issues/comments/${id}`);
    },
    counts() {return Object.freeze({apiGets: reads.size, artifactMetadata: artifactIds.size,
      archiveEndpoints: archiveIds.size, comments: commentIds.size});},
  });
}
