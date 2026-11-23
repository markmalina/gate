import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {deny, exact, sha256, canonical, SHA, DIGEST, ID, ControllerDenied} from './fixture-artifacts.mjs';
import {authenticateArtifact, makeOriginalGithub} from './fixture-artifacts.mjs';
import {authenticateReservation} from './fixture-reservation.mjs';
import {authenticateHostedController} from './fixture-oidc.mjs';

const sourceLock = new URL('./fixture-source.lock.json', import.meta.url);
const MODES = {bootstrap: 1, seed: 1, reset: 1, 'unknown-proof': 4};
const WORKFLOW = '.github/workflows/gate745-native-fixture.yml';
export const OPERATIONS = Object.freeze(['bootstrap', 'unknown-proof', 'unknown-proof', 'reset', 'unknown-proof', 'unknown-proof']);
const admitted = new WeakMap();
const ledgers = new WeakMap();

export function readSourceLock() {
  const lock = JSON.parse(readFileSync(sourceLock, 'utf8'));
  exact(lock, ['schemaVersion', 'state', 'enabled', 'independentSourceReview', 'ops',
    'callerWorkflow', 'controllerWorkflow', 'protectedEnvironment', 'helperPolicy', 'caps',
    'runtimeBinding', 'helperAdapterAcceptance', 'cleanupAcceptance']);
  if (lock.schemaVersion !== 'gate745.fixture-controller-source-lock/v2' ||
      lock.state !== 'UNBOUND' || lock.enabled !== false || lock.independentSourceReview !== 'PENDING' ||
      lock.helperAdapterAcceptance !== 'PENDING' || lock.cleanupAcceptance !== 'PENDING') deny('E_CONTROLLER_LOCK');
  exact(lock.ops, ['repository', 'commit', 'tree', 'reviewedCommit', 'reviewedTree', 'actionPath']);
  if (lock.ops.repository !== 'AlphaCenter/ops' ||
      !([lock.ops.commit, lock.ops.tree, lock.ops.reviewedCommit, lock.ops.reviewedTree].every(x => x === null) ||
        [lock.ops.commit, lock.ops.tree, lock.ops.reviewedCommit, lock.ops.reviewedTree].every(x => SHA.test(x))) ||
      lock.ops.actionPath !== 'agent-ops/actions/gate745-fixture-controller' ||
      lock.callerWorkflow !== WORKFLOW || lock.controllerWorkflow !== '.github/workflows/gate745-fixture-controller.yml' ||
      lock.protectedEnvironment !== 'gate745-isolated-fixture' ||
      lock.helperPolicy !== 'gate745.fixture-hosted-helper/v2' ||
      lock.runtimeBinding !== 'authenticated-original-run-artifact-only' ||
      canonical(lock.caps) !== canonical({helperDeploys: 3, proofInvocations: 4, helperInvocations: 7, metadataChallengeGets: 6, configurationTransitions: 7, controlLedgerCreates: 1, controlLedgerRows: 7})) deny('E_CONTROLLER_LOCK');
  return Object.freeze(lock);
}

// Called only by the fixed source-admission job, after authenticating O's
// source facts. Artifact/comment ids come from actual completed API writes;
// neither source code nor the manifest includes its own generated B digest.
export function buildRunBinding({certificate, filledManifestRaw, ownerGrant, sourceCertificateId,
  sourceCertificateRawDigest, reservation}) {
  const source = readSourceLock();
  if (source.ops.commit === null || certificate.opsSha !== source.ops.commit || certificate.opsTree !== source.ops.tree) deny('E_CONTROLLER_OPS_PIN');
  if (typeof filledManifestRaw !== 'string' || !ID.test(sourceCertificateId) || !DIGEST.test(sourceCertificateRawDigest) ||
      !ID.test(ownerGrant?.commentId) || !DIGEST.test(ownerGrant.bodyDigest) ||
      !ID.test(reservation?.commentId) || !DIGEST.test(reservation.bodyDigest) ||
      sha256(filledManifestRaw) !== certificate.manifestRawDigest) deny('E_CONTROLLER_BUILD_BINDING');
  const b = {schemaVersion: 'gate745.fixture-run-binding/v2', state: 'BOUND', enabled: true,
    run: {id: certificate.runId, attempt: 1, clientSha: certificate.clientSha, controllerSha: certificate.controllerSha,
      controllerTree: certificate.controllerTree, workflowPath: WORKFLOW, repositoryId: 1293746267},
    ops: {sha: certificate.opsSha, tree: certificate.opsTree},
    helper: {sha: certificate.helperSha, tree: certificate.helperTree, policy: 'gate745.fixture-hosted-helper/v2'},
    manifestRaw: filledManifestRaw, manifestRawDigest: certificate.manifestRawDigest,
    sourceCertificate: {artifactId: sourceCertificateId, artifactDigest: sourceCertificateRawDigest},
    ownerGrant, reservation, targetDigest: certificate.targetDigest,
    createdAt: certificate.createdAt, expiresAt: certificate.expiresAt};
  return Buffer.from(JSON.stringify(b));
}

