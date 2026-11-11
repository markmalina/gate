import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,mkdtemp,copyFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const root=resolve(process.env.S1_WORKSPACE ?? new URL('..',import.meta.url).pathname);
const original=join(root,'ops-source/agent-ops/actions/gate-snapshot-cutover');
const candidate=join(root,'proposed-ops/agent-ops/actions/gate-snapshot-cutover');
const {assertCaller}=await import(pathToFileURL(join(candidate,'caller-context.mjs')));
const legacy=await import(pathToFileURL(join(original,'caller-context.mjs')));
const {assertArtifactProvenance}=await import(pathToFileURL(join(candidate,'artifact-verifier.mjs')));
const {assertOwnerAuthorizationBody,validateProfilePolicy}=await import(pathToFileURL(join(candidate,'profile-policy.mjs')));
const hash=x=>'sha256:'+createHash('sha256').update(x).digest('hex');
const profileBytes=await readFile(join(candidate,'profile.json')),profile=JSON.parse(profileBytes);
const workflow=Buffer.from('synthetic immutable workflow bytes, no executable effects');
const sha='a'.repeat(40),executor='b'.repeat(40),owner={id:23397294,login:'markmalina',type:'User'};
const requester={id:316468440,login:'alphacenter-ops-reviewer[bot]',type:'Bot'};
function context(actor=owner,body='/ops-gate745 observe') {
 const env={GITHUB_REPOSITORY:'markmalina/gate',GITHUB_REPOSITORY_ID:'1293746267',GITHUB_REPOSITORY_OWNER:'markmalina',GITHUB_REPOSITORY_OWNER_ID:'23397294',GITHUB_REF:'refs/heads/main',GITHUB_EVENT_NAME:'issue_comment',GITHUB_RUN_ATTEMPT:'1',GITHUB_SHA:sha,GITHUB_RUN_ID:'123',GITHUB_WORKFLOW_REF:'markmalina/gate/.github/workflows/gate-snapshot-cutover.yml@refs/heads/main',GITHUB_ACTOR:actor.login,GITHUB_TRIGGERING_ACTOR:actor.login,GITHUB_ACTOR_ID:String(actor.id),AGENT_OPS_ACTION_REF:executor,AGENT_OPS_ACTION_REPOSITORY:'markmalina/ops',AGENT_OPS_GITHUB_ENVIRONMENT:'gate-production'};
 const event={action:'created',repository:{id:1293746267,full_name:'markmalina/gate',owner:{id:23397294,login:'markmalina'}},issue:{number:745,repository_url:'https://api.github.com/repos/markmalina/gate'},sender:actor,comment:{user:actor,body,issue_url:'https://api.github.com/repos/markmalina/gate/issues/745',created_at:'2026-10-19T09:00:00Z',updated_at:'2026-10-19T09:00:00Z',performed_via_github_app:actor===requester?{id:4578389}:null}};
 const lock={schemaVersion:'agent-ops.gate-snapshot-lock/v1',repository:'markmalina/ops',actionPath:'agent-ops/actions/gate-snapshot-cutover',commit:executor,profilePath:'agent-ops/actions/gate-snapshot-cutover/profile.json',profileSha256:hash(profileBytes),workflowSha256:hash(workflow)};
 return {env,event,lock};
}
const invoke=c=>assertCaller(c.env,c.event,c.lock,workflow,profileBytes);
test('canonical current caller reproduces E_CALLER (unfixed deployed source)',()=>{
 const c=context();assert.throws(()=>legacy.assertCaller(c.env,c.event,c.lock,workflow,profileBytes),e=>e.code==='E_CALLER');
});
test('proposed pure caller accepts current repository, owner, workflow and original attempt1',()=>{
 assert.deepEqual(invoke(context()),{phase:'observe'});
 assert.deepEqual(invoke(context(requester,'/agent-ops v1 plan operation=gate-snapshot-cutover target=gate-production')),{phase:'plan'});
 assert.equal(invoke(context(owner,'/agent-ops v1 apply operation=gate-snapshot-cutover target=gate-production plan-run=122 plan-artifact=456 plan-digest=sha256:'+'c'.repeat(64))).phase,'apply');
 assert.equal(Object.hasOwn(invoke(context()),'effectAuthority'),false);
});
const changes={
 oldOwner:c=>{c.env.GITHUB_REPOSITORY='AlphaCenter/gate';c.event.repository.full_name='AlphaCenter/gate';},
 oldOwnerId:c=>{c.env.GITHUB_REPOSITORY_OWNER_ID='310893851';c.event.repository.owner.id=310893851;},
 foreignRepository:c=>{c.env.GITHUB_REPOSITORY='markmalina/other';c.event.repository.full_name='markmalina/other';},
 foreignRepositoryId:c=>{c.env.GITHUB_REPOSITORY_ID='1';c.event.repository.id=1;},
 AQUB:c=>{c.env.GITHUB_REPOSITORY='AlphaCenter/aqub';c.event.repository.full_name='AlphaCenter/aqub';},
 forgedActor:c=>{c.event.sender={...c.event.sender,id:1};},
 actorType:c=>{c.event.sender={...c.event.sender,type:'Bot'};},
 triggeringActor:c=>{c.env.GITHUB_TRIGGERING_ACTOR='other';},
 workflow:c=>{c.env.GITHUB_WORKFLOW_REF='markmalina/gate/.github/workflows/other.yml@refs/heads/main';},
 environment:c=>{c.env.AGENT_OPS_GITHUB_ENVIRONMENT='aqub-c1c-production';},
 rerun:c=>{c.env.GITHUB_RUN_ATTEMPT='2';},
 foreignOps:c=>{c.lock.repository='AlphaCenter/ops';},
 profileDigest:c=>{c.lock.profileSha256='sha256:'+'0'.repeat(64);},
 workflowDigest:c=>{c.lock.workflowSha256='sha256:'+'0'.repeat(64);},
 editedCommand:c=>{c.event.comment.updated_at='2026-10-19T09:00:01Z';},
 foreignIssue:c=>{c.event.issue.repository_url='https://api.github.com/repos/markmalina/other';},
 foreignComment:c=>{c.event.comment.issue_url='https://api.github.com/repos/markmalina/other/issues/745';},
 unknownOwner:c=>{delete c.event.repository.owner;},
};
for(const [name,change]of Object.entries(changes))test('proposed rejects '+name+' in pure admission',()=>{const c=context();change(c);assert.throws(()=>invoke(c));});
test('forged App and self approval are rejected',()=>{
 const c=context(requester,'/agent-ops v1 plan operation=gate-snapshot-cutover target=gate-production');c.event.comment.performed_via_github_app.id=1;assert.throws(()=>invoke(c));
 assert.throws(()=>invoke(context(requester,'/ops-gate745 observe')));
 assert.throws(()=>invoke(context(requester,'/agent-ops v1 apply operation=gate-snapshot-cutover target=gate-production plan-run=122 plan-artifact=456 plan-digest=sha256:'+'c'.repeat(64))));
});
test('authorization digest retained; mutable status and foreign issue stay denied',async()=>{
 const oldProfile=JSON.parse(await readFile(join(original,'profile.json')));
 assert.equal(profile.ownerAuthorization.bodyDigest,oldProfile.ownerAuthorization.bodyDigest);
 const sourceRecord=JSON.parse(await readFile(join(original,'owner-authorization.fixture.json')));
 assert.equal(hash(sourceRecord.body),oldProfile.ownerAuthorization.bodyDigest);
 assert.doesNotThrow(()=>assertOwnerAuthorizationBody({...sourceRecord,repository_url:'https://api.github.com/repos/markmalina/gate'},profile));
 assert.throws(()=>assertOwnerAuthorizationBody({...sourceRecord,body:sourceRecord.body+'\nstatus changed',repository_url:'https://api.github.com/repos/markmalina/gate'},profile));
 assert.throws(()=>assertOwnerAuthorizationBody({...sourceRecord,repository_url:'https://api.github.com/repos/markmalina/other'},profile));
 assert.throws(()=>validateProfilePolicy({...profile,ownerAuthorization:{...profile.ownerAuthorization,bodyDigest:'sha256:'+'0'.repeat(64)}}));
});
function artifacts(){
 const repository={id:1293746267,full_name:'markmalina/gate',owner:{id:23397294,login:'markmalina'}};
 const lineage={id:123,repository_id:1293746267,head_repository_id:1293746267,head_sha:sha};
 return {env:{GITHUB_RUN_ID:'123',GITHUB_SHA:sha},workflow:{id:88,path:'.github/workflows/gate-snapshot-cutover.yml'},run:{repository,triggering_actor:owner,path:'.github/workflows/gate-snapshot-cutover.yml',created_at:'2026-10-19T09:00:00Z',id:123,run_attempt:1,event:'issue_comment',actor:owner,head_sha:sha,head_branch:'main',workflow_id:88,status:'in_progress'},receiptId:'101',liveId:'102',receiptArtifact:{created_at:'2026-10-19T09:01:00Z',id:101,workflow_run:{...lineage},expired:false,name:'gate-snapshot-receipt',size_in_bytes:2000},liveArtifact:{created_at:'2026-10-19T09:02:00Z',id:102,workflow_run:{...lineage},expired:false,name:'gate-snapshot-live',size_in_bytes:1000},now:Date.parse('2026-10-19T09:03:00Z')};
}
test('proposed artifact provenance admits exact origin; rejects foreign origin and attempts',()=>{
 assert.doesNotThrow(()=>assertArtifactProvenance(artifacts()));
 for(const change of [p=>p.run.repository.id=1,p=>p.run.repository.full_name='AlphaCenter/gate',p=>p.run.repository.owner.id=310893851,p=>p.run.run_attempt=2,p=>p.run.triggering_actor={id:1,login:'other'},p=>p.run.path='.github/workflows/other.yml',p=>p.receiptArtifact.workflow_run.repository_id=1,p=>p.liveArtifact.workflow_run.head_repository_id=1,p=>p.liveArtifact.workflow_run.head_sha='c'.repeat(40),p=>p.liveArtifact.workflow_run.id=124]){const p=artifacts();change(p);assert.throws(()=>assertArtifactProvenance(p));}
});
test('canonical artifact verifier ignores a foreign repository identity (regression proof)',async()=>{
 const {assertArtifactProvenance:oldVerifier}=await import(pathToFileURL(join(original,'artifact-verifier.mjs')));
 const p=artifacts();p.run.repository.id=1;p.run.repository.full_name='markmalina/other';p.liveArtifact.workflow_run.repository_id=1;
 assert.doesNotThrow(()=>oldVerifier(p));
 assert.throws(()=>assertArtifactProvenance(p));
});
test('checked-in release and fixture stay disabled/UNBOUND; no source authority promotes effects',async()=>{
 const release=JSON.parse(await readFile(join(candidate,'release.json')));assert.equal(release.enabled,false);assert.equal(release.independentSourceReview,'PENDING');assert.equal(release.fixtureAcceptance,'PENDING');assert.equal(release.reviewedExecutorSourceSha,null);assert.equal(release.allocationPolicy,null);
 const fixture=JSON.parse(await readFile(join(root,'proposed-ops/agent-ops/actions/gate745-isolated-fixture/fixture-manifest.json')));assert.equal(fixture.state,'UNBOUND');assert.equal(fixture.enabled,false);
 const {defaultEntry}=await import(pathToFileURL(join(root,'supporting-source/scripts/gate745/fixture-caller.mjs')));assert.throws(defaultEntry,e=>e.code==='E_CALLER_UNBOUND');
});
async function runWithBadOwner(rootDir,patched){
 const dir=await mkdtemp(join(tmpdir(),'s1-hermetic-'));try{
  const action=join(dir,'action');await mkdir(action);await mkdir(join(dir,'.github/workflows'),{recursive:true});
  for(const name of ['action-runner.mjs','gate-snapshot-cutover.mjs','native-cutover-core.mjs','verify-cutover-receipt.mjs','contract.mjs','caller-context.mjs','profile-policy.mjs','profile.json'])await copyFile(join(rootDir,name),join(action,name));
  const c=context();if(!patched){for(const k of ['GITHUB_REPOSITORY','GITHUB_WORKFLOW_REF','AGENT_OPS_ACTION_REPOSITORY'])c.env[k]=c.env[k].replace('markmalina/','AlphaCenter/');c.event.repository.full_name='AlphaCenter/gate';c.lock.repository='AlphaCenter/ops';}
  c.lock.profileSha256=hash(await readFile(join(action,'profile.json')));
  Object.assign(c.env,{GITHUB_WORKSPACE:dir,GITHUB_EVENT_PATH:join(dir,'event.json')});
  await writeFile(c.env.GITHUB_EVENT_PATH,JSON.stringify(c.event));await writeFile(join(dir,'.github/workflows/gate-snapshot-cutover.yml'),workflow);await writeFile(join(dir,'agent-ops.gate-snapshot.lock.json'),JSON.stringify(c.lock));
  const release=JSON.parse(await readFile(join(rootDir,'release.json')));
  // Only a temporary mock release, isolated from every real workflow/provider.
  Object.assign(release,{independentSourceReview:'ACCEPT',reviewedExecutorSourceSha:executor});await writeFile(join(action,'release.json'),JSON.stringify(release));
  let factories=0,providers=0,reads=0;
  const run=(await import(pathToFileURL(join(action,'action-runner.mjs')))).runAction;
  await assert.rejects(run({env:c.env,gateGithubFactory:async()=>{factories++;return {async assertExecutorRelease(){},async resolveMain(){},async assertCandidate(){}};},provider:{async authenticate(){providers++;}},fetchFn:async url=>{reads++;return {ok:true,async json(){return String(url).endsWith('/commits/main')?{sha}:{number:745,user:owner,repository_url:'https://api.github.com/repos/markmalina/gate',body:'wrong authorization digest'};}};}}),e=>e.code==='E_OWNER_AUTHORIZATION');
  return {factories,providers,reads};
 }finally{await rm(dir,{recursive:true,force:true});}
}
test('canonical authorization ordering defect reproduced with mocks; proposed denies before App factory',async()=>{
 assert.deepEqual(await runWithBadOwner(original,false),{factories:1,providers:0,reads:2});
 assert.deepEqual(await runWithBadOwner(candidate,true),{factories:0,providers:0,reads:2});
});
