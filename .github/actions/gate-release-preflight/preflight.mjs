import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const TARGET = Object.freeze({
  repository: 'markmalina/gate', repositoryId: 1293746267, ownerId: 23397294,
  projectId: '972b6ab7-988a-421c-abfa-425451ad714f',
  environmentId: 'a148b138-44b3-4349-9d4b-292880503055',
  serviceId: '099cbf8c-1d94-43fe-8714-90725bf93998',
});
const SHA = /^[a-f0-9]{40}$/u;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/u;
const API = 'https://backboard.railway.com/graphql/v2';
const MAIN = 'https://api.github.com/repos/markmalina/gate/git/ref/heads/main';
const hash = x => createHash('sha256').update(x).digest('hex');
const blobHash = x => createHash('sha1').update(`blob ${x.byteLength}\0`).update(x).digest('hex');
const GRAPHQL_CODES = new Set(['GRAPHQL_PARSE_FAILED','GRAPHQL_VALIDATION_FAILED','BAD_USER_INPUT',
  'UNAUTHENTICATED','FORBIDDEN','INTERNAL_SERVER_ERROR','TOO_MANY_REQUESTS','RATE_LIMITED','NOT_FOUND','UNCLASSIFIED']);
const graphqlCodes = errors => [...new Set((Array.isArray(errors) && errors.length ? errors.slice(0, 8) : [null])
  .map(error => GRAPHQL_CODES.has(error?.extensions?.code) ? error.extensions.code : 'UNCLASSIFIED'))].sort();
const httpStatus = value => Number.isInteger(value) && value >= 100 && value <= 599 ? value : undefined;
export class Stop extends Error {
  constructor(code, stage, details = {}) {
    super(code); this.code = code; this.stage = stage;
    if (httpStatus(details.httpStatus) !== undefined) this.httpStatus = details.httpStatus;
    if (details.graphqlCodes !== undefined) this.graphqlCodes = graphqlCodes(
      Array.isArray(details.graphqlCodes) ? details.graphqlCodes.map(code => ({ extensions: { code } })) : null);
  }
}
export function safeDiagnostic(error) {
  if (!(error instanceof Stop)) return { code: 'E_INTERNAL', stage: 'local' };
  return { code: error.code, stage: error.stage,
    ...(httpStatus(error.httpStatus) !== undefined ? { httpStatus: error.httpStatus } : {}),
    ...(error.graphqlCodes !== undefined ? { graphqlCodes: error.graphqlCodes } : {}) };
}
const stop = (code, stage) => { throw new Stop(code, stage); };
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);

// Fixed read-only documents copied by field/argument from accepted Ops source.
// No mutation, generic query, variable-value query, App call or DB client exists.
export const QUERIES = Object.freeze({
  auth: `query GateReleaseAuth($projectId: String!) {
    projectToken { id project { id } environment { id } }
    project(id: $projectId) { id services { edges { node { id } } } }
  }`,
  state: `query GateReleaseState($project: String!, $environment: String!, $service: String!) {
    environment(id: $environment, projectId: $project) { id name unmergedChangesCount }
    environmentStagedChanges(environmentId: $environment) { environmentId status patch(decryptVariables: false) }
    serviceInstance(environmentId: $environment, serviceId: $service) {
      serviceId environmentId railwayConfigFile rootDirectory startCommand preDeployCommand source { repo image }
      latestDeployment { id status meta deploymentStopped }
      activeDeployments { id status meta deploymentStopped }
    }
    serviceInstanceAutoDeployStatus(projectId: $project, environmentId: $environment, serviceId: $service) { enabled canEnable }
    deploymentTriggers(projectId: $project, environmentId: $environment, serviceId: $service, first: 9) {
      edges { node { id projectId environmentId serviceId provider repository branch baseEnvironmentOverrideId } }
      pageInfo { hasNextPage }
    }
  }`,
});