export function validateRunBinding(b, raw, context, {now = Date.now()} = {}) {
  exact(b, ['schemaVersion', 'state', 'enabled', 'run', 'ops', 'helper', 'manifestRaw', 'manifestRawDigest',
    'sourceCertificate', 'ownerGrant', 'reservation', 'targetDigest', 'createdAt', 'expiresAt']);
  if (b.schemaVersion !== 'gate745.fixture-run-binding/v2' || b.state !== 'BOUND' || b.enabled !== true) deny('E_CONTROLLER_BINDING');
  exact(b.run, ['id', 'attempt', 'clientSha', 'controllerSha', 'controllerTree', 'workflowPath', 'repositoryId']);
  if (b.run.id !== context.runId || b.run.attempt !== 1 || b.run.clientSha !== context.clientSha ||
      b.run.repositoryId !== 1293746267 || b.run.repositoryId !== context.repositoryId || b.run.workflowPath !== WORKFLOW ||
      !SHA.test(b.run.controllerSha) || !SHA.test(b.run.controllerTree)) deny('E_CONTROLLER_RUN');
  exact(b.ops, ['sha', 'tree']); exact(b.helper, ['sha', 'tree', 'policy']);
  if (![b.ops.sha, b.ops.tree, b.helper.sha, b.helper.tree].every(x => SHA.test(x)) ||
      b.helper.policy !== 'gate745.fixture-hosted-helper/v2') deny('E_CONTROLLER_SOURCE');
  exact(b.sourceCertificate, ['artifactId', 'artifactDigest']); exact(b.ownerGrant, ['commentId', 'bodyDigest']);
  exact(b.reservation, ['commentId', 'bodyDigest']);
  if (!ID.test(b.sourceCertificate.artifactId) || !DIGEST.test(b.sourceCertificate.artifactDigest) ||
      !ID.test(b.ownerGrant.commentId) || !DIGEST.test(b.ownerGrant.bodyDigest) ||
      !ID.test(b.reservation.commentId) || !DIGEST.test(b.reservation.bodyDigest) ||
      !DIGEST.test(b.targetDigest) || typeof b.manifestRaw !== 'string' ||
      Buffer.byteLength(b.manifestRaw) > 16384 || sha256(b.manifestRaw) !== b.manifestRawDigest ||
      !Buffer.isBuffer(raw) || raw.length > 65536) deny('E_CONTROLLER_BINDING');
  const start = Date.parse(b.createdAt), end = Date.parse(b.expiresAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || new Date(start).toISOString() !== b.createdAt ||
      new Date(end).toISOString() !== b.expiresAt || start > now || now >= end || end - start <= 0 || end - start > 120 * 60000) deny('E_CONTROLLER_EXPIRED');
  // The admitted facts must not share nested mutable objects with parser or
  // pure-test inputs. The original raw bytes/digest remain independent.
  const copy = structuredClone(b);
  const freeze = value => {
    if (value && typeof value === 'object') {for (const item of Object.values(value)) freeze(item); Object.freeze(value);}
    return value;
  };
  return freeze({...copy, bindingDigest: sha256(raw), bindingRaw: raw.toString('utf8')});
}

