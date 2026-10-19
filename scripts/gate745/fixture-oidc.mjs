// GitHub's signature authenticates exact reviewed C0/run/operation scope. No
// owner grant, source acceptance or private key is inferred from a JSON role.
import {createPublicKey, verify} from 'node:crypto';
import {deny, exact, sha256, SHA, DIGEST, ID, readBounded, parseObject} from './fixture-artifacts.mjs';

const WORKFLOW = 'AlphaCenter/gate/.github/workflows/gate745-native-fixture.yml@refs/heads/main';
const CONTROLLER = 'AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@';
const MODES = new Set(['bootstrap', 'seed', 'reset', 'unknown-proof']);

export function operationAudience({bindingDigest, mode, index, reservationDigest}) {
  if (!DIGEST.test(bindingDigest) || !DIGEST.test(reservationDigest) || !MODES.has(mode) ||
      !Number.isSafeInteger(index) || index < 1 || index > 7) deny('E_CONTROLLER_OPERATION');
  return 'gate745-fixture:v2:' + sha256(JSON.stringify({bindingDigest, mode, index, reservationDigest}));
}

function decode(part) {
  if (typeof part !== 'string' || !/^[A-Za-z0-9_-]+$/u.test(part)) deny('E_CONTROLLER_OIDC');
  const bytes = Buffer.from(part, 'base64url');
  if (bytes.toString('base64url') !== part || bytes.length > 16384) deny('E_CONTROLLER_OIDC');
  try { return JSON.parse(bytes.toString('utf8')); } catch { deny('E_CONTROLLER_OIDC'); }
}

export function verifyControllerOidc(token, jwks, expected, {now = Date.now()} = {}) {
  if (typeof token !== 'string' || token.length > 32768 || !SHA.test(expected.controllerSha) ||
      !SHA.test(expected.clientSha) || !ID.test(expected.runId) ||
      !Number.isSafeInteger(expected.repositoryId) || expected.repositoryId <= 0) deny('E_CONTROLLER_OIDC');
  const parts = token.split('.');
  if (parts.length !== 3) deny('E_CONTROLLER_OIDC');
  const decodedHeader = decode(parts[0]), hasThumbprint = Object.hasOwn(decodedHeader, 'x5t');
  const header = exact(decodedHeader, hasThumbprint ? ['alg', 'kid', 'typ', 'x5t'] : ['alg', 'kid', 'typ'], 'E_CONTROLLER_OIDC');
  const thumbprint = value => typeof value === 'string' && /^[A-Za-z0-9_-]{27}$/u.test(value) &&
    Buffer.from(value, 'base64url').length === 20 && Buffer.from(value, 'base64url').toString('base64url') === value;
  if (hasThumbprint && !thumbprint(header.x5t)) deny('E_CONTROLLER_OIDC');
  const claims = decode(parts[1]);
  if (header.alg !== 'RS256' || header.typ !== 'JWT' || typeof header.kid !== 'string' ||
      !/^[A-Za-z0-9._-]{1,128}$/u.test(header.kid) ||
      !jwks || !Array.isArray(jwks.keys) || jwks.keys.length > 10) deny('E_CONTROLLER_OIDC');
  const keys = jwks.keys.filter(k => k.kid === header.kid);
  if (keys.length !== 1 || keys[0].kty !== 'RSA' || keys[0].use !== 'sig' || keys[0].alg !== 'RS256' ||
      typeof keys[0].n !== 'string' || typeof keys[0].e !== 'string' ||
      !/^[A-Za-z0-9_-]+$/u.test(keys[0].n) || !/^[A-Za-z0-9_-]+$/u.test(keys[0].e)) deny('E_CONTROLLER_OIDC');
  if (hasThumbprint && (!thumbprint(keys[0].x5t) || header.x5t !== keys[0].x5t)) deny('E_CONTROLLER_OIDC');
  let key;
  try { key = createPublicKey({key: keys[0], format: 'jwk'}); } catch { deny('E_CONTROLLER_OIDC'); }
  if (key.asymmetricKeyType !== 'rsa' || key.asymmetricKeyDetails.modulusLength < 2048 ||
      parts[2].length > 2048 || !/^[A-Za-z0-9_-]+$/u.test(parts[2]) ||
      !verify('RSA-SHA256', Buffer.from(parts[0] + '.' + parts[1]), key, Buffer.from(parts[2], 'base64url'))) deny('E_CONTROLLER_OIDC');
  const seconds = Math.floor(now / 1000);
  if (!claims || Object.getPrototypeOf(claims) !== Object.prototype ||
      claims.iss !== 'https://token.actions.githubusercontent.com' ||
      claims.aud !== operationAudience(expected) ||
      !['repo:AlphaCenter/gate:environment:gate745-isolated-fixture',
        'repo:AlphaCenter@310893851/gate@1293746267:environment:gate745-isolated-fixture'].includes(claims.sub) ||
      claims.repository !== 'AlphaCenter/gate' || claims.repository_id !== '1293746267' ||
      claims.repository_id !== String(expected.repositoryId) ||
      claims.repository_owner !== 'AlphaCenter' || claims.repository_owner_id !== '310893851' ||
      claims.actor !== 'markmalina' || claims.actor_id !== '23397294' ||
      claims.run_id !== expected.runId || claims.run_attempt !== '1' ||
      claims.event_name !== 'issue_comment' || claims.ref !== 'refs/heads/main' || claims.ref_type !== 'branch' ||
      claims.sha !== expected.clientSha || claims.workflow_ref !== WORKFLOW ||
      claims.job_workflow_ref !== CONTROLLER + expected.controllerSha ||
      claims.job_workflow_sha !== expected.controllerSha ||
      claims.environment !== 'gate745-isolated-fixture' || claims.runner_environment !== 'github-hosted' ||
      typeof claims.jti !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/u.test(claims.jti) ||
      ![claims.iat, claims.nbf, claims.exp].every(Number.isSafeInteger) ||
      claims.iat > seconds + 5 || claims.nbf > seconds + 5 || claims.exp <= seconds ||
      claims.nbf >= claims.exp || claims.exp - claims.iat > 600 ||
      claims.exp <= claims.iat || seconds - claims.iat > 600) deny('E_CONTROLLER_OIDC_CLAIMS');
  // Callers retain only a redacted immutable proof; the JWT is never logged or
  // placed in an artifact. JTI must additionally be consumed durably by H v2.
  return Object.freeze({runId: expected.runId, runAttempt: 1, controllerSha: expected.controllerSha,
    bindingDigest: expected.bindingDigest, mode: expected.mode, index: expected.index,
    reservationDigest: expected.reservationDigest, jtiDigest: sha256(claims.jti), expiresAt: claims.exp * 1000});
}

