// Permanent Gate issue receipt prevents reuse after artifact retention expires.
// Only the fixed C0 reservation job may supply its ephemeral issues:write token.
import {deny, exact, sha256, canonical, ID, DIGEST} from './fixture-artifacts.mjs';

export function reservationScope(binding, sourceCertificateDigest) {
  if (!DIGEST.test(sourceCertificateDigest)) deny('E_CONTROLLER_RESERVATION_SCOPE');
  return Object.freeze({schemaVersion: 'gate745.fixture-run-reservation/v2', state: 'RESERVED',
    runId: binding.run.id, runAttempt: 1,
    ownerGrantCommentId: binding.ownerGrant.commentId, ownerGrantBodyDigest: binding.ownerGrant.bodyDigest,
    sourceScopeDigest: sourceScopeDigest(binding),
    manifestRawDigest: binding.manifestRawDigest, sourceCertificateDigest});
}

export function sourceScopeDigest(binding) {
  let manifest; try {manifest = JSON.parse(binding.manifestRaw);} catch {deny('E_CONTROLLER_MANIFEST');}
  const physical = structuredClone(manifest.binding);
  if (!physical?.authority || !physical?.seed) deny('E_CONTROLLER_MANIFEST');
  delete physical.authority.ownerGrantDigest; delete physical.authority.ownerGrantCommentId;
  delete physical.seed.helperManifestDigest;
  return sha256(canonical({ops: binding.ops, controller: {sha: binding.run.controllerSha, tree: binding.run.controllerTree},
    helper: binding.helper, clientSha: binding.run.clientSha, binding: physical}));
}

function fixedRecord(comment) {
  if (typeof comment?.body !== 'string' || comment.body.length > 4096) return null;
  let r; try {r = JSON.parse(comment.body);} catch {return null;}
  if (r?.schemaVersion !== 'gate745.fixture-run-reservation/v2') return null;
  exact(r, ['schemaVersion', 'state', 'runId', 'runAttempt', 'ownerGrantCommentId', 'ownerGrantBodyDigest',
    'sourceScopeDigest', 'manifestRawDigest', 'sourceCertificateDigest'], 'E_CONTROLLER_PRIOR_RESERVATION');
  if (r.state !== 'RESERVED' || !ID.test(r.runId) || r.runAttempt !== 1 ||
      !ID.test(r.ownerGrantCommentId) || ![r.ownerGrantBodyDigest, r.sourceScopeDigest,
        r.manifestRawDigest, r.sourceCertificateDigest].every(x => DIGEST.test(x))) deny('E_CONTROLLER_PRIOR_RESERVATION');
  return r;
}

export function assertNoPriorReservation(comments, grant) {
  if (!Array.isArray(comments) || comments.length > 500) deny('E_CONTROLLER_RESERVATION_PAGE');
  const seen = new Set();
  for (const c of comments) {
    if (!Number.isSafeInteger(c.id) || seen.has(c.id)) deny('E_CONTROLLER_RESERVATION_PAGE');
    seen.add(c.id);
    const r = fixedRecord(c);
    if (r && r.ownerGrantCommentId === grant.commentId) {
      // Edited, incomplete and uncertain records do not release the grant.
      deny('E_CONTROLLER_GRANT_USED');
    }
  }
}

export async function reserveOriginalGrant({binding, sourceCertificateDigest, token, fetchFn = fetch}) {
  if (typeof token !== 'string' || token.length < 20 || /\s/u.test(token)) deny('E_CONTROLLER_GITHUB_AUTH');
  const comments = []; let complete = false;
  for (let page = 1; page <= 5; page++) {
    const response = await fetchFn(`https://api.github.com/repos/AlphaCenter/gate/issues/745/comments?per_page=100&page=${page}`,
      {method: 'GET', redirect: 'error', headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json'}, signal: AbortSignal.timeout(15000)});
    if (!response.ok) deny('E_CONTROLLER_RESERVATION_PAGE');
    let rows; try {rows = await response.json();} catch {deny('E_CONTROLLER_RESERVATION_PAGE');}
    if (!Array.isArray(rows) || rows.length > 100) deny('E_CONTROLLER_RESERVATION_PAGE');
    comments.push(...rows);
    if (rows.length < 100) {complete = true; break;}
  }
  if (!complete) deny('E_CONTROLLER_RESERVATION_PAGE_LIMIT');
  assertNoPriorReservation(comments, binding.ownerGrant);
  const record = reservationScope(binding, sourceCertificateDigest), body = JSON.stringify(record);
  const response = await fetchFn('https://api.github.com/repos/AlphaCenter/gate/issues/745/comments',
    {method: 'POST', redirect: 'error', headers: {Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json', 'Content-Type': 'application/json'},
    body: JSON.stringify({body}), signal: AbortSignal.timeout(15000)});
  if (!response.ok) deny('E_CONTROLLER_RESERVATION_UNKNOWN');
  let comment; try {comment = await response.json();} catch {deny('E_CONTROLLER_RESERVATION_UNKNOWN');}
  if (!Number.isSafeInteger(comment.id) || comment.id <= 0 || comment.body !== body ||
      comment.issue_url !== 'https://api.github.com/repos/AlphaCenter/gate/issues/745' ||
      comment.updated_at !== comment.created_at) deny('E_CONTROLLER_RESERVATION_UNKNOWN');
  return Object.freeze({commentId: String(comment.id), bodyDigest: sha256(body), record});
}

export function authenticateReservation(comment, binding, sourceCertificateDigest) {
  if (!binding.reservation || String(comment?.id) !== binding.reservation.commentId ||
      comment.user?.id !== 41898282 || comment.user?.login !== 'github-actions[bot]' ||
      comment.issue_url !== 'https://api.github.com/repos/AlphaCenter/gate/issues/745' ||
      comment.updated_at !== comment.created_at || typeof comment.body !== 'string' ||
      sha256(comment.body) !== binding.reservation.bodyDigest ||
      canonical(fixedRecord(comment)) !== canonical(reservationScope(binding, sourceCertificateDigest))) deny('E_CONTROLLER_RESERVATION_RECORD');
  return true;
}