export function assertAdmittedBinding(binding) {
  if (!admitted.has(binding)) deny('E_CONTROLLER_AUTHENTICATED_BINDING_MISSING');
}
export function assertGenuineLedger(binding, ledger) {
  if (ledgers.get(ledger) !== binding) deny('E_CONTROLLER_LEDGER_AUTHORITY_MISSING');
}

function assertCertificate(c, binding, grant) {
  const reviewedOps = readSourceLock().ops;
  exact(c, ['schemaVersion', 'runId', 'runAttempt', 'clientSha', 'controllerSha', 'controllerTree',
    'opsSha', 'opsTree', 'helperSha', 'helperTree', 'review', 'acceptedReview', 'checks',
    'manifestRawDigest', 'targetDigest', 'createdAt', 'expiresAt'], 'E_CONTROLLER_SOURCE_CERTIFICATE');
  if (c.schemaVersion !== 'gate745.fixture-source-certificate/v2' || c.runId !== binding.run.id || c.runAttempt !== 1 ||
      c.clientSha !== binding.run.clientSha || c.controllerSha !== binding.run.controllerSha || c.controllerTree !== binding.run.controllerTree ||
      c.opsSha !== binding.ops.sha || c.opsTree !== binding.ops.tree || c.helperSha !== binding.helper.sha || c.helperTree !== binding.helper.tree ||
      c.manifestRawDigest !== binding.manifestRawDigest || c.targetDigest !== binding.targetDigest ||
      c.createdAt !== binding.createdAt || c.expiresAt !== binding.expiresAt) deny('E_CONTROLLER_SOURCE_CERTIFICATE');
  exact(c.acceptedReview, ['ownerCommentId', 'bodyDigest'], 'E_CONTROLLER_SOURCE_CERTIFICATE');
  if (!ID.test(c.acceptedReview.ownerCommentId) || !DIGEST.test(c.acceptedReview.bodyDigest)) deny('E_CONTROLLER_SOURCE_CERTIFICATE');
  exact(c.review, ['workRepository', 'issue', 'commentId', 'bodyDigest', 'executionId', 'terminal'], 'E_CONTROLLER_SOURCE_CERTIFICATE');
  if (c.review.workRepository !== 'AlphaCenter/delta' || !Number.isSafeInteger(c.review.issue) || c.review.issue <= 0 ||
      !ID.test(c.review.commentId) || !DIGEST.test(c.review.bodyDigest) || c.review.terminal !== 'ACCEPT' ||
      typeof c.review.executionId !== 'string' || !new RegExp(`^delta-${c.review.issue}-[A-Za-z0-9-]{1,120}$`, 'u').test(c.review.executionId)) deny('E_CONTROLLER_SOURCE_CERTIFICATE');
  if (!Array.isArray(c.checks) || c.checks.length !== 4) deny('E_CONTROLLER_SOURCE_CERTIFICATE');
  const expected = {ops: ['AlphaCenter/ops', binding.ops.sha, binding.ops.tree],
    controller: ['AlphaCenter/gate', binding.run.controllerSha, binding.run.controllerTree],
    helper: ['AlphaCenter/gate', binding.helper.sha, binding.helper.tree]};
  const roles = new Set();
  const physical = JSON.parse(binding.manifestRaw).binding;
  expected.candidate = ['AlphaCenter/gate', physical.source.reviewedHead, physical.source.reviewedTree];
  const expectedGrantReview = {issue: c.review.issue, commentId: c.review.commentId, bodyDigest: c.review.bodyDigest,
    executionId: c.review.executionId, reviewedHead: physical.source.reviewedHead, reviewedTree: physical.source.reviewedTree};
  if (canonical(grant.acceptedReview) !== canonical(expectedGrantReview)) deny('E_CONTROLLER_SOURCE_CERTIFICATE');
  for (const check of c.checks) {
    exact(check, ['role', 'repository', 'sha', 'tree', 'reviewedHead', 'reviewedTree', 'ciRunId', 'ciEvent', 'ciRunAttempt', 'ciConclusion', 'securityRunId',
      'reviewIssue', 'reviewCommentId', 'reviewBodyDigest', 'reviewExecutionId'], 'E_CONTROLLER_SOURCE_CERTIFICATE');
    if (!Object.hasOwn(expected, check.role) || roles.has(check.role) ||
        [check.repository, check.sha, check.tree].some((x, i) => x !== expected[check.role][i]) ||
        (check.role === 'ops'
          ? check.reviewedHead !== reviewedOps.reviewedCommit || check.reviewedTree !== reviewedOps.reviewedTree
          : check.reviewedHead !== check.sha || check.reviewedTree !== check.tree) ||
        !ID.test(check.ciRunId) || check.ciRunAttempt !== 1 || check.ciConclusion !== 'success' ||
        !['push', 'pull_request'].includes(check.ciEvent) ||
        (check.role === 'ops' && (check.ciEvent !== 'push' || !ID.test(check.securityRunId))) ||
        (check.role !== 'ops' && check.securityRunId !== null) ||
        !Number.isSafeInteger(check.reviewIssue) || check.reviewIssue <= 0 || !ID.test(check.reviewCommentId) ||
        !DIGEST.test(check.reviewBodyDigest) || !new RegExp(`^delta-${check.reviewIssue}-[A-Za-z0-9-]{1,120}$`, 'u').test(check.reviewExecutionId)) deny('E_CONTROLLER_SOURCE_CERTIFICATE');
    roles.add(check.role);
  }
}