export async function loadGithubJwks({fetchFn = fetch} = {}) {
  const response = await fetchFn('https://token.actions.githubusercontent.com/.well-known/jwks',
    {method: 'GET', redirect: 'error', signal: AbortSignal.timeout(10000)});
  if (!response.ok) deny('E_CONTROLLER_OIDC_JWKS');
  try { return parseObject(await readBounded(response, 32768)); } catch { deny('E_CONTROLLER_OIDC_JWKS'); }
}

// This fixed provider path accepts no caller JWT/JWKS, fetch, clock or auth
// factory. Pure verifyControllerOidc proofs themselves never grant authority.
export async function authenticateHostedController(expected) {
  exact(expected, ['runId', 'clientSha', 'repositoryId', 'controllerSha', 'bindingDigest', 'mode', 'index', 'reservationDigest'], 'E_CONTROLLER_OIDC_REQUEST');
  let url; try {url = new URL(process.env.ACTIONS_ID_TOKEN_REQUEST_URL);} catch {deny('E_CONTROLLER_OIDC_REQUEST');}
  if (url.protocol !== 'https:' || url.username || url.password || url.port || url.hash ||
      !/(^|\.)actions\.githubusercontent\.com$/u.test(url.hostname) ||
      typeof process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN !== 'string' || !process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN) deny('E_CONTROLLER_OIDC_REQUEST');
  url.searchParams.set('audience', operationAudience(expected));
  const response = await fetch(url.href, {method: 'GET', redirect: 'error',
    headers: {Authorization: 'Bearer ' + process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN}, signal: AbortSignal.timeout(10000)});
  if (!response.ok) deny('E_CONTROLLER_OIDC_REQUEST');
  let data; try {data = parseObject(await readBounded(response, 65536));} catch {deny('E_CONTROLLER_OIDC_REQUEST');}
  if (typeof data.value !== 'string' || data.value.length > 32768) deny('E_CONTROLLER_OIDC_REQUEST');
  return verifyControllerOidc(data.value, await loadGithubJwks(), expected);
}