export function assertCaller(env, event, lock, workflowBytes, now = Date.now()) {
  const pr = event?.pull_request, head = pr?.head, repository = event?.repository;
  if (env.GITHUB_EVENT_NAME !== 'pull_request' || event?.action !== 'edited' || !object(event?.changes?.body) ||
      env.GITHUB_REPOSITORY !== TARGET.repository || env.GITHUB_REPOSITORY_ID !== String(TARGET.repositoryId) ||
      repository?.id !== TARGET.repositoryId || repository?.full_name !== TARGET.repository || repository?.owner?.id !== TARGET.ownerId ||
      head?.repo?.id !== TARGET.repositoryId || head.repo.full_name !== TARGET.repository || head.repo.fork !== false ||
      pr?.base?.repo?.id !== TARGET.repositoryId || pr.base.ref !== 'main' || pr.state !== 'open' ||
      !Number.isSafeInteger(pr.number) || pr.number < 1 || !SHA.test(head?.sha ?? '') ||
      pr.body !== `/ops-gate-release-preflight expected-head-sha=${head.sha}` ||
      event.sender?.id !== TARGET.ownerId || event.sender.login !== 'markmalina' ||
      env.GITHUB_ACTOR_ID !== String(TARGET.ownerId) || env.GITHUB_ACTOR !== 'markmalina' ||
      env.GITHUB_TRIGGERING_ACTOR !== 'markmalina' || env.GITHUB_RUN_ATTEMPT !== '1' ||
      env.GITHUB_REF !== `refs/pull/${pr.number}/merge` ||
      env.GITHUB_WORKFLOW_REF !== `${TARGET.repository}/.github/workflows/gate-release-preflight.yml@refs/pull/${pr.number}/merge` ||
      !/^[1-9][0-9]{0,19}$/u.test(env.GITHUB_RUN_ID ?? '') || env.GATE_HELPER_SOURCE_SHA !== head.sha) stop('E_CALLER', 'caller');
  if (!object(lock) || lock.schemaVersion !== 'agent-ops.gate-release-preflight-lock/v1' ||
      lock.repository !== 'markmalina/ops' || lock.actionPath !== 'agent-ops/actions/gate-release-preflight' ||
      !SHA.test(lock.commit ?? '') || env.AGENT_OPS_ACTION_REF !== lock.commit ||
      env.AGENT_OPS_ACTION_REPOSITORY !== lock.repository || env.AGENT_OPS_GITHUB_ENVIRONMENT !== 'gate-production' ||
      lock.workflowSha256 !== `sha256:${hash(workflowBytes)}` ||
      !SHA.test(lock.expectedCurrentMain ?? '') || !SHA.test(lock.expectedProductionSource ?? '') ||
      typeof lock.reviewedStartCommand !== 'string' || lock.reviewedStartCommand.length > 4096 ||
      typeof lock.authorizationExpiresAt !== 'string') stop('E_LOCK', 'caller');
  const expiry = Date.parse(lock.authorizationExpiresAt);
  if (!Number.isFinite(expiry) || now >= expiry) stop('E_EXPIRED', 'caller');
  const reference = `markmalina/ops/agent-ops/actions/gate-release-preflight@${lock.commit}`;
  if (workflowBytes.toString('utf8').split(reference).length !== 2) stop('E_LOCK', 'caller');
  return { helperSourceSha: head.sha, runId: env.GITHUB_RUN_ID, executorSha: lock.commit };
}

export function verifySourceFiles(lock, railwayBytes, dockerfileBytes) {
  const c = lock.reviewedConfiguration;
  if (!object(c) || c.productionSourceSha !== lock.expectedProductionSource || !SHA.test(c.proposedSourceSha ?? '') ||
      !SHA.test(c.railwayTomlBlob ?? '') || !SHA.test(c.proposedDockerfileBlob ?? '') || !SHA.test(c.productionDockerfileBlob ?? '') ||
      c.railwayTomlIdenticalProductionAndProposed !== true ||
      blobHash(railwayBytes) !== c.railwayTomlBlob || blobHash(dockerfileBytes) !== c.proposedDockerfileBlob ||
      c.preDeployCommand !== 'alembic upgrade head' || c.startCommand !== lock.reviewedStartCommand ||
      c.productionEnvironmentOverrides !== false || c.alternateRootRailwayJsonExists !== false) stop('E_SOURCE_CONFIG', 'source-config');
  return true;
}

