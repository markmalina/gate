// Gate-local admission/provenance around the byte-identical pinned Ops reader.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFile, writeFile, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { TARGET, Stop, safeDiagnostic, assertReceipt, verifySourceFiles, transport, observe } from './preflight.mjs';

export const UPSTREAM = 'd1b47bdacf6b3bcf9d2abc46e133eada4e0832c6';
export const ACTION = '.github/actions/gate-release-preflight';
export const WORKFLOW = '.github/workflows/gate-release-preflight.yml';
const SHA = /^[a-f0-9]{40}$/u;
const digest = b => createHash('sha256').update(b).digest('hex');
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const reject = code => { throw new Stop(code, 'local'); };
const keys = (v, expected) => object(v) && Object.keys(v).sort().join(',') === [...expected].sort().join(',');
const FILES = ['action.yml', 'local.mjs', 'preflight.mjs', 'preflight.test.js'];

export function admit(env, event, lock, workflow, files, git, now = Date.now()) {
  const pr = event?.pull_request, head = pr?.head, repo = event?.repository;
  if (env.GITHUB_EVENT_NAME !== 'pull_request' || event?.action !== 'edited' || !object(event?.changes?.body) ||
      env.GITHUB_REPOSITORY !== TARGET.repository || env.GITHUB_REPOSITORY_ID !== String(TARGET.repositoryId) ||
      repo?.id !== TARGET.repositoryId || repo.full_name !== TARGET.repository || repo.owner?.id !== TARGET.ownerId ||
      head?.repo?.id !== TARGET.repositoryId || head.repo.full_name !== TARGET.repository || head.repo.fork !== false ||
      pr?.base?.repo?.id !== TARGET.repositoryId || pr.base.ref !== 'main' || pr.state !== 'open' ||
      !Number.isSafeInteger(pr.number) || pr.number < 1 || !SHA.test(head?.sha ?? '') ||
      pr.body !== `/ops-gate-release-preflight expected-head-sha=${head.sha}` ||
      event.sender?.id !== TARGET.ownerId || event.sender.login !== 'markmalina' ||
      env.GITHUB_ACTOR_ID !== String(TARGET.ownerId) || env.GITHUB_ACTOR !== 'markmalina' ||
      env.GITHUB_TRIGGERING_ACTOR !== 'markmalina' || env.GITHUB_RUN_ATTEMPT !== '1' ||
      env.GITHUB_REF !== `refs/pull/${pr.number}/merge` ||
      env.GITHUB_WORKFLOW_REF !== `${TARGET.repository}/${WORKFLOW}@refs/pull/${pr.number}/merge` ||
      !/^[1-9][0-9]{0,19}$/u.test(env.GITHUB_RUN_ID ?? '') || env.GATE_HELPER_SOURCE_SHA !== head.sha ||
      !SHA.test(env.GITHUB_WORKFLOW_SHA ?? '') || env.GITHUB_WORKFLOW_SHA !== env.GITHUB_SHA ||
      env.GATE_PREFLIGHT_ENVIRONMENT !== 'gate-production') reject('E_CALLER');
  if (!object(lock) || lock.schemaVersion !== 'gate.local-release-preflight-lock/v1' ||
      lock.repository !== TARGET.repository || lock.actionPath !== ACTION || lock.upstreamRepository !== 'markmalina/ops' ||
      lock.upstreamCommit !== UPSTREAM || lock.sourceBase !== lock.expectedCurrentMain ||
      !SHA.test(lock.expectedCurrentMain ?? '') || !SHA.test(lock.expectedProductionSource ?? '') ||
      lock.workflowSha256 !== `sha256:${digest(workflow)}` ||
      !keys(lock.executorFilesSha256, FILES) || FILES.some(p => !Buffer.isBuffer(files[p]) ||
        lock.executorFilesSha256[p] !== `sha256:${digest(files[p])}`) ||
      lock.upstreamSourceSha256 !== 'sha256:c39ed6016bd88b123464a9be488686387531177055adf313da6f75e0253a0d08' ||
      lock.executorFilesSha256['preflight.mjs'] !== lock.upstreamSourceSha256 ||
      lock.executorFilesSha256['preflight.test.js'] !== 'sha256:7277e0c684ba27546740c0ebe307c872e9481517dade1a8918365cf3de3fc098' ||
      typeof lock.reviewedStartCommand !== 'string' || lock.reviewedStartCommand.length > 4096 ||
      !workflow.toString('utf8').includes(`uses: ./${ACTION}\n`) ||
      /uses:\s*markmalina\/ops\//u.test(workflow.toString('utf8'))) reject('E_LOCK');
  const start = Date.parse(lock.authorizationStartsAt), end = Date.parse(lock.authorizationExpiresAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || end - start > 45 * 60_000 ||
      now < start || now >= end) reject('E_EXPIRED');
  if (pr.base.sha !== lock.expectedCurrentMain || git?.head !== head.sha ||
      !SHA.test(git.tree ?? '') || !Array.isArray(git.parents) || git.parents.length !== 1 ||
      git.parents[0] !== lock.sourceBase || git.workflowSha !== env.GITHUB_WORKFLOW_SHA ||
      git.workflowTree !== git.tree || !Array.isArray(git.workflowParents) || git.workflowParents.length !== 2 ||
      git.workflowParents[0] !== lock.sourceBase || git.workflowParents[1] !== head.sha) reject('E_SOURCE');
  return { executorRepository: TARGET.repository, executorPath: `${ACTION}/local.mjs`, executorSha: head.sha,
    executorTree: git.tree, workflowSha: git.workflowSha, upstreamRepository: 'markmalina/ops',
    upstreamSha: UPSTREAM, upstreamSourceSha256: lock.upstreamSourceSha256, runId: env.GITHUB_RUN_ID };
}

function commit(raw) {
  const headers = raw.split('\n\n', 1)[0].split('\n');
  return { tree: headers.find(l => l.startsWith('tree '))?.slice(5),
    parents: headers.filter(l => l.startsWith('parent ')).map(l => l.slice(7)) };
}
export async function loadAdmission(env = process.env) {
  const workspace = resolve(env.GITHUB_WORKSPACE ?? process.cwd());
  const event = JSON.parse(await readFile(env.GITHUB_EVENT_PATH, 'utf8'));
  const lock = JSON.parse(await readFile(resolve(workspace, 'agent-ops.gate-release-preflight.lock.json'), 'utf8'));
  const workflow = await readFile(resolve(workspace, WORKFLOW));
  const files = Object.fromEntries(await Promise.all(FILES.map(async p => [p, await readFile(resolve(workspace, ACTION, p))])));
  // Fixed local Git object inspection only. Workflow fetch is a separate uncredentialed Gate step.
  if (!SHA.test(env.GITHUB_WORKFLOW_SHA ?? '')) reject('E_SOURCE');
  const git = (...args) => execFileSync('git', args, { cwd: workspace, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 5000, maxBuffer: 16384 }).trim();
  const head = git('rev-parse', 'HEAD'), h = commit(git('cat-file', '-p', 'HEAD'));
  const w = commit(git('cat-file', '-p', env.GITHUB_WORKFLOW_SHA));
  const provenance = admit(env, event, lock, workflow, files, { head, ...h, workflowSha: env.GITHUB_WORKFLOW_SHA,
    workflowTree: w.tree, workflowParents: w.parents });
  verifySourceFiles(lock, await readFile(resolve(workspace, 'railway.toml')), await readFile(resolve(workspace, 'Dockerfile')));
  if (lock.reviewedConfiguration?.proposedSourceSha !== lock.expectedCurrentMain) reject('E_SOURCE_CONFIG');
  try { await readFile(resolve(workspace, 'railway.json')); reject('E_SOURCE_CONFIG'); }
  catch (error) { if (error?.code !== 'ENOENT') throw error; }
  return { lock, provenance };
}

export function validateEnvelope(envelope, expected) {
  if (!keys(envelope, ['schemaVersion', 'provenance', 'observation']) ||
      envelope.schemaVersion !== 'gate.local-release-preflight/v1') reject('E_RECEIPT');
  const r = assertReceipt(envelope.observation), p = envelope.provenance;
  if (p === null && r.result === 'STOP') return envelope;
  if (!keys(p, ['executorRepository','executorPath','executorSha','executorTree','workflowSha',
    'upstreamRepository','upstreamSha','upstreamSourceSha256','runId']) ||
      p.executorRepository !== TARGET.repository || p.executorPath !== `${ACTION}/local.mjs` ||
      ![p.executorSha,p.executorTree,p.workflowSha].every(x => SHA.test(x ?? '')) ||
      p.upstreamRepository !== 'markmalina/ops' || p.upstreamSha !== UPSTREAM ||
      p.upstreamSourceSha256 !== 'sha256:c39ed6016bd88b123464a9be488686387531177055adf313da6f75e0253a0d08' ||
      !/^[1-9][0-9]{0,19}$/u.test(p.runId ?? '') ||
      (expected && JSON.stringify(p) !== JSON.stringify(expected)) ||
      (r.result === 'OBSERVED' && (r.helperSourceSha !== p.executorSha || r.executorSha !== p.executorSha ||
        r.runId !== p.runId))) reject('E_RECEIPT');
  return envelope;
}
export function verifyBytes(bytes, digestBytes, expected, expectedMain) {
  if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.length > 65536 || !Buffer.isBuffer(digestBytes) ||
      !digestBytes.equals(Buffer.from(`sha256:${digest(bytes)}\n`))) reject('E_RECEIPT');
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { reject('E_RECEIPT'); }
  if (!bytes.equals(Buffer.from(`${JSON.stringify(value)}\n`))) reject('E_RECEIPT');
  validateEnvelope(value, expected);
  if (value.observation.result !== 'OBSERVED' || value.observation.currentMainSha !== expectedMain) reject('E_RECEIPT');
  return value;
}
async function artifact(name, max) {
  const fd = await open(name, constants.O_RDONLY | constants.O_NOFOLLOW);
  try { const s = await fd.stat(); if (!s.isFile() || s.size < 1 || s.size > max) reject('E_RECEIPT');
    const bytes = await fd.readFile(); if (bytes.length !== s.size) reject('E_RECEIPT'); return bytes;
  } finally { await fd.close(); }
}
export async function run(env = process.env, fetchFn = fetch, admission = loadAdmission) {
  let provenance = null;
  let observation = { schemaVersion: 'agent-ops.gate-release-preflight/v1', operation: 'READ_ONLY_GATE_RELEASE_PREFLIGHT',
    result: 'STOP', privateContentExposed: false };
  try {
    const a = await admission(env); provenance = a.provenance;
    const state = await observe({ io: transport({ railwayToken: env.RAILWAY_PROJECT_TOKEN, githubToken: env.GITHUB_READ_TOKEN,
      deadline: Date.parse(a.lock.authorizationExpiresAt), fetchFn }), lock: a.lock, sourceFilesVerified: true });
    observation = { ...observation, result: 'OBSERVED', observedAt: new Date().toISOString(),
      helperSourceSha: provenance.executorSha, runId: provenance.runId, executorSha: provenance.executorSha,
      runAttempt: 1, currentMainSha: a.lock.expectedCurrentMain, target: TARGET, ...state };
  } catch (error) { observation.diagnostic = safeDiagnostic(error); process.exitCode = 1; }
  const envelope = { schemaVersion: 'gate.local-release-preflight/v1', provenance, observation };
  validateEnvelope(envelope);
  const bytes = Buffer.from(`${JSON.stringify(envelope)}\n`);
  await writeFile('gate-release-preflight.json', bytes, { mode: 0o600, flag: 'wx' });
  await writeFile('gate-release-preflight.sha256', `sha256:${digest(bytes)}\n`, { mode: 0o600, flag: 'wx' });
  process.stdout.write(`${observation.result} sha256:${digest(bytes)}\n`);
  return envelope;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv.length === 3 && process.argv[2] === '--check-caller') {
      await loadAdmission(); process.stdout.write('GATE_PREFLIGHT_SOURCE_ACCEPTED\n');
    } else if (process.argv.length === 3 && process.argv[2] === '--verify-receipt') {
      const a = await loadAdmission();
      verifyBytes(await artifact('gate-release-preflight.json', 65536), await artifact('gate-release-preflight.sha256', 80),
        a.provenance, a.lock.expectedCurrentMain);
      process.stdout.write('GATE_PREFLIGHT_OBSERVATION_VERIFIED\n');
    } else if (process.argv.length === 2) await run();
    else reject('E_CALLER');
  } catch { process.stderr.write('GATE_PREFLIGHT_REJECTED\n'); process.exitCode = 1; }
}