export async function validateOriginalBundle({token, context, bindingArtifactId, bindingDigest,
  bindingArchive, certificateArchive, trustedProducerArtifactId, trustedProducerDigest,
  fetchFn = fetch, now = Date.now}) {
  // The hosted workflow supplies trustedProducer* exclusively from the pinned
  // source-admission job's outputs. They are not parameters in owner commands.
  const source = readSourceLock();
  if (source.ops.commit === null) deny('E_CONTROLLER_OPS_PIN_MISSING');
  if (!ID.test(bindingArtifactId) || !DIGEST.test(bindingDigest) ||
      !ID.test(trustedProducerArtifactId) || !DIGEST.test(trustedProducerDigest)) deny('E_CONTROLLER_DESCRIPTOR');
  const github = makeOriginalGithub({token, context, fetchFn, now});
  await github.originalRun();
  const bindingArtifact = authenticateArtifact({metadata: await github.artifact(bindingArtifactId), archiveBytes: bindingArchive,
    name: 'gate745-fixture-binding', filename: 'fixture-binding.json', rawDigest: bindingDigest, context});
  const b = validateRunBinding(bindingArtifact.value, bindingArtifact.raw, context, {now: now()});
  if (b.ops.sha !== source.ops.commit || b.ops.tree !== source.ops.tree) deny('E_CONTROLLER_OPS_PIN');
  if (b.sourceCertificate.artifactId !== trustedProducerArtifactId ||
      b.sourceCertificate.artifactDigest !== trustedProducerDigest) deny('E_CONTROLLER_CERTIFICATE_PRODUCER');
  const certificate = authenticateArtifact({metadata: await github.artifact(trustedProducerArtifactId), archiveBytes: certificateArchive,
    name: 'gate745-fixture-source-certificate', filename: 'fixture-source-certificate.json', rawDigest: trustedProducerDigest, context});
  const jobs = await github.jobs();
  const producers = jobs.filter(j => ['fixture-source-admission', 'fixture / fixture-source-admission'].includes(j.name));
  if (producers.length !== 1 || producers[0].run_id?.toString() !== context.runId ||
      producers[0].status !== 'completed' || producers[0].conclusion !== 'success' ||
      !['Admit exact source and owner', 'Upload source certificate'].every(name =>
        producers[0].steps?.filter(s => s.name === name && s.status === 'completed' && s.conclusion === 'success').length === 1)) deny('E_CONTROLLER_CERTIFICATE_PRODUCER');
  const artifacts = await github.artifactList();
  for (const a of [bindingArtifact, certificate]) {
    const matches = artifacts.filter(x => x.name === a.name);
    if (matches.length !== 1 || String(matches[0].id) !== a.id) deny('E_CONTROLLER_ARTIFACT_DUPLICATE');
  }
  const owner = await github.ownerComment(b.ownerGrant.commentId);
  const grant = assertOwnerGrant(owner, b);
  assertCertificate(certificate.value, b, grant);
  assertSourceAcceptance(await github.ownerComment(certificate.value.acceptedReview.ownerCommentId), certificate.value, b, owner.created_at);
  authenticateReservation(await github.ownerComment(b.reservation.commentId), b, certificate.rawDigest);
  return b;
}

