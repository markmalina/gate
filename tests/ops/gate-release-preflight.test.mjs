// Run the unchanged upstream negative/transport/projection suite in the existing CI glob.
import '../../.github/actions/gate-release-preflight/preflight.test.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { admit, validateEnvelope, verifyBytes, run, UPSTREAM, ACTION, WORKFLOW } from '../../.github/actions/gate-release-preflight/local.mjs';
import { TARGET, QUERIES, Stop, projectState } from '../../.github/actions/gate-release-preflight/preflight.mjs';

const sha = b => createHash('sha256').update(b).digest('hex');
const root = new URL('../../', import.meta.url);
const workflow = await readFile(new URL(WORKFLOW, root));
const lock = JSON.parse(await readFile(new URL('agent-ops.gate-release-preflight.lock.json', root)));
const files = Object.fromEntries(await Promise.all(['action.yml','local.mjs','preflight.mjs','preflight.test.js'].map(async p =>
  [p, await readFile(new URL(`${ACTION}/${p}`, root))])));
const HEAD='a'.repeat(40), MERGE='b'.repeat(40), TREE='c'.repeat(40), LKG=lock.expectedProductionSource;
const now=Date.parse('2026-11-06T20:30:00Z');
function fixture() {
  return { env: { GITHUB_EVENT_NAME:'pull_request', GITHUB_REPOSITORY:TARGET.repository,
    GITHUB_REPOSITORY_ID:String(TARGET.repositoryId), GITHUB_ACTOR_ID:String(TARGET.ownerId),
    GITHUB_ACTOR:'markmalina', GITHUB_TRIGGERING_ACTOR:'markmalina', GITHUB_RUN_ATTEMPT:'1',
    GITHUB_REF:'refs/pull/999/merge', GITHUB_WORKFLOW_REF:`${TARGET.repository}/${WORKFLOW}@refs/pull/999/merge`,
    GITHUB_RUN_ID:'123456789', GATE_HELPER_SOURCE_SHA:HEAD, GITHUB_WORKFLOW_SHA:MERGE, GITHUB_SHA:MERGE,
    GATE_PREFLIGHT_ENVIRONMENT:'gate-production' },
    event: { action:'edited', changes:{body:{from:'ordinary source PR body'}},
      repository:{id:TARGET.repositoryId,full_name:TARGET.repository,owner:{id:TARGET.ownerId}},
      sender:{id:TARGET.ownerId,login:'markmalina'}, pull_request:{number:999,state:'open',
        body:`/ops-gate-release-preflight expected-head-sha=${HEAD}`,head:{sha:HEAD,repo:{id:TARGET.repositoryId,full_name:TARGET.repository,fork:false}},
        base:{ref:'main',sha:lock.expectedCurrentMain,repo:{id:TARGET.repositoryId}}} },
    git:{head:HEAD,tree:TREE,parents:[lock.sourceBase],workflowSha:MERGE,workflowTree:TREE,workflowParents:[lock.sourceBase,HEAD]} };
}
const authorize = f => admit(f.env,f.event,lock,workflow,files,f.git,now);
test('Gate-local caller separates actual executor/workflow from byte-pinned Ops upstream', () => {
  const p=authorize(fixture());assert.equal(p.executorSha,HEAD);assert.equal(p.workflowSha,MERGE);
  assert.equal(p.upstreamSha,UPSTREAM);assert.notEqual(p.executorSha,p.upstreamSha);
});
for(const [name,mutate] of [
  ['ordinary body',f=>{f.event.pull_request.body='ordinary source PR body';}],
  ['opened',f=>{f.event.action='opened';}], ['title edit',f=>{f.event.changes={title:{from:'x'}};}],
  ['wrong owner',f=>{f.event.sender.id++;}],['foreign actor',f=>{f.env.GITHUB_ACTOR_ID='42';}],
  ['foreign head',f=>{f.event.pull_request.head.repo.id++;}], ['fork',f=>{f.event.pull_request.head.repo.fork=true;}],
  ['closed',f=>{f.event.pull_request.state='closed';}],['other base',f=>{f.event.pull_request.base.ref='other';}],
  ['base drift',f=>{f.event.pull_request.base.sha=HEAD;}], ['rerun',f=>{f.env.GITHUB_RUN_ATTEMPT='2';}],
  ['body suffix',f=>{f.event.pull_request.body+='\n';}],['case',f=>{f.event.pull_request.body=f.event.pull_request.body.toUpperCase();}],
  ['head substitution',f=>{f.git.head=MERGE;}],['multiple parents',f=>{f.git.parents.push(HEAD);}],
  ['parent mismatch',f=>{f.git.parents=[HEAD];}], ['workflow context',f=>{f.env.GITHUB_WORKFLOW_SHA=HEAD;}],
  ['workflow tree mismatch',f=>{f.git.workflowTree=HEAD;}],['workflow parent mismatch',f=>{f.git.workflowParents=[HEAD,MERGE];}],
  ['protected environment',f=>{f.env.GATE_PREFLIGHT_ENVIRONMENT='other';}],
]) test(`local admission rejects ${name}`, () => { const f=fixture();mutate(f);assert.throws(()=>authorize(f),Stop); });
test('frozen bytes, source pins and proposed finite window fail closed', () => {
  const f=fixture();
  for(const change of [l=>{l.upstreamCommit=HEAD;},l=>{l.sourceBase=HEAD;},l=>{l.upstreamSourceSha256='sha256:'+sha(Buffer.from('x'));},
    l=>{l.authorizationExpiresAt='2099-01-01T00:00:00Z';},l=>{l.authorizationStartsAt='garbage';}]) {
    const l=structuredClone(lock);change(l);assert.throws(()=>admit(f.env,f.event,l,workflow,files,f.git,now),Stop);
  }
  for(const when of [Date.parse(lock.authorizationStartsAt)-1,Date.parse(lock.authorizationExpiresAt),Date.parse(lock.authorizationExpiresAt)+1])
    assert.throws(()=>admit(f.env,f.event,lock,workflow,files,f.git,when),/E_EXPIRED/);
  assert.throws(()=>admit(f.env,f.event,lock,Buffer.concat([workflow,Buffer.from(' ')]),files,f.git,now),/E_LOCK/);
  assert.throws(()=>admit(f.env,f.event,lock,workflow,{...files,'action.yml':Buffer.from('uses: private/other')},f.git,now),/E_LOCK/);
});
test('workflow keeps local route default OFF and checks source before provider secret binding', () => {
  const s=workflow.toString();assert.match(s,/types: \[edited\]/);assert.match(s,/github.event.changes.body/);
  assert.ok(!/pull_request_target|workflow_dispatch|schedule:|uses:\s*markmalina\/ops\//u.test(s));
  assert.ok(s.indexOf('--check-caller')<s.indexOf('RAILWAY_PROJECT_TOKEN:'));
  assert.match(s,/cancel-in-progress: false/);assert.match(s,/group: gate-production-deploy/);
  assert.match(s,/git -c http.extraheader= fetch --no-tags --depth=1 origin/);
  assert.match(s,/--verify-receipt/);assert.ok(!s.includes('if: false'));
  assert.ok(!files['action.yml'].toString().includes('preflight.mjs"'));
});
test('real wrapper rejects caller before all fetches and writes only sanitized STOP', async () => {
  const dir=await mkdtemp(join(tmpdir(),'gate-local-read-stop-')), previous=process.cwd(), exit=process.exitCode;
  let calls=0;process.chdir(dir);
  try { const e=await run({},async()=>{calls++;throw Error('DO_NOT_EXPORT');},async()=>{throw new Stop('E_CALLER','local');});
    assert.equal(calls,0);assert.equal(e.observation.result,'STOP');validateEnvelope(e);
    assert.equal(e.provenance,null);assert.ok(!JSON.stringify(e).includes('DO_NOT_EXPORT'));
    const bytes=await readFile('gate-release-preflight.json'),d=await readFile('gate-release-preflight.sha256');
    assert.equal(d.toString(),`sha256:${sha(bytes)}\n`);assert.throws(()=>verifyBytes(bytes,d,null,lock.expectedCurrentMain),/E_RECEIPT/);
  } finally {process.exitCode=exit;process.chdir(previous);await rm(dir,{recursive:true,force:true});}
});
test('real wrapper preserves historical alias/staged UNKNOWN; receipt bytes and provenance reject tampering', async () => {
  const dir=await mkdtemp(join(tmpdir(),'gate-local-read-observed-')), previous=process.cwd(), exit=process.exitCode;
  const p=authorize(fixture()), id='11111111-1111-4111-8111-111111111111';
  const deployment={id,status:'SUCCESS',deploymentStopped:false,meta:{commitSha:LKG}};
  const state={environment:{id:TARGET.environmentId,name:'production',unmergedChangesCount:null},environmentStagedChanges:null,
    serviceInstance:{serviceId:TARGET.serviceId,environmentId:TARGET.environmentId,source:{repo:'AlphaCenter/gate',image:null},
      startCommand:null,preDeployCommand:null,railwayConfigFile:null,rootDirectory:null,latestDeployment:deployment,activeDeployments:[deployment]},
    serviceInstanceAutoDeployStatus:{enabled:true,canEnable:false},deploymentTriggers:{edges:[],pageInfo:{hasNextPage:false}}};
  const auth={projectToken:{id,project:{id:TARGET.projectId},environment:{id:TARGET.environmentId}},project:{id:TARGET.projectId,services:{edges:[{node:{id:TARGET.serviceId}}]}}};
  for (const value of [undefined,null,'false',0]) {
    const malformed=structuredClone(state);malformed.serviceInstanceAutoDeployStatus.enabled=value;
    assert.throws(()=>projectState(malformed,lock),/E_AUTO_DEPLOY_SHAPE/);
  }
  const ref={ref:'refs/heads/main',object:{type:'commit',sha:lock.expectedCurrentMain}};
  const responses=[ref,{data:auth},{data:state},{data:state},ref];let calls=0;
  process.chdir(dir);
  try { const e=await run({RAILWAY_PROJECT_TOKEN:'synthetic-token-long-enough',GITHUB_READ_TOKEN:'synthetic-token-long-enough'},async(_url,o)=>{
      calls++;if(o.method==='POST')assert.ok([QUERIES.auth,QUERIES.state].includes(JSON.parse(o.body).query));return Response.json(responses.shift());
    },async()=>({lock:{...lock,authorizationExpiresAt:new Date(Date.now()+60000).toISOString()},provenance:p}));
    assert.equal(calls,5);assert.equal(e.observation.pendingChanges,'UNVERIFIED');assert.equal(e.observation.autoDeploy,true);
    assert.equal(e.observation.sourceRepositoryState,'HISTORICAL_GATE_ALIAS');assert.equal(e.observation.releasePreflightReady,false);
    const bytes=await readFile('gate-release-preflight.json'), d=await readFile('gate-release-preflight.sha256');
    assert.equal(verifyBytes(bytes,d,p,lock.expectedCurrentMain).observation.result,'OBSERVED');
    assert.throws(()=>verifyBytes(Buffer.concat([bytes,Buffer.from(' ')]),d,p,lock.expectedCurrentMain),/E_RECEIPT/);
    assert.throws(()=>verifyBytes(bytes,Buffer.from('sha256:'+'0'.repeat(64)+'\n'),p,lock.expectedCurrentMain),/E_RECEIPT/);
    for(const change of [v=>{v.provenance.executorSha=MERGE;},v=>{v.provenance.upstreamSha=HEAD;},
      v=>{v.observation.currentMainSha=HEAD;},v=>{v.observation.privatePayload='DO_NOT_EXPORT';},v=>{v.provenance.workflowSha=HEAD;}]) {
      const v=structuredClone(e);change(v);const b=Buffer.from(JSON.stringify(v)+'\n');
      assert.throws(()=>verifyBytes(b,Buffer.from(`sha256:${sha(b)}\n`),p,lock.expectedCurrentMain),/E_RECEIPT/);
    }
    const dup=Buffer.from(bytes.toString().replace('{','{"schemaVersion":"discarded",'));
    assert.throws(()=>verifyBytes(dup,Buffer.from(`sha256:${sha(dup)}\n`),p,lock.expectedCurrentMain),/E_RECEIPT/);
    await assert.rejects(run({},async()=>{throw Error('no request');},async()=>{throw new Stop('E_CALLER','local');}),/EEXIST/);
  } finally {process.exitCode=exit;process.chdir(previous);await rm(dir,{recursive:true,force:true});}
});
