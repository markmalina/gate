import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { assertCaller, assertReceipt, safeDiagnostic, verifySourceFiles, observe, projectState, transport as makeTransport, QUERIES, TARGET, Stop } from './preflight.mjs';

const SOURCE = 'a'.repeat(40), EXECUTOR = 'b'.repeat(40), MAIN = 'c'.repeat(40), LKG = 'd'.repeat(40);
const ID = '11111111-1111-4111-8111-111111111111';
const TOKEN = 'synthetic-no-network-token-1234567890';
const transport = options => makeTransport({ deadline: Date.parse('2099-01-01T00:00:00Z'), ...options });
const workflow = Buffer.from(`uses: markmalina/ops/agent-ops/actions/gate-release-preflight@${EXECUTOR}\n`);
const lock = {
  schemaVersion: 'agent-ops.gate-release-preflight-lock/v1', repository: 'markmalina/ops',
  actionPath: 'agent-ops/actions/gate-release-preflight', commit: EXECUTOR,
  workflowSha256: `sha256:${createHash('sha256').update(workflow).digest('hex')}`,
  expectedCurrentMain: MAIN, expectedProductionSource: LKG,
  reviewedStartCommand: 'exec reviewed server', authorizationExpiresAt: '2026-11-04T13:11:00Z',
};
function fixture() {
  return {
    env: {
      GITHUB_EVENT_NAME: 'pull_request', GITHUB_REPOSITORY: TARGET.repository,
      GITHUB_REPOSITORY_ID: String(TARGET.repositoryId), GITHUB_ACTOR_ID: String(TARGET.ownerId),
      GITHUB_ACTOR: 'markmalina', GITHUB_TRIGGERING_ACTOR: 'markmalina', GITHUB_RUN_ATTEMPT: '1',
      GITHUB_REF: 'refs/pull/999/merge',
      GITHUB_WORKFLOW_REF: `${TARGET.repository}/.github/workflows/gate-release-preflight.yml@refs/pull/999/merge`,
      GITHUB_RUN_ID: '123456789', GATE_HELPER_SOURCE_SHA: SOURCE,
      AGENT_OPS_ACTION_REF: EXECUTOR, AGENT_OPS_ACTION_REPOSITORY: 'markmalina/ops',
      AGENT_OPS_GITHUB_ENVIRONMENT: 'gate-production',
    },
    event: {
      action: 'edited', changes: { body: { from: 'PREPARED: no owner command' } },
      repository: { id: TARGET.repositoryId, full_name: TARGET.repository, owner: { id: TARGET.ownerId } },
      sender: { id: TARGET.ownerId, login: 'markmalina' },
      pull_request: { number: 999, state: 'open', body: `/ops-gate-release-preflight expected-head-sha=${SOURCE}`,
        head: { sha: SOURCE, repo: { id: TARGET.repositoryId, full_name: TARGET.repository, fork: false } },
        base: { ref: 'main', repo: { id: TARGET.repositoryId } } },
    },
  };
}
const now = Date.parse('2026-11-04T11:30:00Z');
function state() {
  const deployment = { id: ID, status: 'SUCCESS', deploymentStopped: false, meta: { commitSha: LKG } };
  return {
    environment: { id: TARGET.environmentId, name: 'production', unmergedChangesCount: null },
    environmentStagedChanges: { environmentId: TARGET.environmentId, status: 'STAGED', patch: {} },
    serviceInstance: { serviceId: TARGET.serviceId, environmentId: TARGET.environmentId,
      source: { repo: TARGET.repository, image: null }, startCommand: lock.reviewedStartCommand,
      preDeployCommand: ['alembic upgrade head'], railwayConfigFile: 'railway.toml', rootDirectory: '/',
      latestDeployment: deployment, activeDeployments: [deployment] },
    serviceInstanceAutoDeployStatus: { enabled: false, canEnable: true },
    deploymentTriggers: { edges: [], pageInfo: { hasNextPage: false } },
  };
}
const auth = () => ({ projectToken: { id: ID, project: { id: TARGET.projectId }, environment: { id: TARGET.environmentId } },
  project: { id: TARGET.projectId, services: { edges: [{ node: { id: TARGET.serviceId } }] } } });

