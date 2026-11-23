// Fixed C0 artifact producer/consumer. The native recipe is owned by the
// literal O checkout; no caller-supplied URL, source ref or command is accepted.
import {readFileSync, writeFileSync, mkdirSync, appendFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {deny, exact, sha256, ID, DIGEST, SHA, parseObject} from './fixture-artifacts.mjs';
import {readSourceLock, buildRunBinding, admitOriginalBundle} from './fixture-controller.mjs';
import {reserveOriginalGrant} from './fixture-reservation.mjs';

const OUTPUT = '.agent-ops/output';
function fixedCheckout(dir) {
  let sha, tree;
  try {
    sha = execFileSync('git', ['-C', dir, 'rev-parse', 'HEAD'], {encoding: 'utf8'}).trim();
    tree = execFileSync('git', ['-C', dir, 'rev-parse', 'HEAD^{tree}'], {encoding: 'utf8'}).trim();
  } catch {deny('E_CONTROLLER_CHECKOUT');}
  if (!SHA.test(sha) || !SHA.test(tree)) deny('E_CONTROLLER_CHECKOUT');
  return {sha, tree};
}
function fixedOutput(key, value) {
  if (!/^[a-z-]+$/u.test(key) || typeof value !== 'string' || !/^[A-Za-z0-9:/.-]{1,240}$/u.test(value) ||
      typeof process.env.GITHUB_OUTPUT !== 'string') deny('E_CONTROLLER_OUTPUT');
  appendFileSync(process.env.GITHUB_OUTPUT, key + '=' + value + '\n');
}
function sourceAdmission() {
  let data;
  try {data = parseObject(readFileSync(OUTPUT + '/fixture-source-admission.json'));} catch {deny('E_CONTROLLER_SOURCE_ADMISSION');}
  exact(data, ['certificateRaw', 'certificateDigest', 'manifestRaw', 'manifestRawDigest', 'targetDigest', 'ownerGrant', 'sourceScopeDigest']);
  if (typeof data.certificateRaw !== 'string' || typeof data.manifestRaw !== 'string' ||
      sha256(data.certificateRaw) !== data.certificateDigest || sha256(data.manifestRaw) !== data.manifestRawDigest ||
      ![data.certificateDigest, data.manifestRawDigest, data.targetDigest, data.sourceScopeDigest].every(x => DIGEST.test(x))) deny('E_CONTROLLER_SOURCE_ADMISSION');
  const certificate = parseObject(Buffer.from(data.certificateRaw));
  const actualC0 = fixedCheckout('.'), actualO = fixedCheckout('.agent-ops/executor'), lock = readSourceLock();
  if (actualO.sha !== lock.ops.commit || actualO.tree !== lock.ops.tree ||
      certificate.controllerSha !== actualC0.sha || certificate.controllerTree !== actualC0.tree ||
      certificate.opsSha !== actualO.sha || certificate.opsTree !== actualO.tree ||
      certificate.clientSha !== process.env.GITHUB_SHA || certificate.runId !== process.env.GITHUB_RUN_ID ||
      certificate.runAttempt !== 1 || certificate.manifestRawDigest !== data.manifestRawDigest ||
      certificate.targetDigest !== data.targetDigest ||
      readFileSync(OUTPUT + '/fixture-source-certificate.json', 'utf8') !== data.certificateRaw) deny('E_CONTROLLER_SOURCE_ADMISSION');
  return {...data, certificate};
}
export function guardSource() {
  // Missing literal O pin stops before any event, secret or artifact access.
  const lock = readSourceLock();
  if (lock.ops.commit === null) deny('E_CONTROLLER_OPS_PIN_MISSING');
  fixedOutput('ops-sha', lock.ops.commit); fixedOutput('ops-tree', lock.ops.tree);
  return lock;
}
export function sourceMetadata() {
  guardSource();
  const data = sourceAdmission(); fixedOutput('certificate-raw-digest', data.certificateDigest);
  fixedOutput('controller-tree', data.certificate.controllerTree);
  return data;
}
export async function produceBinding() {
  guardSource(); const data = sourceAdmission(), e = process.env;
  if (!ID.test(e.INPUT_SOURCE_CERTIFICATE_ARTIFACT_ID ?? '')) deny('E_CONTROLLER_DESCRIPTOR');
  const partial = {run: {id: data.certificate.runId, clientSha: data.certificate.clientSha,
    controllerSha: data.certificate.controllerSha, controllerTree: data.certificate.controllerTree},
    ops: {sha: data.certificate.opsSha, tree: data.certificate.opsTree},
    helper: {sha: data.certificate.helperSha, tree: data.certificate.helperTree, policy: 'gate745.fixture-hosted-helper/v2'},
    manifestRaw: data.manifestRaw, manifestRawDigest: data.manifestRawDigest, ownerGrant: data.ownerGrant};
  const reservation = await reserveOriginalGrant({binding: partial, sourceCertificateDigest: data.certificateDigest,
    token: e.GATE745_RESERVATION_TOKEN});
  const raw = buildRunBinding({certificate: data.certificate, filledManifestRaw: data.manifestRaw,
    ownerGrant: data.ownerGrant, sourceCertificateId: e.INPUT_SOURCE_CERTIFICATE_ARTIFACT_ID,
    sourceCertificateRawDigest: data.certificateDigest,
    reservation: {commentId: reservation.commentId, bodyDigest: reservation.bodyDigest}});
  mkdirSync(OUTPUT, {recursive: true, mode: 0o700});
  writeFileSync(OUTPUT + '/fixture-binding.json', raw, {mode: 0o600});
  fixedOutput('binding-raw-digest', sha256(raw));
  fixedOutput('reservation-body-digest', reservation.bodyDigest);
  return raw;
}
export async function admitHostedFixture() {
  guardSource(); const e = process.env;
  const binding = await admitOriginalBundle({
    bindingArtifactId: e.INPUT_BINDING_ARTIFACT_ID, bindingDigest: e.INPUT_BINDING_RAW_DIGEST,
    trustedProducerArtifactId: e.INPUT_SOURCE_CERTIFICATE_ARTIFACT_ID,
    trustedProducerDigest: e.INPUT_SOURCE_CERTIFICATE_RAW_DIGEST,
    reservationBodyDigest: e.INPUT_RESERVATION_BODY_DIGEST});
  const actual = fixedCheckout('.');
  if (actual.sha !== binding.run.controllerSha || actual.tree !== binding.run.controllerTree) deny('E_CONTROLLER_CHECKOUT');
  return binding;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv[2] === 'guard-source') guardSource();
    else if (process.argv[2] === 'source-metadata') sourceMetadata();
    else if (process.argv[2] === 'produce-binding') await produceBinding();
    else if (process.argv[2] === 'admit-fixture') await admitHostedFixture();
    else deny('E_CONTROLLER_MODE');
  } catch (error) {
    process.stderr.write((/^E_[A-Z0-9_]+$/u.test(error.code ?? '') ? error.code : 'E_CONTROLLER_INTERNAL') + '\n');
    process.exitCode = 1;
  }
}
