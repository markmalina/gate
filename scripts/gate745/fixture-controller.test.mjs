import assert from 'node:assert/strict';
import {test} from 'node:test';
import {generateKeyPairSync, sign} from 'node:crypto';
import {deflateRawSync} from 'node:zlib';
import {mkdtempSync, cpSync, readFileSync, writeFileSync, rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {sha256, canonical, authenticateArtifact, assertOriginalRun, makeOriginalGithub} from './fixture-artifacts.mjs';
import {verifyControllerOidc, operationAudience} from './fixture-oidc.mjs';
import {validateRunBinding, makeOperationLedger, defaultEntry, assertAdmittedBinding, readSourceLock} from './fixture-controller.mjs';
import {reservationScope, sourceScopeDigest, assertNoPriorReservation, reserveOriginalGrant} from './fixture-reservation.mjs';

const now = Date.parse('2026-10-11T17:00:00.000Z');
const hex = (char, length = 40) => char.repeat(length);
const D = 'sha256:' + hex('0', 64);
const context = {runId: '101', clientSha: hex('a'), repositoryId: 1293746267};
const json = value => Buffer.from(JSON.stringify(value));
const denial = code => error => error.code === code;

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);}
  return (crc ^ 0xffffffff) >>> 0;
}
function zip(raw, filename = 'fixture-binding.json', {descriptor = false, compressed = true} = {}) {
  const name = Buffer.from(filename), data = compressed ? deflateRawSync(raw) : raw;
  const flags = descriptor ? 2056 : 0, method = compressed ? 8 : 0;
  const local = Buffer.alloc(30 + name.length); local.writeUInt32LE(0x04034b50);
  local.writeUInt16LE(20, 4); local.writeUInt16LE(flags, 6); local.writeUInt16LE(method, 8);
  local.writeUInt16LE(name.length, 26); name.copy(local, 30);
  if (!descriptor) {local.writeUInt32LE(crc32(raw), 14); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(raw.length, 22);}
  const d = Buffer.alloc(descriptor ? 16 : 0);
  if (descriptor) {d.writeUInt32LE(0x08074b50); d.writeUInt32LE(crc32(raw), 4); d.writeUInt32LE(data.length, 8); d.writeUInt32LE(raw.length, 12);}
  const central = Buffer.alloc(46 + name.length); central.writeUInt32LE(0x02014b50);
  central.writeUInt16LE(20, 6); central.writeUInt16LE(flags, 8); central.writeUInt16LE(method, 10);
  central.writeUInt32LE(crc32(raw), 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(raw.length, 24);
  central.writeUInt16LE(name.length, 28); central.writeUInt32LE((0o100644 << 16) >>> 0, 38); name.copy(central, 46);
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10);
  end.writeUInt32LE(central.length, 12); end.writeUInt32LE(local.length + data.length + d.length, 16);
  return Buffer.concat([local, data, d, central, end]);
}
function metadata(id, name, archive) {
  return {id: Number(id), name, expired: false, digest: sha256(archive), size_in_bytes: archive.length,
    workflow_run: {id: 101, head_sha: context.clientSha}};
}
function originalRun() {
  return {id: 101, run_attempt: 1, repository: {full_name: 'AlphaCenter/gate', id: 1293746267},
    actor: {id: 23397294, login: 'markmalina'}, triggering_actor: {id: 23397294}, head_sha: context.clientSha,
    event: 'issue_comment', head_branch: 'main', path: '.github/workflows/gate745-native-fixture.yml',
    status: 'in_progress', conclusion: null, created_at: '2026-10-11T16:50:00.000Z'};
}
function fixture(source = {}) {
  const physical = {target: {healthUrl: 'https://gate745-synthetic.up.railway.app/admin/ops/reply-cutover'},
    source: {reviewedHead: hex('e'), reviewedTree: hex('7'), deploySha: hex('e'), deployTree: hex('7')},
    authority: {ownerGrantCommentId: '701', ownerGrantDigest: D, createdAt: '2026-10-11T16:50:00.000Z', expiresAt: '2026-10-11T18:50:00.000Z'},
    seed: {helperManifestDigest: D, unknownJournalDigest: D, recipeDigest: D},
    caps: {helperDeploys: 3, proofInvocations: 4, maxHelperInvocations: 7, metadataChallengeGets: 6, configurationTransitions: 7}};
  const b = {schemaVersion: 'gate745.fixture-run-binding/v2', state: 'BOUND', enabled: true,
    run: {...context, id: context.runId, attempt: 1, controllerSha: source.controllerSha ?? hex('c'), controllerTree: source.controllerTree ?? hex('4'), workflowPath: '.github/workflows/gate745-native-fixture.yml'},
    ops: {sha: source.opsSha ?? hex('d'), tree: source.opsTree ?? hex('6')}, helper: {sha: hex('f'), tree: hex('5'), policy: 'gate745.fixture-hosted-helper/v2'},
    manifestRaw: '', manifestRawDigest: D, sourceCertificate: {artifactId: '21', artifactDigest: D},
    ownerGrant: {commentId: '701', bodyDigest: D}, reservation: {commentId: '702', bodyDigest: D},
    targetDigest: D, createdAt: physical.authority.createdAt, expiresAt: physical.authority.expiresAt};
  delete b.run.runId;
  const scopeBinding = structuredClone(physical); delete scopeBinding.authority.ownerGrantCommentId;
  delete scopeBinding.authority.ownerGrantDigest; delete scopeBinding.seed.helperManifestDigest;
  const acceptedReview = {ownerCommentId: '700', bodyDigest: D};
  const grant = {schemaVersion: 'gate745.fixture-effect-grant/v2', result: 'AUTHORIZED',
    scope: {ops: b.ops, controller: {sha: b.run.controllerSha, tree: b.run.controllerTree}, helper: b.helper,
      clientSha: b.run.clientSha, binding: scopeBinding}, acceptedReview: {issue: 999, commentId: '1', bodyDigest: D,
        executionId: 'delta-999-independent', reviewedHead: hex('e'), reviewedTree: hex('7')}, noNewSubscription: true, noSharedWorkspaceHardCap: true,
    conditionalReset: {authorized: true, requiresSuccess: true, requiresLive: true, requiresWholeFleetEmpty: true}};
  const grantBody = JSON.stringify(grant); b.ownerGrant.bodyDigest = sha256(grantBody); physical.authority.ownerGrantDigest = b.ownerGrant.bodyDigest;
  b.manifestRaw = JSON.stringify({schemaVersion: 'agent-ops.gate745-isolated-fixture-manifest/v2', state: 'BOUND', enabled: true, binding: physical});
  b.manifestRawDigest = sha256(b.manifestRaw);
  const pairs = {ops: ['AlphaCenter/ops', b.ops.sha, b.ops.tree], controller: ['AlphaCenter/gate', b.run.controllerSha, b.run.controllerTree],
    helper: ['AlphaCenter/gate', b.helper.sha, b.helper.tree], candidate: ['AlphaCenter/gate', hex('e'), hex('7')]};
  const c = {schemaVersion: 'gate745.fixture-source-certificate/v2', runId: context.runId, runAttempt: 1,
    clientSha: b.run.clientSha, controllerSha: b.run.controllerSha, controllerTree: b.run.controllerTree,
    opsSha: b.ops.sha, opsTree: b.ops.tree, helperSha: b.helper.sha, helperTree: b.helper.tree,
    review: {workRepository: 'AlphaCenter/delta', issue: 999, commentId: '1', bodyDigest: D, executionId: 'delta-999-independent', terminal: 'ACCEPT'},
    acceptedReview, checks: Object.entries(pairs).map(([role, [repository, sha, tree]]) => ({role, repository, sha, tree, reviewedHead: sha, reviewedTree: tree,
      ciRunId: '103', ciEvent: role === 'ops' ? 'push' : 'pull_request', ciRunAttempt: 1, ciConclusion: 'success', securityRunId: role === 'ops' ? '104' : null,
      reviewIssue: 999, reviewCommentId: '1', reviewBodyDigest: D, reviewExecutionId: 'delta-999-independent'})),
    manifestRawDigest: b.manifestRawDigest, targetDigest: b.targetDigest, createdAt: b.createdAt, expiresAt: b.expiresAt};
  const sourceBody = JSON.stringify({schemaVersion: 'gate745.fixture-source-acceptance/v2', result: 'SOURCE_ACCEPTED',
    reviews: c.checks.map(check => ({role: check.role, issue: check.reviewIssue, commentId: check.reviewCommentId,
      bodyDigest: check.reviewBodyDigest, executionId: check.reviewExecutionId})),
    source: {ops: b.ops, controller: {sha: b.run.controllerSha, tree: b.run.controllerTree}, helper: b.helper,
      candidate: {sha: physical.source.reviewedHead, tree: physical.source.reviewedTree}}});
  c.acceptedReview.bodyDigest = sha256(sourceBody);
  const acceptedComment = {id: 700, user: {id: 23397294, login: 'markmalina'}, issue_url: 'https://api.github.com/repos/AlphaCenter/gate/issues/745',
    body: sourceBody, created_at: b.createdAt, updated_at: b.createdAt};
  const certRaw = json(c); b.sourceCertificate.artifactDigest = sha256(certRaw);
  const r = reservationScope(b, sha256(certRaw)), reserveBody = JSON.stringify(r); b.reservation.bodyDigest = sha256(reserveBody);
  const raw = json(b), archive = zip(raw), certArchive = zip(certRaw, 'fixture-source-certificate.json', {descriptor: true});
  const ownerComment = {id: 701, user: {id: 23397294, login: 'markmalina'}, issue_url: 'https://api.github.com/repos/AlphaCenter/gate/issues/745',
    body: grantBody, created_at: b.createdAt, updated_at: b.createdAt};
  const reservationComment = {id: 702, user: {id: 41898282, login: 'github-actions[bot]'}, issue_url: ownerComment.issue_url, body: reserveBody, created_at: b.createdAt, updated_at: b.createdAt};
  return {b, c, raw, certRaw, archive, certArchive, ownerComment, reservationComment, acceptedComment, r};
}