function actualCheckout(path) {
  const env = {PATH: process.env.PATH, LANG: 'C', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_TERMINAL_PROMPT: '0', GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'core.hooksPath', GIT_CONFIG_VALUE_0: '/dev/null'};
  try {
    const sha = execFileSync('git', ['-C', path, 'rev-parse', 'HEAD'], {encoding: 'utf8', env, stdio: ['ignore', 'pipe', 'pipe']}).trim();
    const tree = execFileSync('git', ['-C', path, 'rev-parse', 'HEAD^{tree}'], {encoding: 'utf8', env, stdio: ['ignore', 'pipe', 'pipe']}).trim();
    if (!SHA.test(sha) || !SHA.test(tree)) deny('E_CONTROLLER_CHECKOUT');
    return {sha, tree};
  } catch {deny('E_CONTROLLER_CHECKOUT');}
}

// Only this fixed hosted path may create the private binding authority.
// Injected-fetch validation above remains explicitly unbranded data.
export async function admitOriginalBundle(options) {
  const source = readSourceLock();
  if (source.ops.commit === null) deny('E_CONTROLLER_OPS_PIN_MISSING');
  exact(options, ['bindingArtifactId', 'bindingDigest', 'trustedProducerArtifactId', 'trustedProducerDigest', 'reservationBodyDigest'], 'E_CONTROLLER_AUTHORITY_PORT');
  const {bindingArtifactId, bindingDigest, trustedProducerArtifactId, trustedProducerDigest, reservationBodyDigest} = options;
  if (!ID.test(bindingArtifactId) || !DIGEST.test(bindingDigest) || !ID.test(trustedProducerArtifactId) ||
      !DIGEST.test(trustedProducerDigest) || !DIGEST.test(reservationBodyDigest)) deny('E_CONTROLLER_DESCRIPTOR');
  const e = process.env;
  const context = {runId: e.GITHUB_RUN_ID, clientSha: e.GITHUB_SHA, repositoryId: Number(e.GITHUB_REPOSITORY_ID)};
  if (!ID.test(context.runId ?? '') || !SHA.test(context.clientSha ?? '') || context.repositoryId !== 1293746267 ||
      e.GITHUB_REPOSITORY !== 'AlphaCenter/gate' || e.GITHUB_RUN_ATTEMPT !== '1' ||
      e.GITHUB_REF !== 'refs/heads/main' || e.GITHUB_EVENT_NAME !== 'issue_comment') deny('E_CONTROLLER_HOSTED_CONTEXT');
  const controller = actualCheckout('.'), ops = actualCheckout('.agent-ops/executor');
  if (ops.sha !== source.ops.commit || ops.tree !== source.ops.tree) deny('E_CONTROLLER_OPS_PIN');
  const proof = await authenticateHostedController({...context, controllerSha: controller.sha,
    bindingDigest, mode: 'bootstrap', index: 1, reservationDigest: reservationBodyDigest});
  const token = e.GATE745_ORIGINAL_READ_TOKEN;
  const github = makeOriginalGithub({token, context});
  const bindingArchive = await github.archive(bindingArtifactId), certificateArchive = await github.archive(trustedProducerArtifactId);
  const binding = await validateOriginalBundle({token, context, bindingArtifactId, bindingDigest, bindingArchive,
    certificateArchive, trustedProducerArtifactId, trustedProducerDigest});
  if (binding.run.controllerSha !== controller.sha || binding.run.controllerTree !== controller.tree ||
      binding.reservation.bodyDigest !== reservationBodyDigest || proof.bindingDigest !== binding.bindingDigest) deny('E_CONTROLLER_CHECKOUT');
  admitted.set(binding, Object.freeze({proof, controller, ops}));
  return binding;
}

