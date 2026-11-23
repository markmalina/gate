// One immutable nonsecret source artifact carries the complete canonical O
// commit/tree/blob object graph. No .git config, App token or environment data.
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {mkdirSync, writeFileSync, existsSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {deny, exact, SHA, DIGEST, ID, sha256, authenticateArtifact, makeOriginalGithub} from './fixture-artifacts.mjs';
import {readSourceLock} from './fixture-controller.mjs';

export const SOURCE_LIMITS = Object.freeze({objects: 2048, objectBytes: 2097152, payloadBytes: 8388608,
  jsonBytes: 12582912, archiveBytes: 16777216});
const gitEnvironment = () => ({PATH: process.env.PATH, LANG: 'C', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
  GIT_TERMINAL_PROMPT: '0', GIT_CONFIG_COUNT: '0'});
function git(dir, args, input) {
  try {return execFileSync('git', ['-C', dir, ...args], {input, env: gitEnvironment(), maxBuffer: SOURCE_LIMITS.jsonBytes});}
  catch {deny('E_CONTROLLER_OPS_GIT');}
}
const objectSha = (type, bytes) => createHash('sha1').update(type + ' ' + bytes.length + '\0').update(bytes).digest('hex');
function decode(value) {
  if (typeof value !== 'string' || value.length > Math.ceil(SOURCE_LIMITS.objectBytes / 3) * 4 ||
      value.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/u.test(value)) deny('E_CONTROLLER_OPS_OBJECT');
  const bytes = Buffer.from(value, 'base64');
  if (bytes.toString('base64') !== value || bytes.length > SOURCE_LIMITS.objectBytes) deny('E_CONTROLLER_OPS_OBJECT');
  return bytes;
}
function commitFacts(bytes) {
  const boundary = bytes.indexOf('\n\n');
  if (boundary < 0 || bytes.length > 65536 || bytes.includes(0)) deny('E_CONTROLLER_OPS_COMMIT');
  const headers = bytes.subarray(0, boundary).toString('utf8').split('\n'), tree = headers[0]?.match(/^tree ([0-9a-f]{40})$/u)?.[1];
  if (!tree) deny('E_CONTROLLER_OPS_COMMIT');
  const parents = []; let endedParents = false;
  for (const h of headers.slice(1)) {
    if (h.startsWith('parent ')) {
      if (endedParents || !/^parent [0-9a-f]{40}$/u.test(h) || parents.includes(h.slice(7))) deny('E_CONTROLLER_OPS_COMMIT');
      parents.push(h.slice(7));
    } else endedParents = true;
  }
  if (headers.filter(x => x.startsWith('tree ')).length !== 1 || headers.filter(x => x.startsWith('author ')).length !== 1 ||
      headers.filter(x => x.startsWith('committer ')).length !== 1 || parents.length > 8) deny('E_CONTROLLER_OPS_COMMIT');
  return {tree, parents};
}
function safeName(bytes) {
  const name = bytes.toString('utf8');
  if (!bytes.equals(Buffer.from(name)) || name.length < 1 || name.length > 240 || /[\u0000-\u001f\u007f/\\]/u.test(name) ||
      ['.', '..', '.git', '.gitmodules', '.githooks'].includes(name.toLowerCase()) || name.toLowerCase() === 'hooks') deny('E_CONTROLLER_OPS_PATH');
  return name;
}
export function validateOpsSourceBundle(bundle, pin) {
  exact(bundle, ['schemaVersion', 'repository', 'commitSha', 'treeSha', 'commitRawBase64', 'objects']);
  if (bundle.schemaVersion !== 'gate745.fixture-ops-source-bundle/v2' || bundle.repository !== 'AlphaCenter/ops' ||
      !SHA.test(pin.sha) || !SHA.test(pin.tree) || bundle.commitSha !== pin.sha || bundle.treeSha !== pin.tree ||
      !Array.isArray(bundle.objects) || bundle.objects.length < 1 || bundle.objects.length > SOURCE_LIMITS.objects) deny('E_CONTROLLER_OPS_SOURCE');
  const commit = decode(bundle.commitRawBase64), facts = commitFacts(commit);
  // The exact SHA authenticates every original header, including ordered real
  // parents. Parents are not rewritten and need not be materialized in this
  // credentialless shallow source checkout.
  if (objectSha('commit', commit) !== pin.sha || facts.tree !== pin.tree) deny('E_CONTROLLER_OPS_COMMIT');
  const objects = new Map(); let total = commit.length, previous = '';
  for (const o of bundle.objects) {
    exact(o, ['sha', 'type', 'size', 'rawBase64']);
    if (!SHA.test(o.sha) || o.sha <= previous || !['tree', 'blob'].includes(o.type) || !Number.isSafeInteger(o.size)) deny('E_CONTROLLER_OPS_OBJECT');
    previous = o.sha; const bytes = decode(o.rawBase64); total += bytes.length;
    if (bytes.length !== o.size || total > SOURCE_LIMITS.payloadBytes || objectSha(o.type, bytes) !== o.sha) deny('E_CONTROLLER_OPS_OBJECT');
    objects.set(o.sha, {type: o.type, bytes});
  }
  const reached = new Set(), paths = new Set(), files = new Map();
  function walk(sha, prefix, stack) {
    const object = objects.get(sha);
    if (object?.type !== 'tree' || stack.has(sha) || prefix.length > 4096 || stack.size > 40) deny('E_CONTROLLER_OPS_TREE');
    reached.add(sha); const next = new Set(stack); next.add(sha);
    const names = new Set(), bytes = object.bytes; let i = 0;
    while (i < bytes.length) {
      const space = bytes.indexOf(32, i), zero = bytes.indexOf(0, space + 1);
      if (space < i || zero < space || zero + 21 > bytes.length) deny('E_CONTROLLER_OPS_TREE');
      const mode = bytes.subarray(i, space).toString('ascii'), name = safeName(bytes.subarray(space + 1, zero));
      const child = bytes.subarray(zero + 1, zero + 21).toString('hex'), path = prefix + name;
      if (!['40000', '100644', '100755'].includes(mode) || names.has(name) || paths.has(path)) deny('E_CONTROLLER_OPS_MODE');
      names.add(name); paths.add(path); i = zero + 21;
      if (mode === '40000') walk(child, path + '/', next);
      else {
        const blob = objects.get(child); if (blob?.type !== 'blob') deny('E_CONTROLLER_OPS_TREE');
        reached.add(child); files.set(path, {sha: child, mode});
      }
    }
  }
  walk(pin.tree, '', new Set());
  if (reached.size !== objects.size || !files.has('agent-ops/actions/gate745-fixture-controller/entry.mjs')) deny('E_CONTROLLER_OPS_EXTRA_OBJECT');
  return Object.freeze({commit, objects, files, sha: pin.sha, tree: pin.tree, parents: Object.freeze(facts.parents)});
}
export function createOpsSourceBundle() {
  const lock = readSourceLock(), dir = '.agent-ops/executor';
  if (!lock.ops.commit) deny('E_CONTROLLER_OPS_PIN_MISSING');
  const sha = git(dir, ['rev-parse', 'HEAD']).toString().trim(), tree = git(dir, ['rev-parse', 'HEAD^{tree}']).toString().trim();
  if (sha !== lock.ops.commit || tree !== lock.ops.tree) deny('E_CONTROLLER_OPS_SOURCE');
  const list = git(dir, ['ls-tree', '-r', '-t', '--full-tree', 'HEAD']).toString('utf8').trim().split('\n');
  const types = new Map([[tree, 'tree']]);
  for (const row of list) {
    const m = row.match(/^(040000|100644|100755) (tree|blob) ([0-9a-f]{40})\t[^\n]+$/u);
    if (!m) deny('E_CONTROLLER_OPS_MODE'); types.set(m[3], m[2]);
  }
  const bundle = {schemaVersion: 'gate745.fixture-ops-source-bundle/v2', repository: 'AlphaCenter/ops', commitSha: sha, treeSha: tree,
    commitRawBase64: git(dir, ['cat-file', 'commit', sha]).toString('base64'),
    objects: [...types].sort(([a], [b]) => a.localeCompare(b)).map(([id, type]) => {
      const bytes = git(dir, ['cat-file', type, id]); return {sha: id, type, size: bytes.length, rawBase64: bytes.toString('base64')};
    })};
  validateOpsSourceBundle(bundle, {sha, tree});
  const raw = Buffer.from(JSON.stringify(bundle)); if (raw.length > SOURCE_LIMITS.jsonBytes) deny('E_CONTROLLER_OPS_SOURCE_SIZE');
  return raw;
}
export function installOpsSourceBundle(bundle, pin) {
  const checked = validateOpsSourceBundle(bundle, pin), destination = '.agent-ops/executor';
  if (existsSync(destination)) deny('E_CONTROLLER_OPS_DESTINATION');
  mkdirSync(destination, {recursive: true, mode: 0o700}); git(destination, ['init', '--quiet']);
  git(destination, ['config', '--local', 'core.hooksPath', '/dev/null']);
  for (const [sha, object] of checked.objects) {
    if (git(destination, ['hash-object', '-w', '--stdin', '-t', object.type], object.bytes).toString().trim() !== sha) deny('E_CONTROLLER_OPS_OBJECT');
  }
  if (git(destination, ['hash-object', '-w', '--stdin', '-t', 'commit'], checked.commit).toString().trim() !== pin.sha) deny('E_CONTROLLER_OPS_COMMIT');
  writeFileSync(destination + '/.git/shallow', pin.sha + '\n', {mode: 0o600});
  git(destination, ['update-ref', 'refs/heads/gate745-source', pin.sha]);
  git(destination, ['checkout', '--quiet', '--detach', pin.sha]);
  if (git(destination, ['rev-parse', 'HEAD']).toString().trim() !== pin.sha ||
      git(destination, ['rev-parse', 'HEAD^{tree}']).toString().trim() !== pin.tree) deny('E_CONTROLLER_OPS_SOURCE');
  return checked;
}
export async function installOriginalOpsSource() {
  const lock = readSourceLock(); if (!lock.ops.commit) deny('E_CONTROLLER_OPS_PIN_MISSING');
  const e = process.env, id = e.INPUT_OPS_SOURCE_ARTIFACT_ID, digest = e.INPUT_OPS_SOURCE_RAW_DIGEST;
  if (!ID.test(id ?? '') || !DIGEST.test(digest ?? '')) deny('E_CONTROLLER_DESCRIPTOR');
  const context = {runId: e.GITHUB_RUN_ID, clientSha: e.GITHUB_SHA, repositoryId: Number(e.GITHUB_REPOSITORY_ID)};
  const loader = makeOriginalGithub({token: e.GATE745_ORIGINAL_READ_TOKEN, context});
  await loader.originalRun(); const metadata = await loader.artifact(id), list = await loader.artifactList(), jobs = await loader.jobs();
  const producers = jobs.filter(x => ['fixture-source-admission', 'fixture / fixture-source-admission'].includes(x.name));
  if (producers.length !== 1 || producers[0].run_id !== Number(context.runId) || producers[0].status !== 'completed' || producers[0].conclusion !== 'success' ||
      producers[0].steps?.filter(x => x.name === 'Upload accepted Ops source' && x.status === 'completed' && x.conclusion === 'success').length !== 1 ||
      list.filter(x => x.name === 'gate745-fixture-ops-source' && String(x.id) === id).length !== 1 ||
      list.filter(x => x.name === 'gate745-fixture-ops-source').length !== 1) deny('E_CONTROLLER_OPS_PRODUCER');
  const artifact = authenticateArtifact({metadata, archiveBytes: await loader.opsSourceArchive(id), name: 'gate745-fixture-ops-source',
    filename: 'fixture-ops-source.json', rawDigest: digest, context});
  return installOpsSourceBundle(artifact.value, {sha: lock.ops.commit, tree: lock.ops.tree});
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv[2] === 'produce') {
      const raw = createOpsSourceBundle(); mkdirSync('.agent-ops/output', {recursive: true, mode: 0o700});
      writeFileSync('.agent-ops/output/fixture-ops-source.json', raw, {mode: 0o600});
      const {appendFileSync} = await import('node:fs'); appendFileSync(process.env.GITHUB_OUTPUT, 'ops-source-raw-digest=' + sha256(raw) + '\n');
    } else if (process.argv[2] === 'install') await installOriginalOpsSource();
    else deny('E_CONTROLLER_MODE');
  } catch (error) {
    process.stderr.write((/^E_[A-Z0-9_]+$/u.test(error.code ?? '') ? error.code : 'E_CONTROLLER_INTERNAL') + '\n'); process.exitCode = 1;
  }
}