test('default entry denies before caller options, credentials or effects', () => {
  assert.throws(() => defaultEntry(new Proxy({}, {get() {throw new Error('read');}})), denial('E_CONTROLLER_UNBOUND'));
});
test('canonical source scope survives key order and excludes its three generated self references', () => {
  assert.equal(canonical({b: 1, a: {d: 2, c: 3}}), '{"a":{"c":3,"d":2},"b":1}');
  const {b} = fixture(), before = sourceScopeDigest(b), physical = JSON.parse(b.manifestRaw);
  physical.binding.authority.ownerGrantCommentId = '999'; physical.binding.authority.ownerGrantDigest = 'sha256:' + hex('9', 64);
  physical.binding.seed.helperManifestDigest = 'sha256:' + hex('8', 64);
  assert.equal(sourceScopeDigest({...b, manifestRaw: JSON.stringify(physical)}), before);
  assert.throws(() => canonical({x: NaN}), denial('E_CONTROLLER_CANONICAL'));
});
for (const options of [{descriptor: false, compressed: false}, {descriptor: false, compressed: true}, {descriptor: true, compressed: true}]) {
  test('authenticates real single-file ZIP and separate raw digest ' + JSON.stringify(options), () => {
    const raw = json({synthetic: true}), archive = zip(raw, 'fixture-binding.json', options);
    const result = authenticateArtifact({metadata: metadata('22', 'gate745-fixture-binding', archive), archiveBytes: archive,
      name: 'gate745-fixture-binding', filename: 'fixture-binding.json', rawDigest: sha256(raw), context});
    assert.deepEqual(result.value, {synthetic: true}); assert.notEqual(result.rawDigest, result.archiveDigest);
  });
}
for (const [name, mutate, code] of [
  ['cross-run', m => {m.workflow_run.id = 102;}, 'E_CONTROLLER_ARTIFACT_ORIGIN'],
  ['wrong sha', m => {m.workflow_run.head_sha = hex('b');}, 'E_CONTROLLER_ARTIFACT_ORIGIN'],
  ['expired', m => {m.expired = true;}, 'E_CONTROLLER_ARTIFACT_ORIGIN'],
  ['renamed', m => {m.name = 'other';}, 'E_CONTROLLER_ARTIFACT_ORIGIN'],
  ['digest drift', m => {m.digest = D;}, 'E_CONTROLLER_ARTIFACT_ORIGIN'],
]) test('artifact denies ' + name, () => {
  const raw = json({synthetic: true}), archive = zip(raw), m = metadata('22', 'gate745-fixture-binding', archive); mutate(m);
  assert.throws(() => authenticateArtifact({metadata: m, archiveBytes: archive, name: 'gate745-fixture-binding', filename: 'fixture-binding.json', rawDigest: sha256(raw), context}), denial(code));
});
test('plain JSON is never mislabeled as a GitHub ZIP archive', () => {
  const raw = json({synthetic: true}); assert.throws(() => authenticateArtifact({metadata: metadata('22', 'gate745-fixture-binding', raw),
    archiveBytes: raw, name: 'gate745-fixture-binding', filename: 'fixture-binding.json', rawDigest: sha256(raw), context}), denial('E_CONTROLLER_ZIP'));
});
test('archive traversal and additional entry counts deny after authentic archive digest', () => {
  for (const archive of [zip(json({a: 1}), '../fixture-binding.json'), (() => {const z = zip(json({a: 1})); z.writeUInt16LE(2, z.length - 12); return z;})()]) {
    assert.throws(() => authenticateArtifact({metadata: metadata('22', 'gate745-fixture-binding', archive), archiveBytes: archive,
      name: 'gate745-fixture-binding', filename: 'fixture-binding.json', rawDigest: sha256(json({a: 1})), context}), denial('E_CONTROLLER_ZIP'));
  }
});
test('source C0/current original run predicates have a complete positive case', () => assertOriginalRun(originalRun(), context, {now}));
for (const [name, mutate] of [
  ['rerun', r => {r.run_attempt = 2;}], ['foreign repo', r => {r.repository.full_name = 'AlphaCenter/aqub';}],
  ['wrong owner', r => {r.actor.id = 1;}], ['wrong workflow', r => {r.path = '.github/workflows/ops-deploy.yml';}],
  ['completed old run', r => {r.status = 'completed';}], ['stale head', r => {r.head_sha = hex('b');}],
]) test('original run denies ' + name, () => {const r = originalRun(); mutate(r); assert.throws(() => assertOriginalRun(r, context, {now}), denial('E_CONTROLLER_ORIGINAL_RUN'));});