function assertSourceAcceptance(comment, certificate, binding, grantCreatedAt) {
  const ref = certificate.acceptedReview;
  if (String(comment?.id) !== ref.ownerCommentId || comment.user?.id !== 23397294 || comment.user?.login !== 'markmalina' ||
      comment.issue_url !== 'https://api.github.com/repos/AlphaCenter/gate/issues/745' ||
      comment.updated_at !== comment.created_at || typeof comment.body !== 'string' || sha256(comment.body) !== ref.bodyDigest ||
      Date.parse(comment.created_at) > Date.parse(grantCreatedAt)) deny('E_CONTROLLER_SOURCE_ACCEPTANCE');
  let record; try {record = JSON.parse(comment.body);} catch {deny('E_CONTROLLER_SOURCE_ACCEPTANCE');}
  exact(record, ['schemaVersion', 'result', 'reviews', 'source'], 'E_CONTROLLER_SOURCE_ACCEPTANCE');
  const physical = JSON.parse(binding.manifestRaw).binding;
  const reviews = certificate.checks.map(c => ({role: c.role, issue: c.reviewIssue, commentId: c.reviewCommentId,
    bodyDigest: c.reviewBodyDigest, executionId: c.reviewExecutionId}));
  const source = {ops: binding.ops, controller: {sha: binding.run.controllerSha, tree: binding.run.controllerTree},
    helper: binding.helper, candidate: {sha: physical.source.reviewedHead, tree: physical.source.reviewedTree}};
  if (record.schemaVersion !== 'gate745.fixture-source-acceptance/v2' || record.result !== 'SOURCE_ACCEPTED' ||
      canonical(record.reviews) !== canonical(reviews) || canonical(record.source) !== canonical(source)) deny('E_CONTROLLER_SOURCE_ACCEPTANCE');
}

export function assertOwnerGrant(comment, binding) {
  if (String(comment?.id) !== binding.ownerGrant.commentId ||
      comment.user?.id !== 23397294 || comment.user?.login !== 'markmalina' ||
      comment.issue_url !== 'https://api.github.com/repos/AlphaCenter/gate/issues/745' ||
      comment.updated_at !== comment.created_at || typeof comment.body !== 'string' ||
      sha256(comment.body) !== binding.ownerGrant.bodyDigest) deny('E_CONTROLLER_OWNER_RECORD');
  let g; try {g = JSON.parse(comment.body);} catch {deny('E_CONTROLLER_OWNER_RECORD');}
  exact(g, ['schemaVersion', 'result', 'scope', 'acceptedReview', 'noNewSubscription',
    'noSharedWorkspaceHardCap', 'conditionalReset'], 'E_CONTROLLER_OWNER_RECORD');
  let manifest; try {manifest = JSON.parse(binding.manifestRaw);} catch {deny('E_CONTROLLER_MANIFEST');}
  const physical = structuredClone(manifest.binding);
  if (!physical?.authority || !physical?.seed) deny('E_CONTROLLER_MANIFEST');
  delete physical.authority.ownerGrantDigest; delete physical.authority.ownerGrantCommentId;
  delete physical.seed.helperManifestDigest;
  const expectedScope = {ops: binding.ops, controller: {sha: binding.run.controllerSha, tree: binding.run.controllerTree},
    helper: binding.helper, clientSha: binding.run.clientSha, binding: physical};
  if (g.schemaVersion !== 'gate745.fixture-effect-grant/v2' || g.result !== 'AUTHORIZED' ||
      canonical(g.scope) !== canonical(expectedScope) || g.noNewSubscription !== true ||
      g.noSharedWorkspaceHardCap !== true || Date.parse(comment.created_at) > Date.parse(binding.createdAt)) deny('E_CONTROLLER_OWNER_SCOPE');
  // Conditional reset refers to the same owner's original v2 authorization.
  // It is never exported as a fabricated second owner comment.
  exact(g.conditionalReset, ['authorized', 'requiresSuccess', 'requiresLive', 'requiresWholeFleetEmpty']);
  if (g.conditionalReset.authorized !== true || g.conditionalReset.requiresSuccess !== true ||
      g.conditionalReset.requiresLive !== true || g.conditionalReset.requiresWholeFleetEmpty !== true) deny('E_CONTROLLER_RESET_GRANT');
  return Object.freeze(g);
}