function pendingState(d) {
  const e = d?.environment, staged = d?.environmentStagedChanges;
  if (e?.id !== TARGET.environmentId || !Object.hasOwn(e, 'unmergedChangesCount')) return 'UNVERIFIED';
  const n = e.unmergedChangesCount;
  if (Number.isSafeInteger(n) && n > 0) return 'PENDING';
  if (n !== null && n !== 0) return 'UNVERIFIED';
  if (staged?.environmentId !== TARGET.environmentId || staged.status !== 'STAGED' ||
      !object(staged.patch) || Object.getPrototypeOf(staged.patch) !== Object.prototype || Reflect.ownKeys(staged.patch).length !== 0) return 'UNVERIFIED';
  return 'EMPTY_VERIFIED';
}
function commandState(value, expected, allowArray = false) {
  if (value === null || value === undefined) return 'UNSET_UNVERIFIED';
  if (value === expected || (allowArray && Array.isArray(value) && value.length === 1 && value[0] === expected)) return 'MATCHES_REVIEWED_SOURCE';
  return 'UNEXPECTED';
}
function deployment(d) {
  if (!object(d) || !UUID.test(d.id ?? '') || typeof d.deploymentStopped !== 'boolean' ||
      !['SUCCESS','BUILDING','DEPLOYING','INITIALIZING','WAITING','QUEUED','NEEDS_APPROVAL','FAILED','CRASHED','REMOVED','REMOVING','SLEEPING','SKIPPED','STOPPED','COMPLETED'].includes(d.status)) stop('E_DEPLOYMENT_SHAPE', 'state');
  const candidates = [d.meta?.commitSha, d.meta?.commitHash, d.meta?.sourceSha, d.meta?.repoCommit].filter(x => x !== undefined && x !== null);
  if (candidates.length === 0 || candidates.some(x => !SHA.test(x) || x !== candidates[0])) stop('E_SOURCE_SHAPE', 'state');
  return { id: d.id, status: d.status, stopped: d.deploymentStopped, sourceSha: candidates[0] };
}
function triggerSummary(connection) {
  if (!object(connection) || !Array.isArray(connection.edges) || !object(connection.pageInfo) ||
      typeof connection.pageInfo.hasNextPage !== 'boolean') stop('E_TRIGGER_SHAPE', 'state');
  if (connection.edges.length > 8 || connection.pageInfo.hasNextPage) stop('E_TRIGGER_INCOMPLETE', 'state');
  const ids = new Set();
  const rows = connection.edges.map(edge => {
    const n = edge?.node;
    if (!object(n) || typeof n.id !== 'string' || n.id.length < 1 || n.id.length > 256 || ids.has(n.id) ||
        ['provider','repository','branch'].some(k => typeof n[k] !== 'string' || n[k].length < 1 || n[k].length > 256) ||
        !(n.baseEnvironmentOverrideId === null || (typeof n.baseEnvironmentOverrideId === 'string' && n.baseEnvironmentOverrideId.length <= 256))) stop('E_TRIGGER_SHAPE', 'state');
    ids.add(n.id);
    if (n.projectId !== TARGET.projectId || n.environmentId !== TARGET.environmentId || n.serviceId !== TARGET.serviceId) stop('E_TRIGGER_TARGET', 'state');
    return { id: n.id, projection: {
      repositoryState: n.repository === TARGET.repository ? 'CURRENT_GATE' : n.repository === 'AlphaCenter/gate' ? 'HISTORICAL_GATE_ALIAS' : 'OTHER',
      branchState: n.branch === 'main' ? 'MAIN' : 'OTHER',
      providerState: n.provider === 'github' ? 'GITHUB' : 'OTHER',
      baseEnvironmentOverride: n.baseEnvironmentOverrideId === null ? 'ABSENT' : 'PRESENT',
    } };
  }).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0).map(row => row.projection);
  return { count: rows.length, completeness: 'COMPLETE', targetMatches: true, rows };
}
export function projectState(d, lock, sourceFilesVerified = false) {
  // Environment names select TOML overrides and can change independently of
  // the fixed UUID. Never infer base config until the actual name is proved.
  if (d?.environment?.name !== 'production') stop('E_ENVIRONMENT_NAME', 'state');
  const s = d?.serviceInstance;
  if (s?.serviceId !== TARGET.serviceId || s.environmentId !== TARGET.environmentId || !object(s.source) ||
      !Array.isArray(s.activeDeployments) || s.activeDeployments.length > 8) stop('E_TARGET_SHAPE', 'state');
  const validText = v => v === null || (typeof v === 'string' && v.length <= 4096);
  if (['rootDirectory','railwayConfigFile','startCommand'].some(k => Object.hasOwn(s,k) && !validText(s[k])) ||
      (Object.hasOwn(s,'preDeployCommand') && !validText(s.preDeployCommand) &&
       !(Array.isArray(s.preDeployCommand) && s.preDeployCommand.length <= 20 && s.preDeployCommand.every(v => typeof v === 'string' && v.length <= 4096)))) stop('E_CONFIG_SHAPE', 'state');
  const active = s.activeDeployments.map(deployment);
  const latest = s.latestDeployment === null ? null : deployment(s.latestDeployment);
  const autoDeploy = d.serviceInstanceAutoDeployStatus?.enabled;
  if (typeof autoDeploy !== 'boolean') stop('E_AUTO_DEPLOY_SHAPE', 'state');
  const autoDeployCanEnable = d.serviceInstanceAutoDeployStatus?.canEnable;
  if (typeof autoDeployCanEnable !== 'boolean') stop('E_AUTO_DEPLOY_SHAPE', 'state');
  const deploymentTriggers = triggerSummary(d.deploymentTriggers);
  const sourceMatches = s.source.repo === TARGET.repository && s.source.image === null;
  // Diagnostic classification only; historical literal is not identity proof.
  const sourceRepositoryState = s.source.repo === TARGET.repository ? 'CURRENT_GATE' :
    s.source.repo === 'AlphaCenter/gate' ? 'HISTORICAL_GATE_ALIAS' :
    s.source.repo === null || s.source.repo === '' ? 'EMPTY' : typeof s.source.repo === 'string' ? 'OTHER' : 'MALFORMED';
  const sourceImageState = !Object.hasOwn(s.source, 'image') ? 'MISSING' : s.source.image === null ? 'NULL' :
    s.source.image === '' ? 'EMPTY_STRING' : typeof s.source.image === 'string' ? 'CONFIGURED' : 'MALFORMED';
  // The default path and root are documented provider defaults. Accept them
  // only for explicit own NULL fields plus byte-pinned config on the actual
  // observed production SHA. Missing fields never become defaults.
  const sourceProvenance = sourceFilesVerified && lock.reviewedConfiguration?.productionSourceSha === lock.expectedProductionSource &&
    active.length === 1 && active[0].sourceSha === lock.expectedProductionSource;
  const rootDirectoryState = ['', '/'].includes(s.rootDirectory) ? 'ROOT' :
    Object.hasOwn(s, 'rootDirectory') && s.rootDirectory === null && sourceProvenance ? 'ROOT_DEFAULT' : 'UNVERIFIED';
  const configFileState = ['railway.toml','/railway.toml'].includes(s.railwayConfigFile) ? 'RAILWAY_TOML' :
    Object.hasOwn(s, 'railwayConfigFile') && s.railwayConfigFile === null && sourceProvenance ? 'ROOT_RAILWAY_TOML_DEFAULT' : 'UNVERIFIED';
  const usesPinnedConfig = sourceProvenance && ['ROOT','ROOT_DEFAULT'].includes(rootDirectoryState) && ['RAILWAY_TOML','ROOT_RAILWAY_TOML_DEFAULT'].includes(configFileState);
  // Official precedence: environment code > base code > service settings.
  // Reviewed TOML has explicit base start/predeploy and no production override.
  // This is source-backed derivation, not a claim that service override NULL
  // itself disclosed a command. The source blob is included in the receipt.
  const startCommandState = usesPinnedConfig && Object.hasOwn(s, 'startCommand') ? 'FROM_PINNED_RAILWAY_TOML' : commandState(s.startCommand, lock.reviewedStartCommand);
  const preDeployCommandState = usesPinnedConfig && Object.hasOwn(s, 'preDeployCommand') ? 'FROM_PINNED_RAILWAY_TOML' : commandState(s.preDeployCommand, 'alembic upgrade head', true);
  const pendingChanges = pendingState(d);
  const latestMatchesActive = active.length === 1 && latest?.id === active[0].id && JSON.stringify(latest) === JSON.stringify(active[0]);
    const configuration = { sourceMatches, autoDeploy, pendingChanges, startCommandState, preDeployCommandState, configFileState, rootDirectoryState };
  return {
    ...configuration, sourceRepositoryState, sourceImageState, autoDeployCanEnable, deploymentTriggers,
    activeDeploymentCount: active.length, latestMatchesActive,
    productionSourceSha: active.length === 1 ? active[0].sourceSha : null,
    activeDeploymentStatus: active.length === 1 ? active[0].status : null,
    deploymentStopped: active.length === 1 ? active[0].stopped : null,
    configurationSourceSha: usesPinnedConfig ? lock.expectedProductionSource : null,
    railwayTomlBlob: usesPinnedConfig ? lock.reviewedConfiguration.railwayTomlBlob : null,
    // Hash only normalized public enums/identities, never raw command/variables.
    stateDigest: hash(JSON.stringify({ configuration, active, latest })),
    releasePreflightReady: sourceMatches && !autoDeploy && pendingChanges === 'EMPTY_VERIFIED' && latestMatchesActive &&
      active[0]?.sourceSha === lock.expectedProductionSource && active[0]?.status === 'SUCCESS' && active[0]?.stopped === false &&
      ['MATCHES_REVIEWED_SOURCE','FROM_PINNED_RAILWAY_TOML'].includes(startCommandState) &&
      ['MATCHES_REVIEWED_SOURCE','FROM_PINNED_RAILWAY_TOML'].includes(preDeployCommandState) &&
      ['RAILWAY_TOML','ROOT_RAILWAY_TOML_DEFAULT'].includes(configFileState) && ['ROOT','ROOT_DEFAULT'].includes(rootDirectoryState),
  };
}

