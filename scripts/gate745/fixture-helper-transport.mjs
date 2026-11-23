// Pure C0/H wire validation is separate from production authority. The actual
// native recipe uses the pinned O driver. Physical resource-cap verification
// has no accepted route, so this production helper cannot reserve or mutate.
import {deny, exact, sha256, DIGEST, readBounded, parseObject} from './fixture-artifacts.mjs';
import {assertAdmittedBinding, assertGenuineLedger, OPERATIONS} from './fixture-controller.mjs';

export const HELPER_METADATA_PATH = '/admin/ops/gate745-fixture-metadata';
export const HELPER_OPERATION_PATH = '/admin/ops/gate745-fixture-operation';

function helperOrigin(binding) {
  let manifest; try {manifest = JSON.parse(binding.manifestRaw);} catch {deny('E_CONTROLLER_MANIFEST');}
  const t = manifest?.binding?.target;
  if (typeof t?.healthUrl !== 'string') deny('E_CONTROLLER_HELPER_ORIGIN');
  let u; try {u = new URL(t.healthUrl);} catch {deny('E_CONTROLLER_HELPER_ORIGIN');}
  if (u.protocol !== 'https:' || u.pathname !== '/admin/ops/reply-cutover' || u.username || u.password ||
      u.port || u.search || u.hash || !/^[a-z0-9-]+\.up\.railway\.app$/u.test(u.hostname) ||
      u.hostname === 'gate-production-bab1.up.railway.app') deny('E_CONTROLLER_HELPER_ORIGIN');
  return u.origin;
}
function checkedReservation(binding, reservation) {
  exact(reservation, ['record', 'reservationDigest'], 'E_CONTROLLER_RESERVATION');
  const r = reservation.record;
  exact(r, ['schemaVersion', 'state', 'runId', 'runAttempt', 'bindingDigest', 'mode', 'index', 'priorCompletionDigest', 'retained'], 'E_CONTROLLER_RESERVATION');
  if (r.schemaVersion !== 'gate745.fixture-operation/v2' || r.state !== 'RESERVED' ||
      r.runId !== binding.run.id || r.runAttempt !== 1 || r.bindingDigest !== binding.bindingDigest ||
      !Number.isSafeInteger(r.index) || r.index < 1 || r.index > OPERATIONS.length || OPERATIONS[r.index - 1] !== r.mode ||
      sha256(JSON.stringify(r)) !== reservation.reservationDigest) deny('E_CONTROLLER_RESERVATION');
  return r;
}
// Returned bytes are unbranded protocol data; no credential, provider or OIDC
// getter is accessed and no ACCEPT or operation completion is inferred.
export function buildHelperRequest(binding, reservation) {
  const r = checkedReservation(binding, reservation);
  return Object.freeze({schemaVersion: 'gate745.fixture-helper-request/v2', runBindingDigest: binding.bindingDigest,
    bindingRaw: binding.bindingRaw, mode: r.mode, operationIndex: r.index, reservation: r,
    reservationDigest: reservation.reservationDigest});
}
export function validateHelperReceipt(binding, reservation, receipt) {
  const r = checkedReservation(binding, reservation), physical = JSON.parse(binding.manifestRaw).binding;
  const helperPhase = [1, 4, 6].includes(r.index);
  const expectedSource = helperPhase ? binding.helper : {sha: physical.source.deploySha, tree: physical.source.deployTree};
  const expectedSchema = [1, 4].includes(r.index) ? '0115_watch_voice_post_payment' : '0116_reply_dialog_versions';
  const expectedRole = r.index === 6 ? 'PROOF_ONLY' : [1, 4].includes(r.index) ? 'CONTROL' : 'GUARDED';
  if (receipt?.schemaVersion !== 'gate745.fixture-bootstrap-receipt/v2' || receipt.mode !== r.mode ||
      receipt.runId !== binding.run.id || receipt.runAttempt !== 1 || receipt.result !== 'SUCCEEDED' ||
      receipt.runBindingDigest !== binding.bindingDigest || receipt.reservationDigest !== reservation.reservationDigest ||
      receipt.applyPlan !== false || receipt.fixtureAcceptance !== 'NOT_PROVEN' || receipt.executionRole !== expectedRole ||
      receipt.currentSourceSha !== expectedSource.sha || receipt.currentSourceTree !== expectedSource.tree ||
      receipt.schemaRevision !== expectedSchema || receipt.reserved !== 0 || receipt.sending !== 0 || receipt.unknownCount !== 1 ||
      receipt.unknownJournalDigest !== physical.seed.unknownJournalDigest || receipt.recipeDigest !== physical.seed.recipeDigest ||
      receipt.targetDigest !== binding.targetDigest || !DIGEST.test(receipt.unknownJournalDigest)) deny('E_CONTROLLER_HELPER_RECEIPT');
  return sha256(JSON.stringify(receipt));
}

export function makeFixedHelperTransport(options) {
  exact(options, ['binding', 'ledger'], 'E_CONTROLLER_TRUSTED_TRANSPORT');
  const {binding, ledger} = options;
  assertAdmittedBinding(binding);
  assertGenuineLedger(binding, ledger);
  const origin = helperOrigin(binding);
  let metadataReads = 0;
  return Object.freeze({
    async metadata() {
      if (Date.now() >= Date.parse(binding.expiresAt)) deny('E_CONTROLLER_EXPIRED');
      const next = ledger.snapshot().records.length + 1;
      if (![1, 4, 6].includes(next)) deny('E_CONTROLLER_METADATA_PHASE');
      if (++metadataReads > 6) deny('E_CONTROLLER_METADATA_CAP');
      const response = await fetch(origin + HELPER_METADATA_PATH, {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(10000)});
      if (!response.ok) deny('E_CONTROLLER_METADATA');
      let m; try {m = parseObject(await readBounded(response, 2048));} catch {deny('E_CONTROLLER_METADATA');}
      exact(m, ['schemaVersion', 'sourceSha', 'helperPolicy'], 'E_CONTROLLER_METADATA');
      if (m.schemaVersion !== 'gate745.fixture-helper-metadata/v2' || m.sourceSha !== binding.helper.sha ||
          m.helperPolicy !== 'gate745.fixture-hosted-helper/v2') deny('E_CONTROLLER_METADATA');
      return Object.freeze({...m});
    },
    async invoke() {
      // This STOP precedes reserve, OIDC, admin-token access and HTTP effects.
      deny('E_FIXTURE_V2_RESOURCE_LIMITS_NOT_PROVEN');
    },
    counters() {return Object.freeze({metadataReads, proofReads: 0, invocations: 0});},
  });
}