export function makeOperationLedger(binding, options) {
  // Injected clocks are useful for pure rehearsal but never mint a trusted
  // production ledger, even if the binding itself was admitted previously.
  if (options !== undefined) exact(options, ['now'], 'E_CONTROLLER_LEDGER_OPTIONS');
  const now = options === undefined ? Date.now : options.now;
  if (typeof now !== 'function') deny('E_CONTROLLER_LEDGER_OPTIONS');
  const counts = {bootstrap: 0, seed: 0, reset: 0, 'unknown-proof': 0};
  const records = []; let pending = null, uncertain = false, retained = null;
  function fresh() {
    if (uncertain) deny('E_CONTROLLER_UNKNOWN');
    if (now() >= Date.parse(binding.expiresAt)) deny('E_CONTROLLER_EXPIRED');
  }
  const ledger = Object.freeze({
    retainSuccess({successDigest, liveDigest, quiescenceDigest}) {
      fresh();
      if (pending || retained || records.length !== 3 || ![successDigest, liveDigest, quiescenceDigest].every(x => DIGEST.test(x))) deny('E_CONTROLLER_RESET_LINEAGE');
      retained = Object.freeze({successDigest, liveDigest, quiescenceDigest});
    },
    reserve(mode) {
      fresh();
      if (!Object.hasOwn(MODES, mode) || pending || records.length >= 7 || counts[mode] >= MODES[mode]) deny('E_CONTROLLER_CAP');
      if (OPERATIONS[records.length] !== mode) deny('E_CONTROLLER_OPERATION_ORDER');
      if (mode !== 'unknown-proof' && now() >= Date.parse(binding.createdAt) + 100 * 60000) deny('E_CONTROLLER_NEW_CASE_DEADLINE');
      if (mode === 'reset' && !retained) deny('E_CONTROLLER_RESET_LINEAGE');
      counts[mode]++; const index = records.length + 1;
      const record = Object.freeze({schemaVersion: 'gate745.fixture-operation/v2', state: 'RESERVED',
        runId: binding.run.id, runAttempt: 1, bindingDigest: binding.bindingDigest, mode, index,
        priorCompletionDigest: records.at(-1)?.completionDigest ?? null,
        retained: mode === 'reset' ? retained : null});
      pending = {record, reservationDigest: sha256(JSON.stringify(record))};
      return Object.freeze({...pending});
    },
    complete(reservationDigest, receiptDigest) {
      fresh();
      if (!pending || pending.reservationDigest !== reservationDigest || !DIGEST.test(receiptDigest)) deny('E_CONTROLLER_COMPLETION');
      const record = Object.freeze({...pending.record, state: 'COMPLETED', reservationDigest, receiptDigest});
      const completionDigest = sha256(JSON.stringify(record));
      records.push(Object.freeze({...record, completionDigest})); pending = null;
      return Object.freeze({record, completionDigest});
    },
    unknown(reservationDigest) {
      if (!pending || pending.reservationDigest !== reservationDigest) deny('E_CONTROLLER_COMPLETION');
      uncertain = true; // Reservation is retained; no replay or counter reset.
      return Object.freeze({...pending.record, state: 'UNKNOWN', reservationDigest});
    },
    snapshot() {return Object.freeze({records: Object.freeze([...records]), counts: Object.freeze({...counts}), uncertain, pending: pending?.record ?? null});},
  });
  if (options === undefined && admitted.has(binding)) ledgers.set(ledger, binding);
  return ledger;
}

export function defaultEntry() {
  // Static admission deliberately precedes all environment/credential getters.
  readSourceLock();
  deny('E_CONTROLLER_UNBOUND');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {defaultEntry();} catch (error) {
    process.stderr.write((error instanceof ControllerDenied ? error.code : 'E_CONTROLLER_INTERNAL') + '\n');
    process.exitCode = 1;
  }
}