test('exact same-repo owner edited command binds immutable head and Ops pin', () => {
  const { env, event } = fixture(); assert.equal(assertCaller(env, event, lock, workflow, now).helperSourceSha, SOURCE);
});
for (const [name, change] of [
  ['old namespace', f => { f.env.GITHUB_REPOSITORY = 'AlphaCenter/gate'; }],
  ['wrong repo immutable id', f => { f.event.repository.id++; }],
  ['fork', f => { f.event.pull_request.head.repo.fork = true; }],
  ['foreign head repository', f => { f.event.pull_request.head.repo.id++; }],
  ['wrong owner', f => { f.event.sender.id++; }],
  ['nonowner actor', f => { f.env.GITHUB_ACTOR_ID = '42'; }],
  ['rerun', f => { f.env.GITHUB_RUN_ATTEMPT = '2'; }],
  ['wrong exact head', f => { f.event.pull_request.head.sha = MAIN; }],
  ['command suffix', f => { f.event.pull_request.body += '\n'; }],
  ['wrong command case', f => { f.event.pull_request.body = f.event.pull_request.body.toUpperCase(); }],
  ['nonbody edit', f => { f.event.changes = { title: { from: 'x' } }; }],
  ['closed PR', f => { f.event.pull_request.state = 'closed'; }],
  ['main instead of PR workflow', f => { f.env.GITHUB_WORKFLOW_REF = `${TARGET.repository}/.github/workflows/gate-release-preflight.yml@refs/heads/main`; }],
  ['wrong action pin', f => { f.env.AGENT_OPS_ACTION_REF = MAIN; }],
  ['wrong protected environment', f => { f.env.AGENT_OPS_GITHUB_ENVIRONMENT = 'other'; }],
]) test(`caller denies ${name}`, () => { const f = fixture(); change(f); assert.throws(() => assertCaller(f.env, f.event, lock, workflow, now), Stop); });
test('expiry and byte-changing workflow fail closed', () => {
  const f = fixture(); assert.throws(() => assertCaller(f.env, f.event, lock, workflow, Date.parse('2026-11-04T13:11:01Z')), /E_EXPIRED/);
  assert.throws(() => assertCaller(f.env, f.event, lock, Buffer.concat([workflow, Buffer.from(' ')]), now), /E_LOCK/);
});
test('ready predicates and NULL-empty-patch proof are explicit', () => assert.equal(projectState(state(), lock).releasePreflightReady, true));
for (const [name, change] of [
  ['autodeploy true', s => { s.serviceInstanceAutoDeployStatus.enabled = true; }],
  ['pending positive', s => { s.environment.unmergedChangesCount = 1; }],
  ['null with missing patch', s => { delete s.environmentStagedChanges; }],
  ['numeric zero with nonempty patch', s => { s.environment.unmergedChangesCount = 0; s.environmentStagedChanges.patch = { sentinel: 'DO_NOT_EXPORT' }; }],
  ['APPLYING empty patch', s => { s.environmentStagedChanges.status = 'APPLYING'; }],
  ['null startup cannot imply inheritance', s => { s.serviceInstance.startCommand = null; }],
  ['null predeploy cannot imply inheritance', s => { s.serviceInstance.preDeployCommand = null; }],
  ['unknown config file', s => { s.serviceInstance.railwayConfigFile = 'other.toml'; }],
  ['foreign repo', s => { s.serviceInstance.source.repo = 'foreign/repository'; }],
  ['pending latest', s => { s.serviceInstance.latestDeployment = { ...s.serviceInstance.latestDeployment, id: '22222222-2222-4222-8222-222222222222', status: 'QUEUED' }; }],
]) test(`projection denies readiness for ${name}`, () => { const s = state(); change(s); assert.equal(projectState(s, lock).releasePreflightReady, false); });
test('all private strings are excluded from projection', () => {
  const s = state(); s.serviceInstance.startCommand = 'DO_NOT_EXPORT_SECRET_PAYLOAD'; s.environmentStagedChanges.patch = { secret: 'DO_NOT_EXPORT_PATCH' };
  const serialized = JSON.stringify(projectState(s, lock)); assert.ok(!serialized.includes('DO_NOT_EXPORT'));
});
test('scope before state; bracketed source and config; exactly five bounded read requests', async () => {
  const ref = { ref: 'refs/heads/main', object: { type: 'commit', sha: MAIN } };
  const calls = []; const responses = [ref, { data: auth() }, { data: state() }, { data: state() }, ref];
  const io = transport({ railwayToken: TOKEN, githubToken: TOKEN, fetchFn: async (url, options) => {
    calls.push({ url, options }); assert.equal(options.redirect, 'error'); return Response.json(responses.shift());
  } });
  const r = await observe({ io, lock }); assert.equal(r.releasePreflightReady, true); assert.equal(calls.length, 5);
  const docs = calls.filter(x => x.options.method === 'POST').map(x => JSON.parse(x.options.body).query);
  assert.equal(docs.length, 3); assert.deepEqual(docs, [QUERIES.auth, QUERIES.state, QUERIES.state]);
  assert.ok(docs.every(x => x.trimStart().startsWith('query ') && !x.includes('mutation') && !x.includes('variablesForServiceDeployment')));
});
test('main identity reads only the exact Git ref, avoiding oversized full commit payload', async () => {
  const calls=[];
  const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async(url,options)=>{
    calls.push(url);assert.equal(options.method,'GET');
    if(url.endsWith('/commits/main'))return Response.json({sha:MAIN,files:[{patch:'x'.repeat(524288)}]});
    assert.equal(url,'https://api.github.com/repos/markmalina/gate/git/ref/heads/main');
    return Response.json({ref:'refs/heads/main',object:{type:'commit',sha:MAIN}});
  }});
  assert.equal(await io.main(),MAIN);assert.equal(calls.length,1);
  assert.ok(!calls[0].includes('/commits/'));
});
test('main ref rejects legacy body, wrong literal ref or object type and malformed lowercase SHA', async () => {
  const good={ref:'refs/heads/main',object:{type:'commit',sha:MAIN}};
  for(const body of [null,[],{sha:MAIN},{...good,ref:'refs/heads/other'},{...good,ref:'refs/tags/main'},
    {...good,object:null},{...good,object:[]},{...good,object:{type:'tag',sha:MAIN}},
    ...[null,undefined,42,[MAIN],MAIN+'\n','C'.repeat(40),'c'.repeat(39),'c'.repeat(41),'g'.repeat(40)].map(sha=>({...good,object:{type:'commit',sha}}))]) {
    let calls=0;const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async()=>{calls++;return Response.json(body);}});
    await assert.rejects(io.main(),e=>e.code==='E_MAIN_SHAPE'&&e.stage==='main-before');assert.equal(calls,1);
  }
});
test('oversized ref responses still stop at the unchanged cap and identify both main phases', async () => {
  for(const phase of ['main-before','main-after']) {
    let mains=0,requests=0;
    const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async(url,options)=>{
      requests++;if(options.method==='POST')return Response.json({data:JSON.parse(options.body).query===QUERIES.auth?auth():state()});
      mains++;const ref={ref:'refs/heads/main',object:{type:'commit',sha:MAIN}};
      return Response.json(phase==='main-before'||mains===2?{...ref,padding:'x'.repeat(524288)}:ref);
    }});
    await assert.rejects(observe({lock,io}),e=>e.code==='E_RESPONSE_SIZE'&&e.stage===phase&&e.httpStatus===200);
    assert.equal(requests,phase==='main-before'?1:5);
  }
});
test('main-after shape failure and all historical/new STOP phase receipts remain explicit', async () => {
  let mains=0;
  const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async(url,options)=>{
    if(options.method==='POST')return Response.json({data:JSON.parse(options.body).query===QUERIES.auth?auth():state()});
    return Response.json({ref:'refs/heads/main',object:{type:++mains===1?'commit':'tag',sha:MAIN}});
  }});
  await assert.rejects(observe({lock,io}),e=>e.code==='E_MAIN_SHAPE'&&e.stage==='main-after');
  for(const stage of ['main','main-before','main-after'])assertReceipt({schemaVersion:'agent-ops.gate-release-preflight/v1',
    operation:'READ_ONLY_GATE_RELEASE_PREFLIGHT',result:'STOP',privateContentExposed:false,
    diagnostic:{code:'E_RESPONSE_SIZE',stage,httpStatus:200}});
});
test('wrong credential target stops before service observation', async () => {
  const calls = []; const bad = auth(); bad.projectToken.project.id = ID;
  await assert.rejects(observe({ lock, io: { main: async () => MAIN, query: async n => { calls.push(n); return bad; } } }), /E_SCOPE/);
  assert.deepEqual(calls, ['auth']);
});
test('source/config drift never yields accepted observation', async () => {
  let n = 0; await assert.rejects(observe({ lock, io: { main: async () => ++n === 1 ? MAIN : SOURCE, query: async q => q === 'auth' ? auth() : state() } }), /E_MAIN_DRIFT/);
  let states = 0; await assert.rejects(observe({ lock, io: { main: async () => MAIN, query: async q => { if (q === 'auth') return auth(); const s = state(); if (++states === 2) s.serviceInstanceAutoDeployStatus.enabled = true; return s; } } }), /E_STATE_DRIFT/);
});
test('network errors are typed, no retries and no provider body leaked', async () => {
  let count = 0; const io = transport({ railwayToken: TOKEN, githubToken: TOKEN, fetchFn: async () => { count++; throw Error('DO_NOT_EXPORT'); } });
  await assert.rejects(io.main(), error => error.code === 'E_NETWORK' && !String(error).includes('DO_NOT_EXPORT')); assert.equal(count, 1);
});
test('malformed/oversized response and HTTP denial never export body', async () => {
  for (const response of [new Response('DO_NOT_EXPORT', { status: 401 }), new Response('DO_NOT_EXPORT'), new Response('x'.repeat(524289))]) {
    const io = transport({ railwayToken: TOKEN, githubToken: TOKEN, fetchFn: async () => response });
    await assert.rejects(io.main(), error => error instanceof Stop && !String(error).includes('DO_NOT_EXPORT'));
  }
});
test('no network request begins after grant expiry', async () => {
  let requests = 0;
  const io = makeTransport({ railwayToken: TOKEN, githubToken: TOKEN, deadline: 42, now: () => 42,
    fetchFn: async () => { requests++; return Response.json({ sha: MAIN }); } });
  await assert.rejects(io.main(), /E_EXPIRED/); assert.equal(requests, 0);
});
test('receipt rejects extra fields/private payload and invalid projection', () => {
  const stopped = { schemaVersion: 'agent-ops.gate-release-preflight/v1', operation: 'READ_ONLY_GATE_RELEASE_PREFLIGHT', result: 'STOP', privateContentExposed: false,
    diagnostic: { code: 'E_INTERNAL', stage: 'local' } };
  assert.equal(assertReceipt(stopped), stopped);
  assert.throws(() => assertReceipt({ ...stopped, providerBody: 'DO_NOT_EXPORT' }), /E_RECEIPT/);
  const r = { schemaVersion: stopped.schemaVersion, operation: stopped.operation, result: 'OBSERVED', privateContentExposed: false,
    observedAt: '2026-11-04T11:30:00Z', helperSourceSha: SOURCE, runId: '123456789', executorSha: EXECUTOR, runAttempt: 1,
    currentMainSha: MAIN, target: TARGET, ...projectState(state(), lock), configurationUnchanged: true };
  assert.equal(assertReceipt(r), r);
  assert.throws(() => assertReceipt({ ...r, autoDeploy: 'false' }), /E_RECEIPT/);
});
test('pinned actual-production TOML supplies documented explicit NULL defaults', () => {
  const s = state();
  for (const k of ['startCommand','preDeployCommand','rootDirectory','railwayConfigFile']) s.serviceInstance[k] = null;
  const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, railwayTomlBlob: EXECUTOR } };
  const p = projectState(s, provenance, true);
  assert.equal(p.releasePreflightReady, true); assert.equal(p.configurationSourceSha, LKG);
  assert.equal(p.startCommandState, 'FROM_PINNED_RAILWAY_TOML'); assert.equal(p.rootDirectoryState, 'ROOT_DEFAULT');
  assert.equal(projectState(s, provenance, false).releasePreflightReady, false);
});
test('main config is not substituted for actual unknown production source', () => {
  const s = state(); s.serviceInstance.startCommand = null;
  s.serviceInstance.latestDeployment.meta.commitSha = MAIN;
  const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, railwayTomlBlob: EXECUTOR } };
  assert.equal(projectState(s, provenance, true).releasePreflightReady, false);
});
test('missing fields remain unverified despite config provenance', () => {
  const s = state(); delete s.serviceInstance.startCommand;
  const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, railwayTomlBlob: EXECUTOR } };
  assert.equal(projectState(s, provenance, true).releasePreflightReady, false);
});
test('code precedence uses pinned TOML but rejects invalid provider shapes', () => {
  const s = state(); s.serviceInstance.startCommand = 'different dashboard override DO_NOT_EXPORT';
  const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, railwayTomlBlob: EXECUTOR } };
  const p = projectState(s, provenance, true); assert.equal(p.releasePreflightReady, true);
  assert.ok(!JSON.stringify(p).includes('DO_NOT_EXPORT'));
  s.serviceInstance.startCommand = { shell: 'INVALID' }; assert.throws(() => projectState(s, provenance, true), /E_CONFIG_SHAPE/);
});
test('override drift is detected even when effective code config takes precedence', async () => {
  let n = 0; const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, railwayTomlBlob: EXECUTOR } };
  await assert.rejects(observe({ lock: provenance, sourceFilesVerified: true, io: { main: async () => MAIN, query: async q => {
    if (q === 'auth') return auth(); const s = state(); s.serviceInstance.startCommand = `override-${++n}`; return s;
  } } }), /E_STATE_DRIFT/);
});
test('runtime verifies source blob pins rather than trusting inheritance booleans', () => {
  const railway = Buffer.from('reviewed railway'), docker = Buffer.from('reviewed Dockerfile');
  const blob = b => createHash('sha1').update(`blob ${b.byteLength}\0`).update(b).digest('hex');
  const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, proposedSourceSha: SOURCE,
    railwayTomlBlob: blob(railway), proposedDockerfileBlob: blob(docker), productionDockerfileBlob: EXECUTOR,
    railwayTomlIdenticalProductionAndProposed: true, preDeployCommand: 'alembic upgrade head', startCommand: lock.reviewedStartCommand,
    productionEnvironmentOverrides: false, alternateRootRailwayJsonExists: false } };
  assert.equal(verifySourceFiles(provenance, railway, docker), true);
  assert.throws(() => verifySourceFiles(provenance, Buffer.from('changed railway'), docker), /E_SOURCE_CONFIG/);
});
test('real environment name is required before TOML override derivation', () => {
  const provenance = { ...lock, reviewedConfiguration: { productionSourceSha: LKG, railwayTomlBlob: EXECUTOR } };
  for (const value of ['staging', 'other', null, undefined]) {
    const s = state(); for (const k of ['startCommand','preDeployCommand','rootDirectory','railwayConfigFile']) s.serviceInstance[k] = null;
    if (value === undefined) delete s.environment.name; else s.environment.name = value;
    assert.throws(() => projectState(s, provenance, true), error => error.code === 'E_ENVIRONMENT_NAME' && !String(error).includes('staging'));
  }
  const s = state(); for (const k of ['startCommand','preDeployCommand','rootDirectory','railwayConfigFile']) s.serviceInstance[k] = null;
  assert.equal(projectState(s, provenance, true).releasePreflightReady, true);
});
test('source discriminator exports enums only and preserves exact source predicate', () => {
  for (const [value, expected] of [[TARGET.repository,'CURRENT_GATE'],['AlphaCenter/gate','HISTORICAL_GATE_ALIAS'],[null,'EMPTY'],['','EMPTY'],['DO_NOT_EXPORT_REPOSITORY','OTHER'],[42,'MALFORMED'],[undefined,'MALFORMED']]) {
    const s=state();s.serviceInstance.source.repo=value;
    const p=projectState(s,lock);
    assert.equal(p.sourceRepositoryState,expected);
    assert.equal(p.sourceMatches,value===TARGET.repository);
    assert.equal(p.releasePreflightReady,value===TARGET.repository);
    assert.ok(!JSON.stringify(p).includes('DO_NOT_EXPORT'));
  }
  for (const [value, expected] of [[null,'NULL'],['','EMPTY_STRING'],['DO_NOT_EXPORT_IMAGE','CONFIGURED'],[{},'MALFORMED'],[undefined,'MALFORMED']]) {
    const s=state();s.serviceInstance.source.image=value;
    const p=projectState(s,lock);
    assert.equal(p.sourceImageState,expected);
    assert.equal(p.sourceMatches,value===null);
    assert.equal(p.releasePreflightReady,value===null);
    assert.ok(!JSON.stringify(p).includes('DO_NOT_EXPORT'));
  }
  const s=state();delete s.serviceInstance.source.image;
  assert.equal(projectState(s,lock).sourceImageState,'MISSING');assert.equal(projectState(s,lock).releasePreflightReady,false);
});
test('strict receipt rejects unknown discriminator or raw source fields', () => {
  const r={schemaVersion:'agent-ops.gate-release-preflight/v1',operation:'READ_ONLY_GATE_RELEASE_PREFLIGHT',result:'OBSERVED',privateContentExposed:false,
    observedAt:'2026-11-04T11:30:00Z',helperSourceSha:SOURCE,runId:'123456789',executorSha:EXECUTOR,runAttempt:1,
    currentMainSha:MAIN,target:TARGET,...projectState(state(),lock),configurationUnchanged:true};
  assertReceipt(r);
  assert.throws(()=>assertReceipt({...r,sourceRepositoryState:'DO_NOT_EXPORT'}),/E_RECEIPT/);
  assert.throws(()=>assertReceipt({...r,sourceImageState:'DO_NOT_EXPORT'}),/E_RECEIPT/);
  assert.throws(()=>assertReceipt({...r,sourceRepository:'DO_NOT_EXPORT'}),/E_RECEIPT/);
});