export function transport({ railwayToken, githubToken, deadline, now = Date.now, fetchFn = fetch }) {
  const credential = value => typeof value === 'string' && value.length >= 20 && value.length <= 512 && !/\s/u.test(value);
  if (!credential(railwayToken) || !credential(githubToken)) stop('E_CREDENTIAL', 'input');
  let requests = 0, providerQueries = 0;
  async function request(url, options, stage) {
    if (!Number.isFinite(deadline) || now() >= deadline) stop('E_EXPIRED', stage);
    if (++requests > 5) stop('E_REQUEST_CAP', stage);
    const expires = Math.min(deadline, now() + 15000), controller = new AbortController();
    const details = {}; let timer, reader, response;
    const timeout = new Promise((_, reject) => { timer = setTimeout(() => {
      controller.abort(); reject(new Stop('E_TIMEOUT', stage, details));
    }, Math.max(1, expires - now())); });
    const check = () => { if (now() >= expires) throw new Stop('E_TIMEOUT', stage, details); };
    try {
      response = await Promise.race([fetchFn(url, { ...options, redirect: 'error', signal: controller.signal }), timeout]);
      details.httpStatus = httpStatus(response.status); check();
      if (response.redirected || (response.url && response.url !== url) || !response.body) throw new Stop('E_HTTP', stage, details);
      reader = response.body.getReader(); const chunks = []; let bytes = 0;
      for (;;) {
        check(); const item = await Promise.race([reader.read(), timeout]); check();
        if (item.done) break;
        if (!(item.value instanceof Uint8Array)) throw new Stop('E_RESPONSE_JSON', stage, details);
        bytes += item.value.byteLength;
        if (bytes > 524288) throw new Stop('E_RESPONSE_SIZE', stage, details);
        chunks.push(Buffer.from(item.value));
      }
      let body;
      try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
      catch { throw new Stop(response.status === 200 ? 'E_RESPONSE_JSON' : 'E_HTTP', stage, details); }
      if (url === API && body?.errors) details.graphqlCodes = graphqlCodes(body.errors);
      if (response.status !== 200) throw new Stop('E_HTTP', stage, details);
      if (url === API && body?.errors) throw new Stop('E_PROVIDER_GRAPHQL', stage, details);
      if (url === API && !object(body?.data)) throw new Stop('E_PROVIDER_SHAPE', stage, details);
      return body;
    } catch (error) {
      if (error instanceof Stop) throw error;
      throw new Stop(error?.name === 'AbortError' || error?.name === 'TimeoutError' ? 'E_TIMEOUT' : 'E_NETWORK', stage, details);
    } finally {
      clearTimeout(timer);
      try { controller.abort(); } catch { /* release the transport on every exit */ }
      // Cleanup must never delay the safe receipt, even if cancellation hangs.
      try { void (reader ? reader.cancel() : response?.body?.cancel())?.catch(() => {}); } catch { /* no raw exception */ }
      try { reader?.releaseLock(); } catch { /* pending read already bounded */ }
    }
  }
  return {
    async main(stage = 'main-before') {
      const d = await request(MAIN, { method: 'GET', headers: { Authorization: `Bearer ${githubToken}`, Accept: 'application/vnd.github+json' } }, stage);
      if (!object(d) || d.ref !== 'refs/heads/main' || !object(d.object) || d.object.type !== 'commit' ||
          typeof d.object.sha !== 'string' || d.object.sha.length !== 40 || !SHA.test(d.object.sha)) stop('E_MAIN_SHAPE', stage);
      return d.object.sha;
    },
    async query(name) {
      if (!Object.hasOwn(QUERIES, name) || ++providerQueries > 3) stop('E_QUERY_CAP', 'query');
      const variables = name === 'auth' ? { projectId: TARGET.projectId } : { project: TARGET.projectId, environment: TARGET.environmentId, service: TARGET.serviceId };
      const d = await request(API, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Project-Access-Token': railwayToken }, body: JSON.stringify({ query: QUERIES[name], variables }) }, name);
      if (d?.errors || !object(d?.data)) stop('E_PROVIDER_SHAPE', name); return d.data;
    },
  };
}

export async function observe({ io, lock, sourceFilesVerified = false }) {
  if (await io.main('main-before') !== lock.expectedCurrentMain) stop('E_MAIN_DRIFT', 'main-before');
  const auth = await io.query('auth');
  if (!UUID.test(auth?.projectToken?.id ?? '') || auth.projectToken.project?.id !== TARGET.projectId ||
      auth.projectToken.environment?.id !== TARGET.environmentId || auth.project?.id !== TARGET.projectId ||
      !auth.project.services?.edges?.some(x => x.node?.id === TARGET.serviceId)) stop('E_SCOPE', 'auth');
  const firstRaw = await io.query('state'), first = projectState(firstRaw, lock, sourceFilesVerified);
  const secondRaw = await io.query('state'), second = projectState(secondRaw, lock, sourceFilesVerified);
  if (await io.main('main-after') !== lock.expectedCurrentMain) stop('E_MAIN_DRIFT', 'main-after');
  // Compare override bytes only in memory. They never enter a receipt/digest.
  const rawConfig = d => JSON.stringify([d.serviceInstance.startCommand, d.serviceInstance.preDeployCommand,
    d.serviceInstance.rootDirectory, d.serviceInstance.railwayConfigFile, d.serviceInstance.source,
    d.serviceInstanceAutoDeployStatus.canEnable,
    [...d.deploymentTriggers.edges].sort((a, b) => a.node.id < b.node.id ? -1 : a.node.id > b.node.id ? 1 : 0).map(({ node: n }) =>
      [n.id,n.projectId,n.environmentId,n.serviceId,n.provider,n.repository,n.branch,n.baseEnvironmentOverrideId])]);
  if (first.stateDigest !== second.stateDigest || rawConfig(firstRaw) !== rawConfig(secondRaw)) stop('E_STATE_DRIFT', 'state-after');
  return { ...second, configurationUnchanged: true };
}

export function assertReceipt(r) {
  const base = ['schemaVersion','operation','result','privateContentExposed'];
  if (!object(r) || r.schemaVersion !== 'agent-ops.gate-release-preflight/v1' ||
      r.operation !== 'READ_ONLY_GATE_RELEASE_PREFLIGHT' || r.privateContentExposed !== false) stop('E_RECEIPT', 'receipt');
  const keys = Object.keys(r).sort().join(',');
  if (r.result === 'STOP') {
    if (keys !== [...base,'diagnostic'].sort().join(',') || !object(r.diagnostic) ||
        !Object.hasOwn(r.diagnostic, 'code') || !Object.hasOwn(r.diagnostic, 'stage') ||
        Object.keys(r.diagnostic).some(k => !['code','stage','httpStatus','graphqlCodes'].includes(k)) ||
        (Object.hasOwn(r.diagnostic, 'httpStatus') && httpStatus(r.diagnostic.httpStatus) === undefined) ||
        (Object.hasOwn(r.diagnostic, 'graphqlCodes') && (!Array.isArray(r.diagnostic.graphqlCodes) ||
          r.diagnostic.graphqlCodes.length < 1 || r.diagnostic.graphqlCodes.length > 8 ||
          new Set(r.diagnostic.graphqlCodes).size !== r.diagnostic.graphqlCodes.length ||
          r.diagnostic.graphqlCodes.some(code => !GRAPHQL_CODES.has(code)))) ||
        !/^E_[A-Z_]{2,48}$/u.test(r.diagnostic.code ?? '') || !/^[a-z][a-z-]{1,32}$/u.test(r.diagnostic.stage ?? '')) stop('E_RECEIPT', 'receipt');
    return r;
  }
  const more = ['observedAt','helperSourceSha','runId','executorSha','runAttempt','currentMainSha','target',
    'sourceMatches','sourceRepositoryState','sourceImageState','autoDeploy','pendingChanges','startCommandState','preDeployCommandState','configFileState','rootDirectoryState',
    'activeDeploymentCount','latestMatchesActive','productionSourceSha','activeDeploymentStatus','deploymentStopped','configurationSourceSha','railwayTomlBlob','stateDigest',
    'releasePreflightReady','configurationUnchanged','autoDeployCanEnable','deploymentTriggers'];
  if (r.result !== 'OBSERVED' || keys !== [...base,...more].sort().join(',') ||
      !Number.isFinite(Date.parse(r.observedAt)) || ![r.helperSourceSha,r.executorSha,r.currentMainSha].every(x => SHA.test(x ?? '')) ||
      !/^[1-9][0-9]{0,19}$/u.test(r.runId ?? '') || r.runAttempt !== 1 || JSON.stringify(r.target) !== JSON.stringify(TARGET) ||
      !Number.isSafeInteger(r.activeDeploymentCount) || r.activeDeploymentCount < 0 || r.activeDeploymentCount > 8 ||
      !['sourceMatches','autoDeploy','autoDeployCanEnable','latestMatchesActive','releasePreflightReady','configurationUnchanged'].every(k => typeof r[k] === 'boolean') ||
      !['CURRENT_GATE','HISTORICAL_GATE_ALIAS','EMPTY','OTHER','MALFORMED'].includes(r.sourceRepositoryState) ||
      !['NULL','EMPTY_STRING','CONFIGURED','MISSING','MALFORMED'].includes(r.sourceImageState) ||
      (r.productionSourceSha !== null && !SHA.test(r.productionSourceSha ?? '')) ||
      (r.activeDeploymentStatus !== null && !/^[A-Z_]{3,32}$/u.test(r.activeDeploymentStatus ?? '')) ||
      (r.deploymentStopped !== null && typeof r.deploymentStopped !== 'boolean') ||
      !['EMPTY_VERIFIED','PENDING','UNVERIFIED'].includes(r.pendingChanges) ||
      !['MATCHES_REVIEWED_SOURCE','FROM_PINNED_RAILWAY_TOML','UNSET_UNVERIFIED','UNEXPECTED'].includes(r.startCommandState) ||
      !['MATCHES_REVIEWED_SOURCE','FROM_PINNED_RAILWAY_TOML','UNSET_UNVERIFIED','UNEXPECTED'].includes(r.preDeployCommandState) ||
      !['RAILWAY_TOML','ROOT_RAILWAY_TOML_DEFAULT','UNVERIFIED'].includes(r.configFileState) ||
      !['ROOT','ROOT_DEFAULT','UNVERIFIED'].includes(r.rootDirectoryState) ||
      (r.configurationSourceSha !== null && !SHA.test(r.configurationSourceSha ?? '')) ||
      (r.railwayTomlBlob !== null && !SHA.test(r.railwayTomlBlob ?? '')) || !/^[a-f0-9]{64}$/u.test(r.stateDigest ?? '')) stop('E_RECEIPT', 'receipt');
  const t = r.deploymentTriggers;
  if (!object(t) || Object.keys(t).sort().join(',') !== 'completeness,count,rows,targetMatches' ||
      !Number.isSafeInteger(t.count) || t.count < 0 || t.count > 8 || t.completeness !== 'COMPLETE' || t.targetMatches !== true ||
      !Array.isArray(t.rows) || t.rows.length !== t.count || t.rows.some(row => !object(row) ||
        Object.keys(row).sort().join(',') !== 'baseEnvironmentOverride,branchState,providerState,repositoryState' ||
        !['CURRENT_GATE','HISTORICAL_GATE_ALIAS','OTHER'].includes(row.repositoryState) ||
        !['MAIN','OTHER'].includes(row.branchState) || !['GITHUB','OTHER'].includes(row.providerState) ||
        !['ABSENT','PRESENT'].includes(row.baseEnvironmentOverride))) stop('E_RECEIPT', 'receipt');
  return r;
}

export async function main(env = process.env, fetchFn = fetch) {
  let receipt = { schemaVersion: 'agent-ops.gate-release-preflight/v1', operation: 'READ_ONLY_GATE_RELEASE_PREFLIGHT', result: 'STOP', privateContentExposed: false };
  try {
    const workspace = resolve(env.GITHUB_WORKSPACE ?? process.cwd());
    const event = JSON.parse(await readFile(env.GITHUB_EVENT_PATH, 'utf8'));
    const lock = JSON.parse(await readFile(resolve(workspace, 'agent-ops.gate-release-preflight.lock.json'), 'utf8'));
    const workflow = await readFile(resolve(workspace, '.github/workflows/gate-release-preflight.yml'));
    const identity = assertCaller(env, event, lock, workflow);
    const sourceFilesVerified = verifySourceFiles(lock, await readFile(resolve(workspace, 'railway.toml')), await readFile(resolve(workspace, 'Dockerfile')));
    try { await readFile(resolve(workspace, 'railway.json')); stop('E_SOURCE_CONFIG', 'source-config'); }
    catch (error) { if (error?.code !== 'ENOENT') throw error; }
    const state = await observe({ io: transport({ railwayToken: env.RAILWAY_PROJECT_TOKEN, githubToken: env.GITHUB_READ_TOKEN,
      deadline: Date.parse(lock.authorizationExpiresAt), fetchFn }), lock, sourceFilesVerified });
    receipt = { ...receipt, result: 'OBSERVED', observedAt: new Date().toISOString(), ...identity,
      runAttempt: 1, currentMainSha: lock.expectedCurrentMain, target: TARGET, ...state };
  } catch (error) {
    receipt.diagnostic = safeDiagnostic(error);
    process.exitCode = 1;
  }
  assertReceipt(receipt);
  const bytes = `${JSON.stringify(receipt)}\n`;
  await writeFile('gate-release-preflight.json', bytes, { mode: 0o600, flag: 'wx' });
  await writeFile('gate-release-preflight.sha256', `sha256:${hash(bytes)}\n`, { mode: 0o600, flag: 'wx' });
  process.stdout.write(`${receipt.result} sha256:${hash(bytes)}\n`);
  return receipt;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();