const {publicKey, privateKey} = generateKeyPairSync('rsa', {modulusLength: 2048});
const jwk = {...publicKey.export({format: 'jwk'}), kid: 'synthetic-test-key', alg: 'RS256', use: 'sig'};
const oidcExpected = {...context, controllerSha: hex('c'), bindingDigest: D, mode: 'bootstrap', index: 1, reservationDigest: D};
function claims() {
  const seconds = now / 1000;
  return {iss: 'https://token.actions.githubusercontent.com', aud: operationAudience(oidcExpected),
    sub: 'repo:AlphaCenter/gate:environment:gate745-isolated-fixture', repository: 'AlphaCenter/gate', repository_id: '1293746267',
    repository_owner: 'AlphaCenter', repository_owner_id: '310893851',
    actor: 'markmalina', actor_id: '23397294', run_id: '101', run_attempt: '1', event_name: 'issue_comment', ref: 'refs/heads/main', ref_type: 'branch',
    sha: context.clientSha, workflow_ref: 'AlphaCenter/gate/.github/workflows/gate745-native-fixture.yml@refs/heads/main',
    job_workflow_ref: 'AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@' + hex('c'), job_workflow_sha: hex('c'),
    environment: 'gate745-isolated-fixture', runner_environment: 'github-hosted', jti: 'synthetic-test-jti-unique', iat: seconds, nbf: seconds, exp: seconds + 300};
}
function jwt(c, header = {alg: 'RS256', typ: 'JWT', kid: 'synthetic-test-key'}) {
  const raw = json(header).toString('base64url') + '.' + json(c).toString('base64url');
  return raw + '.' + sign('RSA-SHA256', Buffer.from(raw), privateKey).toString('base64url');
}
test('actual RSA signature authenticates exact C0 operation without returning JWT', () => {
  const proof = verifyControllerOidc(jwt(claims()), {keys: [jwk]}, oidcExpected, {now});
  assert.equal(proof.index, 1); assert.equal(proof.controllerSha, hex('c')); assert.ok(!Object.hasOwn(proof, 'token'));
  assert.throws(() => assertAdmittedBinding(proof), denial('E_CONTROLLER_AUTHENTICATED_BINDING_MISSING'));
});
test('validated binding is a deep immutable copy preserving original raw bytes', () => {
  const {b, raw} = fixture(), binding = validateRunBinding(b, raw, context, {now});
  b.helper.sha = hex('9'); b.run.id = '999'; b.reservation.bodyDigest = D;
  assert.equal(binding.helper.sha, hex('f')); assert.equal(binding.run.id, '101');
  assert.equal(binding.bindingRaw, raw.toString('utf8')); assert.equal(binding.bindingDigest, sha256(raw));
  for (const value of [binding, binding.run, binding.ops, binding.helper, binding.sourceCertificate, binding.ownerGrant, binding.reservation]) assert.equal(Object.isFrozen(value), true);
  assert.throws(() => {binding.helper.sha = hex('9');}, TypeError);
});
test('immutable subject authenticates only the same fixed owner and repository ids', () => {
  const c = {...claims(), sub: 'repo:AlphaCenter@310893851/gate@1293746267:environment:gate745-isolated-fixture'};
  assert.equal(verifyControllerOidc(jwt(c), {keys: [jwk]}, oidcExpected, {now}).controllerSha, hex('c'));
});
for (const [field, value] of Object.entries({iss: 'https://example.com', aud: 'wrong', repository: 'AlphaCenter/aqub', repository_id: '1', actor_id: '1',
  repository_owner: 'Foreign', repository_owner_id: '2', sub: 'repo:AlphaCenter@2/gate@1293746267:environment:gate745-isolated-fixture',
  run_id: '102', run_attempt: '2', event_name: 'workflow_dispatch', ref: 'refs/heads/other', sha: hex('b'),
  job_workflow_sha: hex('d'), job_workflow_ref: 'AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@refs/heads/main',
  environment: 'gate-production', runner_environment: 'self-hosted', exp: now / 1000, iat: now / 1000 + 100})) {
  test('signed OIDC with wrong ' + field + ' denies', () => {const c = claims(); c[field] = value;
    assert.throws(() => verifyControllerOidc(jwt(c), {keys: [jwk]}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC_CLAIMS'));});
}
test('bad signature, ambiguous key, algorithm substitution and operation replay audience deny', () => {
  const signed = jwt(claims());
  assert.throws(() => verifyControllerOidc(signed.slice(0, -2) + 'AA', {keys: [jwk]}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC'));
  assert.throws(() => verifyControllerOidc(signed, {keys: [jwk, jwk]}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC'));
  assert.throws(() => verifyControllerOidc(jwt(claims(), {alg: 'HS256', typ: 'JWT', kid: jwk.kid}), {keys: [jwk]}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC'));
  assert.throws(() => verifyControllerOidc(signed, {keys: [jwk]}, {...oidcExpected, index: 2}, {now}), denial('E_CONTROLLER_OIDC_CLAIMS'));
});
test('signed optional x5t binds only its selected canonical fixed JWKS key', () => {
  const x5t = Buffer.alloc(20, 7).toString('base64url'), other = Buffer.alloc(20, 8).toString('base64url');
  const header = {alg: 'RS256', typ: 'JWT', kid: jwk.kid, x5t};
  assert.equal(verifyControllerOidc(jwt(claims(), header), {keys: [{...jwk, x5t}]}, oidcExpected, {now}).index, 1);
  for (const [h, keys] of [[header, [jwk]], [header, [{...jwk, x5t: other}]],
    [{...header, x5t: x5t + '='}, [{...jwk, x5t}]], [{...header, x5t: 'not-a-thumbprint'}, [{...jwk, x5t}]],
    [header, [{...jwk, x5t: other}, {...jwk, kid: 'other-key', x5t}]]]) {
    assert.throws(() => verifyControllerOidc(jwt(claims(), h), {keys}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC'));
  }
  for (const field of ['jku', 'jwk', 'crit', 'x5u', 'x5c']) {
    assert.throws(() => verifyControllerOidc(jwt(claims(), {...header, [field]: 'untrusted'}), {keys: [{...jwk, x5t}]}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC'));
  }
});
test('signed GitHub nbf before fresh iat is compatible while future and nbf at expiry deny', () => {
  assert.equal(verifyControllerOidc(jwt({...claims(), nbf: now / 1000 - 600}), {keys: [jwk]}, oidcExpected, {now}).index, 1);
  for (const changes of [{nbf: now / 1000 + 6}, {nbf: now / 1000 + 4, exp: now / 1000 + 4}, {nbf: now / 1000 + 5, exp: now / 1000 + 4}]) {
    assert.throws(() => verifyControllerOidc(jwt({...claims(), ...changes}), {keys: [jwk]}, oidcExpected, {now}), denial('E_CONTROLLER_OIDC_CLAIMS'));
  }
});
test('ledger executes only closed recipe with retained reset lineage and no seventh operation', () => {
  const {b, raw} = fixture(), binding = validateRunBinding(b, raw, context, {now}), ledger = makeOperationLedger(binding, {now: () => now});
  for (const mode of ['bootstrap', 'unknown-proof', 'unknown-proof']) {const r = ledger.reserve(mode); ledger.complete(r.reservationDigest, D);}
  assert.throws(() => ledger.reserve('reset'), denial('E_CONTROLLER_RESET_LINEAGE'));
  ledger.retainSuccess({successDigest: D, liveDigest: D, quiescenceDigest: D});
  for (const mode of ['reset', 'unknown-proof', 'unknown-proof']) {const r = ledger.reserve(mode); ledger.complete(r.reservationDigest, D);}
  assert.equal(ledger.snapshot().counts['unknown-proof'], 4); assert.equal(ledger.snapshot().records.length, 6);
  assert.throws(() => ledger.reserve('seed'), denial('E_CONTROLLER_OPERATION_ORDER'));
});
test('unknown reservation permanently poisons this controller without replay or counter reset', () => {
  const {b, raw} = fixture(), binding = validateRunBinding(b, raw, context, {now}), ledger = makeOperationLedger(binding, {now: () => now});
  const r = ledger.reserve('bootstrap'); ledger.unknown(r.reservationDigest);
  assert.equal(ledger.snapshot().pending.mode, 'bootstrap'); assert.throws(() => ledger.reserve('bootstrap'), denial('E_CONTROLLER_UNKNOWN'));
});
test('edited or incomplete permanent reservation never releases its grant', () => {
  const {b, reservationComment} = fixture(); reservationComment.updated_at = '2026-10-12T00:00:00.000Z';
  assert.throws(() => assertNoPriorReservation([reservationComment], b.ownerGrant), denial('E_CONTROLLER_GRANT_USED'));
});
test('reservation pagination is finite and refuses truncation before POST', async () => {
  const {b} = fixture(); let gets = 0, posts = 0;
  await assert.rejects(() => reserveOriginalGrant({binding: b, sourceCertificateDigest: D, token: 'synthetic-token-do-not-use', fetchFn: async (_url, opts) => {
    if (opts.method === 'POST') posts++; gets++; return {ok: true, async json() {return Array.from({length: 100}, (_, i) => ({id: gets * 100 + i, body: 'synthetic'}));}};
  }}), denial('E_CONTROLLER_RESERVATION_PAGE_LIMIT'));
  assert.equal(gets, 5); assert.equal(posts, 0);
});
test('archive redirect strips GitHub Authorization and rejects foreign hosts', async () => {
  const github = makeOriginalGithub({token: 'synthetic-token-do-not-use', context, now: () => now, fetchFn: async (url, opts) => {
    if (url.startsWith('https://api.github.com/')) {assert.ok(opts.headers.Authorization); return {status: 302, headers: new Headers({location: 'https://safe.blob.core.windows.net/archive?synthetic=1'})};}
    assert.equal(opts.headers, undefined); return new Response(zip(json({synthetic: true})));
  }});
  assert.ok((await github.archive('22')).length > 0);
  const foreign = makeOriginalGithub({token: 'synthetic-token-do-not-use', context, fetchFn: async () => ({status: 302, headers: new Headers({location: 'https://example.com/x'})})});
  await assert.rejects(() => foreign.archive('22'), denial('E_CONTROLLER_ARCHIVE_REDIRECT'));
});

// Same production modules, installed beside a synthetic immutable O pin. The
// candidate's checked-in null pin and default-off state are never altered.
async function installedFixture() {
  const root = mkdtempSync(join(tmpdir(), 'gate745-controller-'));
  cpSync(new URL('.', import.meta.url), root, {recursive: true});
  const lock = JSON.parse(readFileSync(join(root, 'fixture-source.lock.json')));
  lock.ops.commit = lock.ops.reviewedCommit = hex('d'); lock.ops.tree = lock.ops.reviewedTree = hex('6');
  writeFileSync(join(root, 'fixture-source.lock.json'), JSON.stringify(lock));
  const controller = await import(pathToFileURL(join(root, 'fixture-controller.mjs')));
  const transport = await import(pathToFileURL(join(root, 'fixture-helper-transport.mjs')));
  const data = fixture(), meta = metadata('22', 'gate745-fixture-binding', data.archive), certMeta = metadata('21', 'gate745-fixture-source-certificate', data.certArchive);
  const routes = {
    '/repos/AlphaCenter/gate/actions/runs/101': originalRun(),
    '/repos/AlphaCenter/gate/actions/artifacts/22': meta, '/repos/AlphaCenter/gate/actions/artifacts/21': certMeta,
    '/repos/AlphaCenter/gate/actions/runs/101/jobs?per_page=100': {total_count: 1, jobs: [{name: 'fixture-source-admission', run_id: 101, status: 'completed', conclusion: 'success',
      steps: ['Admit exact source and owner', 'Upload source certificate'].map(name => ({name, status: 'completed', conclusion: 'success'}))}]},
    '/repos/AlphaCenter/gate/actions/runs/101/artifacts?per_page=100': {total_count: 2, artifacts: [meta, certMeta]},
    '/repos/AlphaCenter/gate/issues/comments/701': data.ownerComment,
    '/repos/AlphaCenter/gate/issues/comments/700': data.acceptedComment,
    '/repos/AlphaCenter/gate/issues/comments/702': data.reservationComment,
  };
  const options = {token: 'synthetic-token-do-not-use', context, bindingArtifactId: '22', bindingDigest: sha256(data.raw),
    bindingArchive: data.archive, certificateArchive: data.certArchive, trustedProducerArtifactId: '21', trustedProducerDigest: sha256(data.certRaw),
    now: () => now, fetchFn: async url => {assert.ok(url.startsWith('https://api.github.com/')); const value = routes[url.slice('https://api.github.com'.length)]; assert.ok(value); return new Response(JSON.stringify(value));}};
  return {root, controller, transport, data, routes, options};
}
function rewriteFixtureCertificate(f, mutate) {
  mutate(f.data.c);
  const certRaw = json(f.data.c); f.data.b.sourceCertificate.artifactDigest = sha256(certRaw);
  const reservationBody = JSON.stringify(reservationScope(f.data.b, sha256(certRaw)));
  f.data.b.reservation.bodyDigest = sha256(reservationBody); f.data.reservationComment.body = reservationBody;
  const raw = json(f.data.b), archive = zip(raw), certificateArchive = zip(certRaw, 'fixture-source-certificate.json');
  const bm = metadata('22', 'gate745-fixture-binding', archive), cm = metadata('21', 'gate745-fixture-source-certificate', certificateArchive);
  f.routes['/repos/AlphaCenter/gate/actions/artifacts/22'] = bm;
  f.routes['/repos/AlphaCenter/gate/actions/artifacts/21'] = cm;
  f.routes['/repos/AlphaCenter/gate/actions/runs/101/artifacts?per_page=100'].artifacts = [bm, cm];
  Object.assign(f.options, {bindingDigest: sha256(raw), bindingArchive: archive, certificateArchive, trustedProducerDigest: sha256(certRaw)});
}
test('Ops certificate binds literal reviewed code separately from literal released source', async () => {
  const f = await installedFixture();
  try {
    const path = join(f.root, 'fixture-source.lock.json'), lock = JSON.parse(readFileSync(path));
    lock.ops.reviewedCommit = hex('b'); lock.ops.reviewedTree = hex('9'); writeFileSync(path, JSON.stringify(lock));
    rewriteFixtureCertificate(f, c => {const row = c.checks.find(x => x.role === 'ops'); row.reviewedHead = hex('b'); row.reviewedTree = hex('9');});
    const binding = await f.controller.validateOriginalBundle(f.options);
    assert.equal(binding.ops.sha, hex('d')); assert.equal(binding.ops.tree, hex('6'));
  } finally {rmSync(f.root, {recursive: true, force: true});}
});
for (const [name, mutate] of [
  ['arbitrary Ops code SHA', c => {c.checks.find(x => x.role === 'ops').reviewedHead = hex('b');}],
  ['arbitrary Ops code tree', c => {c.checks.find(x => x.role === 'ops').reviewedTree = hex('9');}],
  ['missing reviewed code pair', c => {delete c.checks[0].reviewedTree;}],
  ['Gate reviewed SHA differs from actual', c => {c.checks.find(x => x.role === 'controller').reviewedHead = hex('b');}],
  ['Gate reviewed tree differs from actual', c => {c.checks.find(x => x.role === 'helper').reviewedTree = hex('9');}],
]) test('source certificate denies ' + name, async () => {
  const f = await installedFixture();
  try {
    rewriteFixtureCertificate(f, mutate);
    await assert.rejects(() => f.controller.validateOriginalBundle(f.options), denial('E_CONTROLLER_SOURCE_CERTIFICATE'));
  } finally {rmSync(f.root, {recursive: true, force: true});}
});
test('pure original-source validation and six-operation wire rehearsal never mint effect authority', async () => {
  const f = await installedFixture();
  try {
    const b = await f.controller.validateOriginalBundle(f.options), ledger = f.controller.makeOperationLedger(b, {now: () => now});
    assert.throws(() => f.controller.assertAdmittedBinding(b), denial('E_CONTROLLER_AUTHENTICATED_BINDING_MISSING'));
    assert.throws(() => f.controller.assertGenuineLedger(b, ledger), denial('E_CONTROLLER_LEDGER_AUTHORITY_MISSING'));
    for (const mode of ['bootstrap', 'unknown-proof', 'unknown-proof', 'reset', 'unknown-proof', 'unknown-proof']) {
      if (mode === 'reset') ledger.retainSuccess({successDigest: D, liveDigest: D, quiescenceDigest: D});
      const reservation = ledger.reserve(mode), request = f.transport.buildHelperRequest(b, reservation), index = request.operationIndex;
      assert.equal(sha256(request.bindingRaw), b.bindingDigest);
      const receipt = {schemaVersion: 'gate745.fixture-bootstrap-receipt/v2', result: 'SUCCEEDED', mode, runId: b.run.id,
        runAttempt: 1, runBindingDigest: b.bindingDigest, reservationDigest: request.reservationDigest,
        currentSourceSha: [1, 4, 6].includes(index) ? b.helper.sha : hex('e'),
        currentSourceTree: [1, 4, 6].includes(index) ? b.helper.tree : hex('7'),
        schemaRevision: [1, 4].includes(index) ? '0115_watch_voice_post_payment' : '0116_reply_dialog_versions',
        reserved: 0, sending: 0, unknownCount: 1, unknownJournalDigest: D, recipeDigest: D, targetDigest: D,
        applyPlan: false, fixtureAcceptance: 'NOT_PROVEN', executionRole: index === 6 ? 'PROOF_ONLY' : [1, 4].includes(index) ? 'CONTROL' : 'GUARDED'};
      ledger.complete(reservation.reservationDigest, f.transport.validateHelperReceipt(b, reservation, receipt));
      assert.throws(() => f.transport.validateHelperReceipt(b, reservation, {...receipt, executionRole: 'FOREIGN'}), denial('E_CONTROLLER_HELPER_RECEIPT'));
    }
    assert.equal(ledger.snapshot().records.length, 6); assert.equal(ledger.snapshot().counts['unknown-proof'], 4);
    assert.throws(() => f.transport.makeFixedHelperTransport({binding: b, ledger}), denial('E_CONTROLLER_AUTHENTICATED_BINDING_MISSING'));
  } finally {rmSync(f.root, {recursive: true, force: true});}
});
test('duplicate source artifacts and fake producer steps do not authenticate', async () => {
  const f = await installedFixture();
  try {
    const list = f.routes['/repos/AlphaCenter/gate/actions/runs/101/artifacts?per_page=100']; list.artifacts.push({...list.artifacts[0], id: 23}); list.total_count++;
    await assert.rejects(() => f.controller.validateOriginalBundle(f.options), denial('E_CONTROLLER_ARTIFACT_DUPLICATE'));
    list.artifacts.pop(); list.total_count--;
    f.routes['/repos/AlphaCenter/gate/actions/runs/101/jobs?per_page=100'].jobs[0].steps[0].conclusion = 'failure';
    await assert.rejects(() => f.controller.validateOriginalBundle(f.options), denial('E_CONTROLLER_CERTIFICATE_PRODUCER'));
  } finally {rmSync(f.root, {recursive: true, force: true});}
});
test('caller fetch, clock, token and auth ports cannot promote injected validation or access effects', async () => {
  const f = await installedFixture();
  try {
    let getters = 0, injectedReads = 0;
    const options = {...f.options, fetchFn: async () => {injectedReads++; throw new Error('CALLER_PORT_CANARY');}};
    await assert.rejects(() => f.controller.admitOriginalBundle(options), denial('E_CONTROLLER_AUTHORITY_PORT'));
    assert.equal(injectedReads, 0);
    const descriptors = {bindingArtifactId: '22', bindingDigest: sha256(f.data.raw), trustedProducerArtifactId: '21',
      trustedProducerDigest: sha256(f.data.certRaw), reservationBodyDigest: f.data.b.reservation.bodyDigest};
    for (const port of ['fetchFn', 'now', 'token', 'context', 'bindingArchive', 'certificateArchive', 'oidcProof']) {
      const value = {...descriptors}; Object.defineProperty(value, port, {enumerable: true, get() {getters++; throw new Error('GETTER_CANARY');}});
      await assert.rejects(() => f.controller.admitOriginalBundle(value), denial('E_CONTROLLER_AUTHORITY_PORT'));
    }
    const b = await f.controller.validateOriginalBundle(f.options), ledger = f.controller.makeOperationLedger(b, {now: () => now});
    for (const port of ['mintOidc', 'adminToken', 'fetchFn', 'now']) {
      const transport = {binding: b, ledger};
      Object.defineProperty(transport, port, {enumerable: true, get() {getters++; throw new Error('GETTER_CANARY');}});
      assert.throws(() => f.transport.makeFixedHelperTransport(transport), denial('E_CONTROLLER_TRUSTED_TRANSPORT'));
    }
    const shapeLedger = {reserve() {getters++;}, complete() {getters++;}, unknown() {getters++;}, snapshot() {getters++;}};
    assert.throws(() => f.transport.makeFixedHelperTransport({binding: b, ledger: shapeLedger}), denial('E_CONTROLLER_AUTHENTICATED_BINDING_MISSING'));
    assert.equal(getters, 0); assert.equal(ledger.snapshot().records.length, 0);
    assert.throws(() => f.controller.assertGenuineLedger(b, shapeLedger), denial('E_CONTROLLER_LEDGER_AUTHORITY_MISSING'));
  } finally {rmSync(f.root, {recursive: true, force: true});}
});

test('fixed hosted RSA/original reader grants only its binding and stops before unsupported helper effects', async () => {
  // An explicit temporary source checkout and a subprocess-local fixed-provider
  // harness exercise the production path without real JWTs, keys or network.
  const f = await installedFixture(), command = (cwd, args) => execFileSync('git', args, {cwd, stdio: 'pipe'}).toString().trim();
  try {
    const {mkdirSync} = await import('node:fs');
    const ops = join(f.root, '.agent-ops/executor'); mkdirSync(ops, {recursive: true});
    for (const cwd of [ops, f.root]) {
      command(cwd, ['init', '--quiet']); command(cwd, ['config', 'user.name', 'Synthetic Source Test']);
      command(cwd, ['config', 'user.email', 'synthetic@example.invalid']);
    }
    writeFileSync(join(ops, 'SOURCE_ONLY.md'), 'Synthetic source identity; no runtime or provider capabilities.\n');
    command(ops, ['add', '.']); command(ops, ['commit', '--quiet', '-m', 'synthetic O source']);
    const opsSha = command(ops, ['rev-parse', 'HEAD']), opsTree = command(ops, ['rev-parse', 'HEAD^{tree}']);
    const lockPath = join(f.root, 'fixture-source.lock.json'), lock = JSON.parse(readFileSync(lockPath));
    lock.ops.commit = lock.ops.reviewedCommit = opsSha; lock.ops.tree = lock.ops.reviewedTree = opsTree;
    writeFileSync(lockPath, JSON.stringify(lock)); writeFileSync(join(f.root, '.gitignore'), '.agent-ops/\n');
    command(f.root, ['add', '.']); command(f.root, ['commit', '--quiet', '-m', 'synthetic C0 source']);
    const controllerSha = command(f.root, ['rev-parse', 'HEAD']), controllerTree = command(f.root, ['rev-parse', 'HEAD^{tree}']);
    const data = fixture({controllerSha, controllerTree, opsSha, opsTree});
    const expected = {...context, controllerSha, bindingDigest: sha256(data.raw), mode: 'bootstrap', index: 1, reservationDigest: data.b.reservation.bodyDigest};
    const signed = jwt({...claims(), aud: operationAudience(expected), job_workflow_sha: controllerSha,
      job_workflow_ref: 'AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@' + controllerSha});
    const bm = metadata('22', 'gate745-fixture-binding', data.archive), cm = metadata('21', 'gate745-fixture-source-certificate', data.certArchive);
    const input = {jwt: signed, jwks: {keys: [jwk]}, audience: operationAudience(expected),
      bindingZip: data.archive.toString('base64'), certificateZip: data.certArchive.toString('base64'),
      routes: {'/repos/AlphaCenter/gate/actions/runs/101': originalRun(),
        '/repos/AlphaCenter/gate/actions/artifacts/22': bm, '/repos/AlphaCenter/gate/actions/artifacts/21': cm,
        '/repos/AlphaCenter/gate/actions/runs/101/jobs?per_page=100': f.routes['/repos/AlphaCenter/gate/actions/runs/101/jobs?per_page=100'],
        '/repos/AlphaCenter/gate/actions/runs/101/artifacts?per_page=100': {total_count: 2, artifacts: [bm, cm]},
        '/repos/AlphaCenter/gate/issues/comments/700': data.acceptedComment,
        '/repos/AlphaCenter/gate/issues/comments/701': data.ownerComment,
        '/repos/AlphaCenter/gate/issues/comments/702': data.reservationComment},
      options: {bindingArtifactId: '22', bindingDigest: sha256(data.raw), trustedProducerArtifactId: '21',
        trustedProducerDigest: sha256(data.certRaw), reservationBodyDigest: data.b.reservation.bodyDigest}};
    const script = `import {readFileSync} from 'node:fs'; import assert from 'node:assert/strict';
      import {admitOriginalBundle,assertAdmittedBinding,makeOperationLedger,assertGenuineLedger} from './fixture-controller.mjs';
      import {makeFixedHelperTransport} from './fixture-helper-transport.mjs';
      const x=JSON.parse(readFileSync(0,'utf8')); Date.now=()=>${now}; let api=0,effects=0,credentialReads=0,mints=0;
      process.env=new Proxy({...process.env},{get(target,key){if(key==='GATE745_FIXTURE_ADMIN_TOKEN')credentialReads++;return target[key];}});
      globalThis.fetch=async raw=>{const u=new URL(raw);
        if(u.hostname==='pipelines.actions.githubusercontent.com'){mints++;assert.equal(u.searchParams.get('audience'),x.audience);return new Response(JSON.stringify({value:x.jwt}));}
        if(u.href==='https://token.actions.githubusercontent.com/.well-known/jwks')return new Response(JSON.stringify(x.jwks));
        if(u.hostname==='api.github.com'){api++;if(u.pathname.endsWith('/zip'))return new Response(null,{status:302,headers:{location:'https://synthetic.blob.core.windows.net'+u.pathname}});
          const value=x.routes[u.pathname+u.search];assert.ok(value);return new Response(JSON.stringify(value));}
        if(u.hostname==='synthetic.blob.core.windows.net')return new Response(Buffer.from(u.pathname.includes('/22/')?x.bindingZip:x.certificateZip,'base64'));
        effects++;throw new Error('FORBIDDEN_EFFECT');};
      const binding=await admitOriginalBundle(x.options);assertAdmittedBinding(binding);
      assert.equal(Object.isFrozen(binding.helper),true);assert.equal(Object.isFrozen(binding.run),true);
      const ledger=makeOperationLedger(binding);assertGenuineLedger(binding,ledger);const transport=makeFixedHelperTransport({binding,ledger});
      await assert.rejects(()=>transport.invoke('bootstrap'),e=>e.code==='E_FIXTURE_V2_RESOURCE_LIMITS_NOT_PROVEN');
      assert.equal(ledger.snapshot().records.length,0);assert.equal(ledger.snapshot().pending,null);
      assert.deepEqual(transport.counters(),{metadataReads:0,proofReads:0,invocations:0});
      assert.throws(()=>makeFixedHelperTransport({binding,ledger:makeOperationLedger(binding,{now:()=>${now}})}),e=>e.code==='E_CONTROLLER_LEDGER_AUTHORITY_MISSING');
      assert.throws(()=>makeFixedHelperTransport({binding,ledger:{...ledger}}),e=>e.code==='E_CONTROLLER_LEDGER_AUTHORITY_MISSING');
      assert.throws(()=>assertAdmittedBinding({...binding}),e=>e.code==='E_CONTROLLER_AUTHENTICATED_BINDING_MISSING');
      assert.equal(api,10);assert.equal(mints,1);assert.equal(effects,0);assert.equal(credentialReads,0);process.stdout.write('HOSTED_AUTHORITY_RESOURCE_STOP_OK');`;
    const output = execFileSync(process.execPath, ['--input-type=module', '-e', script], {cwd: f.root, input: JSON.stringify(input), stdio: 'pipe',
      env: {PATH: process.env.PATH, GITHUB_REPOSITORY: 'AlphaCenter/gate', GITHUB_REPOSITORY_ID: '1293746267', GITHUB_RUN_ID: '101',
        GITHUB_SHA: context.clientSha, GITHUB_RUN_ATTEMPT: '1', GITHUB_REF: 'refs/heads/main', GITHUB_EVENT_NAME: 'issue_comment',
        ACTIONS_ID_TOKEN_REQUEST_URL: 'https://pipelines.actions.githubusercontent.com/synthetic', ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'synthetic-actions-request',
        GATE745_ORIGINAL_READ_TOKEN: 'synthetic-original-github-read-token', GATE745_FIXTURE_ADMIN_TOKEN: 'synthetic-admin-never-use'}}).toString();
    assert.equal(output, 'HOSTED_AUTHORITY_RESOURCE_STOP_OK');
  } finally {rmSync(f.root, {recursive: true, force: true});}
});

const crossVector = JSON.parse(readFileSync(new URL('./fixture-test-vector.json', import.meta.url), 'utf8'));
for (const producerName of ['fixture-source-admission', 'fixture / fixture-source-admission']) {
  test('shared full Ops/C0/H binding passes exact original producer ' + producerName, async () => {
    const f = await installedFixture();
    try {
      const v = structuredClone(crossVector), b = JSON.parse(v.bundleRaw), c = JSON.parse(v.certificateRaw);
      const lockPath = join(f.root, 'fixture-source.lock.json'), lock = JSON.parse(readFileSync(lockPath));
      lock.ops.commit = b.ops.sha; lock.ops.tree = b.ops.tree;
      lock.ops.reviewedCommit = c.checks.find(x => x.role === 'ops').reviewedHead;
      lock.ops.reviewedTree = c.checks.find(x => x.role === 'ops').reviewedTree;
      writeFileSync(lockPath, JSON.stringify(lock));
      const bc = zip(Buffer.from(v.bundleRaw)), cc = zip(Buffer.from(v.certificateRaw), 'fixture-source-certificate.json');
      const ctx = {runId: b.run.id, clientSha: b.run.clientSha, repositoryId: b.run.repositoryId};
      const bm = {...metadata('22', 'gate745-fixture-binding', bc), workflow_run: {id: Number(ctx.runId), head_sha: ctx.clientSha}};
      const cm = {...metadata('21', 'gate745-fixture-source-certificate', cc), workflow_run: {id: Number(ctx.runId), head_sha: ctx.clientSha}};
      const routes = {
        '/repos/AlphaCenter/gate/actions/runs/101': v.run,
        '/repos/AlphaCenter/gate/actions/artifacts/22': bm, '/repos/AlphaCenter/gate/actions/artifacts/21': cm,
        '/repos/AlphaCenter/gate/actions/runs/101/jobs?per_page=100': {total_count: 1, jobs: [{...v.sourceJob, name: producerName}]},
        '/repos/AlphaCenter/gate/actions/runs/101/artifacts?per_page=100': {total_count: 2, artifacts: [bm, cm]},
        '/repos/AlphaCenter/gate/issues/comments/700': v.acceptedComment,
        '/repos/AlphaCenter/gate/issues/comments/701': v.ownerComment,
        '/repos/AlphaCenter/gate/issues/comments/702': v.reservationComment,
      };
      const options = {...f.options, context: ctx, bindingDigest: sha256(v.bundleRaw), bindingArchive: bc, certificateArchive: cc,
        trustedProducerDigest: sha256(v.certificateRaw), now: () => v.now,
        fetchFn: async url => {const record = routes[url.slice('https://api.github.com'.length)]; assert.ok(record); return new Response(JSON.stringify(record));}};
      const admitted = await f.controller.validateOriginalBundle(options);
      assert.equal(admitted.bindingDigest, sha256(v.bundleRaw));
      assert.equal(c.checks.find(x => x.role === 'ops').ciEvent, 'push');
      routes['/repos/AlphaCenter/gate/actions/runs/101/jobs?per_page=100'].jobs[0].steps[0].status = 'in_progress';
      await assert.rejects(() => f.controller.validateOriginalBundle(options), denial('E_CONTROLLER_CERTIFICATE_PRODUCER'));
    } finally {rmSync(f.root, {recursive: true, force: true});}
  });
}

test('isolated explicit all-null Ops pins stop producer and installer before authority access', async () => {
  const root = mkdtempSync(join(tmpdir(), 'gate745-null-source-pin-'));
  const originalEnvironment = Object.getOwnPropertyDescriptor(process, 'env'), originalFetch = globalThis.fetch;
  let environmentReads = 0, networkCalls = 0;
  try {
    cpSync(new URL('.', import.meta.url), root, {recursive: true});
    const lockPath = join(root, 'fixture-source.lock.json'), lock = JSON.parse(readFileSync(lockPath));
    lock.ops.commit = lock.ops.tree = lock.ops.reviewedCommit = lock.ops.reviewedTree = null;
    writeFileSync(lockPath, JSON.stringify(lock));
    const {createOpsSourceBundle, installOriginalOpsSource} = await import(pathToFileURL(join(root, 'fixture-ops-source.mjs')));
    Object.defineProperty(process, 'env', {configurable: true, get() {environmentReads++; throw new Error('Unexpected environment or credential getter');}});
    globalThis.fetch = async () => {networkCalls++; throw new Error('Unexpected network before missing-pin denial');};
    assert.throws(() => createOpsSourceBundle(), denial('E_CONTROLLER_OPS_PIN_MISSING'));
    await assert.rejects(() => installOriginalOpsSource(), denial('E_CONTROLLER_OPS_PIN_MISSING'));
  } finally {
    Object.defineProperty(process, 'env', originalEnvironment); globalThis.fetch = originalFetch;
    rmSync(root, {recursive: true, force: true});
  }
  assert.equal(environmentReads, 0); assert.equal(networkCalls, 0);
});
test('shipped null or literal Ops source pins preserve source guard and unconditional runtime UNBOUND', async () => {
  const lock = readSourceLock(), refs = [lock.ops.commit, lock.ops.tree, lock.ops.reviewedCommit, lock.ops.reviewedTree];
  assert.ok(refs.every(x => x === null) || refs.every(x => /^[0-9a-f]{40}$/u.test(x)));
  assert.equal(lock.state, 'UNBOUND'); assert.equal(lock.enabled, false);
  assert.equal(lock.independentSourceReview, 'PENDING'); assert.equal(lock.helperAdapterAcceptance, 'PENDING'); assert.equal(lock.cleanupAcceptance, 'PENDING');
  const {guardSource} = await import('./fixture-hosted.mjs'), root = mkdtempSync(join(tmpdir(), 'gate745-shipped-source-pin-'));
  const output = join(root, 'source-output'), originalEnvironment = Object.getOwnPropertyDescriptor(process, 'env'), originalFetch = globalThis.fetch;
  let environmentReads = 0, credentialReads = 0, networkCalls = 0, callerReads = 0;
  try {
    writeFileSync(output, '');
    const sourceEnvironment = new Proxy({GITHUB_OUTPUT: output}, {get(target, key) {
      if (key === 'GITHUB_OUTPUT') return target[key];
      credentialReads++; throw new Error('Unexpected event or credential getter');
    }});
    Object.defineProperty(process, 'env', {configurable: true, get() {environmentReads++; return sourceEnvironment;}});
    globalThis.fetch = async () => {networkCalls++; throw new Error('Unexpected network from source guard');};
    if (refs.every(x => x === null)) {
      assert.throws(() => guardSource(), denial('E_CONTROLLER_OPS_PIN_MISSING'));
      assert.equal(environmentReads, 0); assert.equal(readFileSync(output, 'utf8'), '');
    } else {
      assert.deepEqual(guardSource(), lock);
      assert.equal(readFileSync(output, 'utf8'), 'ops-sha=' + lock.ops.commit + '\nops-tree=' + lock.ops.tree + '\n');
    }
    const beforeRuntime = environmentReads;
    assert.throws(() => defaultEntry(new Proxy({}, {get() {callerReads++; throw new Error('Unexpected caller getter');}})), denial('E_CONTROLLER_UNBOUND'));
    assert.equal(environmentReads, beforeRuntime);
  } finally {
    Object.defineProperty(process, 'env', originalEnvironment); globalThis.fetch = originalFetch;
    rmSync(root, {recursive: true, force: true});
  }
  assert.equal(credentialReads, 0); assert.equal(networkCalls, 0); assert.equal(callerReads, 0);
});
test('source delivery preserves a real canonical commit and parent, verifies all objects before import', async () => {
  const root = mkdtempSync(join(tmpdir(), 'gate745-source-roundtrip-'));
  const command = (cwd, args, input) => execFileSync('git', args, {cwd, input, stdio: ['pipe', 'pipe', 'pipe']}).toString().trim();
  try {
    const producer = join(root, 'producer'), native = join(root, 'native');
    cpSync(new URL('.', import.meta.url), producer, {recursive: true});
    const {mkdirSync} = await import('node:fs'); mkdirSync(native);
    const o = join(producer, '.agent-ops/executor'); mkdirSync(o, {recursive: true});
    command(o, ['init', '--quiet']); command(o, ['config', 'user.name', 'Synthetic Test']); command(o, ['config', 'user.email', 'synthetic@example.invalid']);
    const entry = join(o, 'agent-ops/actions/gate745-fixture-controller'); mkdirSync(entry, {recursive: true});
    writeFileSync(join(entry, 'entry.mjs'), "throw new Error('CODE_IMPORT_CANARY');\n");
    command(o, ['add', '.']); command(o, ['commit', '--quiet', '-m', 'synthetic canonical predecessor']);
    const parent = command(o, ['rev-parse', 'HEAD']);
    writeFileSync(join(o, 'README.md'), 'Entirely synthetic source delivery test.\n'); command(o, ['add', '.']);
    command(o, ['commit', '--quiet', '-m', 'synthetic accepted source']);
    const sha = command(o, ['rev-parse', 'HEAD']), tree = command(o, ['rev-parse', 'HEAD^{tree}']);
    const lp = join(producer, 'fixture-source.lock.json'), lock = JSON.parse(readFileSync(lp));
    lock.ops.commit = lock.ops.reviewedCommit = sha; lock.ops.tree = lock.ops.reviewedTree = tree; writeFileSync(lp, JSON.stringify(lock));
    const output = join(root, 'outputs'); writeFileSync(output, '');
    execFileSync(process.execPath, [join(producer, 'fixture-ops-source.mjs'), 'produce'], {cwd: producer, env: {...process.env, GITHUB_OUTPUT: output}, stdio: 'pipe'});
    const raw = readFileSync(join(producer, '.agent-ops/output/fixture-ops-source.json')), packet = JSON.parse(raw);
    const {validateOpsSourceBundle} = await import('./fixture-ops-source.mjs');
    const checked = validateOpsSourceBundle(packet, {sha, tree}); assert.deepEqual(checked.parents, [parent]);
    assert.match(readFileSync(output, 'utf8'), /^ops-source-raw-digest=sha256:/u);
    const script = "import {installOpsSourceBundle} from " + JSON.stringify(new URL('./fixture-ops-source.mjs', import.meta.url).href) + ";" +
      "import {readFileSync} from 'node:fs'; const x=JSON.parse(readFileSync(0)); installOpsSourceBundle(x.packet,x.pin);";
    execFileSync(process.execPath, ['--input-type=module', '-e', script], {cwd: native, input: JSON.stringify({packet, pin: {sha, tree}}), stdio: 'pipe'});
    const installed = join(native, '.agent-ops/executor');
    assert.equal(command(installed, ['rev-parse', 'HEAD']), sha); assert.equal(command(installed, ['rev-parse', 'HEAD^{tree}']), tree);
    assert.equal(command(installed, ['cat-file', 'commit', sha]), command(o, ['cat-file', 'commit', sha]));
    assert.ok(!readFileSync(join(installed, '.git/config'), 'utf8').includes('remote'));
    const corrupt = structuredClone(packet); corrupt.objects[0].rawBase64 = Buffer.from('different object').toString('base64');
    assert.throws(() => validateOpsSourceBundle(corrupt, {sha, tree}), denial('E_CONTROLLER_OPS_OBJECT'));
    const missing = structuredClone(packet); missing.objects.pop(); assert.throws(() => validateOpsSourceBundle(missing, {sha, tree}));
    const foreign = {...packet, commitSha: hex('f')}; assert.throws(() => validateOpsSourceBundle(foreign, {sha, tree}), denial('E_CONTROLLER_OPS_SOURCE'));
    // A tracked Git symlink is rejected even when Git itself gives its tree
    // and commit valid canonical hashes. Its target is never materialized.
    const {symlinkSync} = await import('node:fs'); symlinkSync('/outside/fixture', join(o, 'unsafe-link'));
    command(o, ['add', '.']); command(o, ['commit', '--quiet', '-m', 'synthetic malicious symlink']);
    lock.ops.commit = command(o, ['rev-parse', 'HEAD']); lock.ops.tree = command(o, ['rev-parse', 'HEAD^{tree}']); writeFileSync(lp, JSON.stringify(lock));
    assert.throws(() => execFileSync(process.execPath, [join(producer, 'fixture-ops-source.mjs'), 'produce'], {cwd: producer, env: {...process.env, GITHUB_OUTPUT: output}, stdio: 'pipe'}), e => e.stderr?.toString().trim() === 'E_CONTROLLER_OPS_MODE');
  } finally {rmSync(root, {recursive: true, force: true});}
});

test('large source ZIP has its own fixed descriptor; B keeps the small bound', async () => {
  const raw = json({syntheticSourcePadding: 'x'.repeat(300000)}), archive = zip(raw, 'fixture-ops-source.json', {compressed: false});
  const m = metadata('23', 'gate745-fixture-ops-source', archive);
  assert.equal(authenticateArtifact({metadata: m, archiveBytes: archive, name: m.name,
    filename: 'fixture-ops-source.json', rawDigest: sha256(raw), context}).raw.length, raw.length);
  assert.throws(() => authenticateArtifact({metadata: {...m, name: 'gate745-fixture-binding'}, archiveBytes: archive,
    name: 'gate745-fixture-binding', filename: 'fixture-binding.json', rawDigest: sha256(raw), context}), denial('E_CONTROLLER_ARTIFACT_ORIGIN'));
  const fetchFn = async url => url.startsWith('https://api.github.com/')
    ? {status: 302, headers: new Headers({location: 'https://synthetic.blob.core.windows.net/source?synthetic=true'})}
    : new Response(archive);
  const loader = makeOriginalGithub({token: 'synthetic-token-do-not-use', context, fetchFn});
  assert.equal((await loader.opsSourceArchive('23')).length, archive.length);
  await assert.rejects(() => loader.opsSourceArchive('24'), denial('E_CONTROLLER_GITHUB_READ_CAP'));
  const small = makeOriginalGithub({token: 'synthetic-token-do-not-use', context, fetchFn});
  await assert.rejects(() => small.archive('23'), denial('E_CONTROLLER_GITHUB_SHAPE'));
});
test('GitHub metadata and comment pools are finite independently of helper challenge GETs', async () => {
  let calls = 0; const loader = makeOriginalGithub({token: 'synthetic-token-do-not-use', context,
    fetchFn: async () => {calls++; return new Response('{}');}});
  for (let index = 1; index <= 7; index++) await loader.artifact(String(index));
  await assert.rejects(() => loader.artifact('8'), denial('E_CONTROLLER_GITHUB_READ_CAP'));
  assert.equal(calls, 7);
  for (let index = 1; index <= 3; index++) await loader.ownerComment(String(index));
  await assert.rejects(() => loader.ownerComment('4'), denial('E_CONTROLLER_GITHUB_READ_CAP'));
  assert.equal(calls, 10);
});

test('actual inline workflow bootstrap authenticates signed C0 identity before checkout and App access', () => {
  const workflow = readFileSync(new URL('../../.github/workflows/gate745-fixture-controller.yml', import.meta.url), 'utf8');
  const block = workflow.match(/node --input-type=module <<'NODE'\n([\s\S]*?)\n          NODE/u)?.[1];
  assert.ok(block); const code = block.split('\n').map(x => x.startsWith('          ') ? x.slice(10) : x).join('\n');
  const root = mkdtempSync(join(tmpdir(), 'gate745-bootstrap-')), output = join(root, 'output');
  const source = {...claims(), aud: 'gate745-fixture-source-bootstrap/v2', environment: 'gate-production',
    sub: 'repo:AlphaCenter/gate:environment:gate-production'};
  function run(changes, header, keys = [jwk]) {
    writeFileSync(output, '');
    const token = jwt({...source, ...changes}, header);
    const prelude = "Date.now=()=>" + now + "; const testToken=" + JSON.stringify(token) + "; const testKeys=" + JSON.stringify({keys}) + ";" +
      "globalThis.fetch=async (url,options)=>{ if(String(url).startsWith('https://pipelines.actions.githubusercontent.com/')){ if(options.headers.Authorization!=='Bearer synthetic-bootstrap-request')throw new Error('AUTH'); return new Response(JSON.stringify({value:testToken}));}" +
      "if(String(url)==='https://token.actions.githubusercontent.com/.well-known/jwks')return new Response(JSON.stringify(testKeys));throw new Error('URL');};\n";
    return execFileSync(process.execPath, ['--input-type=module', '-e', prelude + code], {stdio: 'pipe',
      env: {...process.env, ACTIONS_ID_TOKEN_REQUEST_URL: 'https://pipelines.actions.githubusercontent.com/synthetic',
        ACTIONS_ID_TOKEN_REQUEST_TOKEN: 'synthetic-bootstrap-request', GITHUB_RUN_ID: '101', GITHUB_SHA: context.clientSha, GITHUB_OUTPUT: output}});
  }
  try {
    run({}); assert.equal(readFileSync(output, 'utf8'), 'controller-sha=' + hex('c') + '\n');
    run({sub: 'repo:AlphaCenter@310893851/gate@1293746267:environment:gate-production'});
    run({nbf: now / 1000 - 600});
    const x5t = Buffer.alloc(20, 7).toString('base64url'), other = Buffer.alloc(20, 8).toString('base64url');
    const header = {alg: 'RS256', typ: 'JWT', kid: jwk.kid, x5t};
    run({}, header, [{...jwk, x5t}]);
    for (const [h, keys] of [[header, [jwk]], [header, [{...jwk, x5t: other}]],
      [{...header, x5t: x5t + '='}, [{...jwk, x5t}]], [header, [{...jwk, x5t: other}, {...jwk, kid: 'other', x5t}]],
      ...['jku', 'jwk', 'crit', 'x5u', 'x5c'].map(k => [{...header, [k]: 'foreign'}, [{...jwk, x5t}]])]) {
      assert.throws(() => run({}, h, keys)); assert.equal(readFileSync(output, 'utf8'), '');
    }
    for (const changes of [{job_workflow_sha: 'refs/heads/main'}, {repository_owner_id: '2'}, {environment: 'gate745-isolated-fixture'},
      {actor_id: '2'}, {run_attempt: '2'}, {job_workflow_ref: 'AlphaCenter/gate/.github/workflows/other.yml@' + hex('c')},
      {nbf: now / 1000 + 6}, {nbf: now / 1000 + 4, exp: now / 1000 + 4}, {iat: now / 1000 - 601}]) {
      assert.throws(() => run(changes)); assert.equal(readFileSync(output, 'utf8'), '');
    }
    assert.ok(workflow.indexOf('Admit literal O source lock') < workflow.indexOf('private-key: ${{ secrets.AGENT_OPS_REVIEWER_PRIVATE_KEY }}'));
    const native = workflow.slice(workflow.indexOf('  fixture-native:'));
    assert.ok(!native.includes('AGENT_OPS_REVIEWER_PRIVATE_KEY')); assert.ok(!native.includes('secrets.RAILWAY_TOKEN'));
    assert.ok(!native.includes('repository: AlphaCenter/ops'));
  } finally {rmSync(root, {recursive: true, force: true});}
});