function trigger(id = ID) {
  return { node: { id, projectId: TARGET.projectId, environmentId: TARGET.environmentId, serviceId: TARGET.serviceId,
    provider: 'github', repository: TARGET.repository, branch: 'main', baseEnvironmentOverrideId: null } };
}
function receipt(projection) {
  return { schemaVersion:'agent-ops.gate-release-preflight/v1', operation:'READ_ONLY_GATE_RELEASE_PREFLIGHT',
    result:'OBSERVED', privateContentExposed:false, observedAt:'2026-11-05T11:00:00Z', helperSourceSha:SOURCE,
    runId:'123456789', executorSha:EXECUTOR, runAttempt:1, currentMainSha:MAIN, target:TARGET,
    ...projection, configurationUnchanged:true };
}
test('diagnostic trigger projection contains bounded enums, never raw identifiers or text', () => {
  const s = state(), a = trigger();
  a.node.repository = 'DO_NOT_EXPORT_REPOSITORY'; a.node.branch = 'DO_NOT_EXPORT_BRANCH';
  a.node.provider = 'DO_NOT_EXPORT_PROVIDER'; a.node.baseEnvironmentOverrideId = ID;
  s.deploymentTriggers.edges = [a];
  const p = projectState(s, lock);
  assert.deepEqual(p.deploymentTriggers, { count:1, completeness:'COMPLETE', targetMatches:true,
    rows:[{repositoryState:'OTHER',branchState:'OTHER',providerState:'OTHER',baseEnvironmentOverride:'PRESENT'}] });
  assert.equal(p.sourceMatches, true); assert.equal(p.releasePreflightReady, true, 'old readiness is not repurposed as trigger admission');
  assert.equal(p.autoDeployCanEnable, true);
  assert.ok(!JSON.stringify(p.deploymentTriggers).includes('DO_NOT_EXPORT'));
  assert.ok(!JSON.stringify(p.deploymentTriggers).includes(ID));
  assertReceipt(receipt(p));
});
test('literal source and legacy alias predicates stay unchanged with trigger diagnosis', () => {
  for (const repo of [TARGET.repository, 'AlphaCenter/gate']) {
    const s=state(); s.serviceInstance.source.repo=repo; s.deploymentTriggers.edges=[trigger()];
    s.deploymentTriggers.edges[0].node.repository=repo;
    const p=projectState(s,lock);
    assert.equal(p.sourceMatches,repo===TARGET.repository); assert.equal(p.releasePreflightReady,repo===TARGET.repository);
    assert.equal(p.deploymentTriggers.rows[0].repositoryState,repo===TARGET.repository?'CURRENT_GATE':'HISTORICAL_GATE_ALIAS');
  }
});
test('canEnable is required boolean evidence and never changes the old release predicate', () => {
  const s=state(); s.serviceInstanceAutoDeployStatus.canEnable=false;
  const p=projectState(s,lock); assert.equal(p.autoDeployCanEnable,false); assert.equal(p.releasePreflightReady,true);
  for(const value of [null,undefined,0,'true',[]]) {
    s.serviceInstanceAutoDeployStatus.canEnable=value;
    assert.throws(()=>projectState(s,lock),/E_AUTO_DEPLOY_SHAPE/);
  }
});
test('trigger page accepts at most eight complete rows, never follows a cursor', () => {
  const s=state(); s.deploymentTriggers.edges=Array.from({length:8},(_,i)=>trigger(`${i+1}1111111-1111-4111-8111-111111111111`));
  assert.equal(projectState(s,lock).deploymentTriggers.count,8);
  s.deploymentTriggers.edges.push(trigger('91111111-1111-4111-8111-111111111111'));
  assert.throws(()=>projectState(s,lock),/E_TRIGGER_INCOMPLETE/);
  s.deploymentTriggers.edges=[];s.deploymentTriggers.pageInfo.hasNextPage=true;
  assert.throws(()=>projectState(s,lock),/E_TRIGGER_INCOMPLETE/);
  assert.match(QUERIES.state,/first: 9/);assert.ok(!QUERIES.state.includes('after:'));
});
test('each trigger must belong to the exact physical target', () => {
  for(const key of ['projectId','environmentId','serviceId']) for(const value of [ID,null,undefined]) {
    const s=state(); const a=trigger();a.node[key]=value;s.deploymentTriggers.edges=[a];
    assert.throws(()=>projectState(s,lock),/E_TRIGGER_TARGET/);
  }
});
test('malformed, duplicate and unbounded trigger metadata fail closed', () => {
  for(const change of [s=>{s.deploymentTriggers=null;},s=>{delete s.deploymentTriggers.pageInfo.hasNextPage;},
    s=>{s.deploymentTriggers.edges=[null];},s=>{s.deploymentTriggers.edges=[trigger(),trigger()];},
    s=>{s.deploymentTriggers.edges[0].node.id=[ID];},s=>{s.deploymentTriggers.edges[0].node.provider='';},
    s=>{s.deploymentTriggers.edges[0].node.branch='x'.repeat(257);},
    s=>{delete s.deploymentTriggers.edges[0].node.baseEnvironmentOverrideId;}]) {
    const s=state();s.deploymentTriggers.edges=[trigger()];change(s);
    assert.throws(()=>projectState(s,lock),/E_TRIGGER_SHAPE/);
  }
});
test('trigger ID and override follow opaque schema types without exposing their values', () => {
  for (const id of ['opaque:trigger/1', 'x'.repeat(256)]) for (const override of [null, '', 'opaque:environment/2', 'x'.repeat(256)]) {
    const s=state(), t=trigger(id); t.node.baseEnvironmentOverrideId=override; s.deploymentTriggers.edges=[t];
    const p=projectState(s,lock);
    assert.equal(p.deploymentTriggers.rows[0].baseEnvironmentOverride,override===null?'ABSENT':'PRESENT');
    assert.ok(!Object.hasOwn(p.deploymentTriggers.rows[0],'id'));
    assert.ok(!Object.hasOwn(p.deploymentTriggers.rows[0],'baseEnvironmentOverrideId'));
    assertReceipt(receipt(p));
  }
});
test('opaque trigger ID and override reject malformed or overlong values and exact duplicate IDs', () => {
  for (const [key,values] of [['id',[null,undefined,1,[],{},'', 'x'.repeat(257)]],
    ['baseEnvironmentOverrideId',[undefined,1,[],{},'x'.repeat(257)]]]) for (const value of values) {
    const s=state(), t=trigger('opaque:trigger/1'); t.node[key]=value; s.deploymentTriggers.edges=[t];
    assert.throws(()=>projectState(s,lock),/E_TRIGGER_SHAPE/);
  }
  const s=state();s.deploymentTriggers.edges=[trigger('opaque:duplicate'),trigger('opaque:duplicate')];
  assert.throws(()=>projectState(s,lock),/E_TRIGGER_SHAPE/);
});
test('distinct opaque IDs use exact canonical ordering across the read bracket', async () => {
  let n=0;const a=trigger('\u00e9'),b=trigger('e\u0301');b.node.branch='other';
  const io={main:async()=>MAIN,query:async name=>{if(name==='auth')return auth();const s=state();
    s.deploymentTriggers.edges=++n===1?[a,b]:[b,a];return s;}};
  assert.equal((await observe({lock,io})).deploymentTriggers.count,2);
});
test('trigger change within the same redacted OTHER enum still stops the bracketed read', async () => {
  let n=0;
  await assert.rejects(observe({lock,io:{main:async()=>MAIN,query:async name=>{
    if(name==='auth')return auth(); const s=state();s.deploymentTriggers.edges=[trigger()];
    s.deploymentTriggers.edges[0].node.branch=`private-branch-${++n}`;return s;
  }}}),/E_STATE_DRIFT/);
});
test('trigger ordering does not create false drift, while canEnable drift does', async () => {
  let n=0; const second=trigger('22222222-2222-4222-8222-222222222222');
  const io={main:async()=>MAIN,query:async name=>{if(name==='auth')return auth();const s=state();
    s.deploymentTriggers.edges=++n===1?[trigger(),second]:[second,trigger()];return s;}};
  assert.equal((await observe({lock,io})).deploymentTriggers.count,2);
  n=0;
  await assert.rejects(observe({lock,io:{main:async()=>MAIN,query:async name=>{
    if(name==='auth')return auth();const s=state();s.serviceInstanceAutoDeployStatus.canEnable=++n===1;return s;
  }}}),/E_STATE_DRIFT/);
});
test('HTTP and GraphQL failures retain only safe diagnostic codes and never retry', async () => {
  for(const status of [200,400,401,403,429,500]) {
    let calls=0;
    const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async()=>{calls++;return Response.json({errors:[
      {message:'DO_NOT_EXPORT',path:[TOKEN],extensions:{code:'FORBIDDEN',traceId:TOKEN}},
      {message:TOKEN,extensions:{code:'DO_NOT_EXPORT_CODE'}}]}, {status});}});
    await assert.rejects(io.query('state'),error=>{
      assert.deepEqual(safeDiagnostic(error),{code:status===200?'E_PROVIDER_GRAPHQL':'E_HTTP',stage:'state',
        httpStatus:status,graphqlCodes:['FORBIDDEN','UNCLASSIFIED']});
      assert.ok(!JSON.stringify(safeDiagnostic(error)).includes('DO_NOT_EXPORT'));
      assertReceipt({schemaVersion:'agent-ops.gate-release-preflight/v1',operation:'READ_ONLY_GATE_RELEASE_PREFLIGHT',
        result:'STOP',privateContentExposed:false,diagnostic:safeDiagnostic(error)});return true;
    });
    assert.equal(calls,1);
  }
});
test('GraphQL diagnostic fields are bounded for malformed and many errors', async () => {
  for(const errors of [[], 'DO_NOT_EXPORT', [null], Array.from({length:100},()=>({extensions:{code:'FORBIDDEN'}}))]) {
    const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async()=>Response.json({errors})});
    await assert.rejects(io.query('state'),error=>{
      const d=safeDiagnostic(error);assert.ok(d.graphqlCodes.length>=1&&d.graphqlCodes.length<=8);
      assert.ok(!JSON.stringify(d).includes('DO_NOT_EXPORT'));return true;
    });
  }
});
test('whole request deadline includes headers, stalled body and nonterminating cancel', async t => {
  t.mock.timers.enable({apis:['setTimeout','Date']});
  let controller, cancelled=0, settled=false, calls=0;
  const response=new Response(new ReadableStream({start(c){controller=c;},cancel(){cancelled++;return new Promise(()=>{});}}),{status:200});
  const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async()=>{
    calls++;await new Promise(resolve=>setTimeout(resolve,10_000));return response;
  }});
  const result=io.query('state').catch(error=>{settled=true;return error;});
  try {
    await new Promise(setImmediate);t.mock.timers.tick(10_000);await new Promise(setImmediate);
    assert.equal(settled,false);t.mock.timers.tick(5_001);await new Promise(setImmediate);
    assert.equal(settled,true,'15s total, not another15s after headers');
    assert.deepEqual(safeDiagnostic(await result),{code:'E_TIMEOUT',stage:'state',httpStatus:200});
    assert.equal(calls,1);assert.equal(cancelled,1);
  } finally {try{controller.close();}catch{}t.mock.timers.reset();}
});
test('a fetch that ignores AbortSignal still reaches the bounded deadline', async t => {
  t.mock.timers.enable({apis:['setTimeout','Date']});
  const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:()=>new Promise(()=>{})});
  const result=io.query('state').catch(error=>error);
  t.mock.timers.tick(15_001);await new Promise(setImmediate);
  assert.deepEqual(safeDiagnostic(await result),{code:'E_TIMEOUT',stage:'state'});t.mock.timers.reset();
});
test('body cap permits exactly512KiB, rejects next byte and validates receipt fields', async () => {
  for(const [size,passes] of [[524288,true],[524289,false]]) {
    const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async()=>new Response(JSON.stringify({data:state()}).padEnd(size,' '))});
    if(passes)assert.equal((await io.query('state')).environment.id,TARGET.environmentId);
    else await assert.rejects(io.query('state'),error=>error.code==='E_RESPONSE_SIZE'&&error.httpStatus===200);
  }
  const r=receipt(projectState(state(),lock));
  for(const bad of [{...r,autoDeployCanEnable:'true'},{...r,deploymentTriggers:{...r.deploymentTriggers,count:1}},
    {...r,deploymentTriggers:{...r.deploymentTriggers,raw:'DO_NOT_EXPORT'}}])assert.throws(()=>assertReceipt(bad),/E_RECEIPT/);
});
test('early body-limit and redirect exits abort transport despite nonterminating cancel', async () => {
  for(const redirected of [false,true]) {
    let signal,cancelled=0;
    const response=new Response(new ReadableStream({start(c){if(!redirected)c.enqueue(new Uint8Array(524289));},
      cancel(){cancelled++;return new Promise(()=>{});}}));
    if(redirected)Object.defineProperty(response,'redirected',{value:true});
    const io=transport({railwayToken:TOKEN,githubToken:TOKEN,fetchFn:async(_url,options)=>{signal=options.signal;return response;}});
    await assert.rejects(io.query('state'),error=>error.code===(redirected?'E_HTTP':'E_RESPONSE_SIZE'));
    assert.equal(signal.aborted,true);assert.equal(cancelled,1);
  }
});

