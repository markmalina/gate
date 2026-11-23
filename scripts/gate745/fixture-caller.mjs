// Fixed P shell. Completing source wiring cannot authorize native effects.
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

export const CONTROLLER_SHA = '7ec459467b606ac67ec2110289e9018fa322b8fa';
export const CALLER_WORKFLOW = '.github/workflows/gate745-native-fixture.yml';
export const COMMAND = '/ops-gate745 fixture run=complete';
const SHA = /^[0-9a-f]{40}$/u;
const DIGEST = /^sha256:[0-9a-f]{64}$/u;
function deny(code) {const error = new Error(code);error.code = code;throw error;}
function object(value, keys, code) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype ||
      Object.keys(value).length !== keys.length || keys.some(k => !Object.hasOwn(value,k))) deny(code);
}

// Pure source checks never create an admitted binding or an effect authority.
export function inspectProposalSource(raw) {
  if (!Buffer.isBuffer(raw) || raw.length > 65536 || raw.length === 0 ||
      !Buffer.from(raw.toString('utf8'),'utf8').equals(raw)) deny('E_CALLER_PROPOSAL_SHAPE');
  let value;try {value=JSON.parse(raw.toString('utf8'));} catch {deny('E_CALLER_PROPOSAL_SHAPE');}
  object(value,['schemaVersion','state','enabled','manifestRaw','manifestRawDigest','reviews'],'E_CALLER_PROPOSAL_SHAPE');
  if (value.schemaVersion !== 'gate745.fixture-proposal/v2') deny('E_CALLER_PROPOSAL_SHAPE');
  if (value.state !== 'BOUND' || value.enabled !== true) deny('E_CALLER_UNBOUND');
  if (typeof value.manifestRaw !== 'string' || !DIGEST.test(value.manifestRawDigest ?? '') ||
      'sha256:'+createHash('sha256').update(value.manifestRaw).digest('hex') !== value.manifestRawDigest ||
      !Array.isArray(value.reviews) || value.reviews.length !== 4) deny('E_CALLER_PROPOSAL_SHAPE');
  // Full signed reviews, target, original owner grant and physical evidence
  // belong to accepted O/C0 admission. This pure result grants none.
  return Object.freeze({manifestRawDigest:value.manifestRawDigest, reviewCount:4, effectAuthority:false});
}

export function inspectOriginalEvent(event, runtime) {
  object(runtime,['repository','repositoryId','ownerId','actor','actorId','triggeringActor',
    'eventName','ref','sha','runId','runAttempt','workflowRef'],'E_CALLER_RUNTIME');
  if (runtime.repository !== 'AlphaCenter/gate' || runtime.repositoryId !== '1293746267' ||
      runtime.ownerId !== '310893851' || runtime.actor !== 'markmalina' ||
      runtime.actorId !== '23397294' || runtime.triggeringActor !== 'markmalina' ||
      runtime.eventName !== 'issue_comment' || runtime.ref !== 'refs/heads/main' ||
      !SHA.test(runtime.sha ?? '') || !/^[1-9][0-9]{0,19}$/u.test(runtime.runId ?? '') ||
      runtime.runAttempt !== '1' ||
      runtime.workflowRef !== 'AlphaCenter/gate/'+CALLER_WORKFLOW+'@refs/heads/main') deny('E_CALLER_RUNTIME');
  if (!event || event.action !== 'created' || event.issue?.number !== 745 ||
      Object.hasOwn(event.issue,'pull_request') || event.issue.repository_url !== 'https://api.github.com/repos/AlphaCenter/gate' ||
      event.repository?.id !== 1293746267 || event.repository.full_name !== 'AlphaCenter/gate' ||
      event.repository.owner?.id !== 310893851 || event.sender?.id !== 23397294 ||
      event.sender.login !== 'markmalina' || event.comment?.user?.id !== 23397294 ||
      event.comment.user.login !== 'markmalina' || event.comment.body !== COMMAND ||
      !Number.isSafeInteger(event.comment.id) || event.comment.id <= 0 ||
      event.comment.created_at !== event.comment.updated_at ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/u.test(event.comment.created_at ?? '')) deny('E_CALLER_EVENT');
  return Object.freeze({runId:runtime.runId, clientSha:runtime.sha, effectAuthority:false});
}

export function defaultEntry() {
  // Unconditional compiled SOURCE barrier. No argument, event, environment,
  // credential getter, file, artifact, network or reservation is touched.
  // Removing it needs the reviewed preparation/cleanup protocol and real grant.
  deny('E_CALLER_UNBOUND');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3 || process.argv[2] !== 'preflight') deny('E_CALLER_MODE');
    defaultEntry();
  } catch (error) {
    process.stderr.write((/^E_CALLER_[A-Z0-9_]+$/u.test(error.code ?? '') ? error.code : 'E_CALLER_INTERNAL')+'\n');
    process.exitCode=1;
  }
}
