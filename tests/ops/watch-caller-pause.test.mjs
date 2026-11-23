import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { PROFILE, admit, execute, transport, assertReceipt, verifyReceipt } from '../../.github/actions/watch-caller-pause/pause.mjs';
const root=new URL('../../.github/actions/watch-caller-pause/',import.meta.url);
const files=Object.fromEntries(await Promise.all(['action.yml','pause.mjs','profile.json'].map(async p=>[p,await readFile(new URL(p,root))])));
const hash=b=>createHash('sha256').update(b).digest('hex');
const HEAD='a'.repeat(40),EXEC='b'.repeat(40),clock=Date.parse('2030-01-01T00:00:00.000Z');
const callerBytes=Buffer.from('synthetic reviewed Mark-owned caller; no private source copied');
function input(){return {env:{GITHUB_EVENT_NAME:'workflow_dispatch',GITHUB_REF:'refs/heads/main',GITHUB_REPOSITORY:'markmalina/mark',
  GITHUB_REPOSITORY_ID:'1336157811',GITHUB_ACTOR_ID:'23397294',GITHUB_ACTOR:'markmalina',GITHUB_TRIGGERING_ACTOR:'markmalina',
  GITHUB_RUN_ATTEMPT:'1',GITHUB_RUN_ID:'123456789',GITHUB_WORKFLOW_REF:`markmalina/mark/${PROFILE.callerPath}@refs/heads/main`,
  GITHUB_SHA:HEAD,GITHUB_WORKFLOW_SHA:HEAD,PAUSE_EXECUTOR_REPOSITORY:'markmalina/gate',PAUSE_EXECUTOR_SHA:EXEC,
  MARK_ACTIONS_TOKEN:'synthetic-Mark-job-token-no-network'},event:{repository:{id:1336157811,full_name:'markmalina/mark',owner:{id:23397294},private:true,fork:false},
  sender:{id:23397294,login:'markmalina'},inputs:{operation:'MARK_CALLER_PAUSE',expected_caller_sha:HEAD,expected_executor_sha:EXEC,
    not_before:'2029-12-31T23:59:59.000Z',expires_at:'2030-01-01T00:05:00.000Z'}},lock:{schemaVersion:'mark.caller-pause-lock/v1',
  executorRepository:'markmalina/gate',executorSha:EXEC,callerWorkflowSha256:hash(callerBytes),executorFilesSha256:Object.fromEntries(Object.entries(files).map(([p,b])=>[p,hash(b)]))},
  callerBytes,files,callerHead:HEAD,profile:PROFILE};}
function fake(o={}) {
  const calls=[];let state=o.disabled?'disabled_manually':'active',puts=0,pass=0;
  const n=o.total??1229;
  const row=i=>({id:i+1,workflow_id:376111707,repository:{id:1336157811},run_attempt:1,status:'completed',conclusion:'success'});
  const fetch=async(url,options)=>{
    calls.push({url,method:options.method});assert.equal(options.redirect,'error');
    assert.ok(url.startsWith('https://api.github.com/repos/markmalina/mark'));
    if(o.failure)return o.failure(url,options,calls);
    const u=new URL(url),path=u.pathname;
    if(options.method==='PUT') {
      puts++;assert.equal(path,'/repos/markmalina/mark/actions/workflows/376111707/disable');
      if(o.lostAck)throw Error('DO_NOT_EXPORT_TOKEN_OR_PAYLOAD');
      if(o.putStatus)return new Response('DO_NOT_EXPORT',{status:o.putStatus});
      state=o.badReadback?'active':'disabled_manually';return new Response(null,{status:204});
    }
    assert.equal(options.method,'GET');
    if(path==='/repos/markmalina/mark')return Response.json({id:o.wrongRepo?42:1336157811,full_name:'markmalina/mark',owner:{id:23397294},private:true,fork:false,archived:false});
    if(path.endsWith('/git/ref/heads/main'))return Response.json({ref:'refs/heads/main',object:{type:'commit',sha:o.mainDrift?EXEC:HEAD}});
    if(path.includes('/contents/'))return Response.json({type:'file',path:PROFILE.workflowPath,sha:o.sourceDrift?EXEC:PROFILE.workflowBlob,encoding:'base64',size:4,content:'ZmFrZQ=='});
    if(path.endsWith('/workflows/376111707'))return Response.json({id:o.wrongWorkflow?42:376111707,path:PROFILE.workflowPath,state:o.workflowState??state});
    assert.ok(path.endsWith('/workflows/376111707/runs'));
    assert.deepEqual([...u.searchParams.keys()].sort(),['page','per_page']);assert.equal(u.searchParams.get('per_page'),'100');
    const page=Number(u.searchParams.get('page'));if(page===1)pass++;
    const total=o.countDrift&&page>1?n+1:n;
    let rows=Array.from({length:Math.min(100,Math.max(0,n-(page-1)*100))},(_,j)=>row((page-1)*100+j));
    if(o.truncate&&page===2)rows=[];
    for(const r of rows) {
      if(r.id===(o.nonterminalAt??101)&&o.status){r.status=o.status;r.conclusion=null;}
      if(o.snapshotDrift&&pass===2&&r.id===n)r.run_attempt=2;
      if(o.duplicate&&page===2&&r.id===101)r.id=1;
      if(o.foreignRun&&r.id===101)r.workflow_id=42;
    }
    const next=page<Math.ceil(n/100)||o.extraNext;
    const headers=next&&!o.missingNext?{link:`<https://api.github.com/repos/markmalina/mark/actions/workflows/376111707/runs?per_page=100&page=${page+1}>; rel="next"`}:{};
    return Response.json({total_count:total,workflow_runs:rows},{headers});
  };
  return {fetch,calls,get puts(){return puts;}};
}
test('fresh owner dispatch binds exact caller/executor and future finite grant',()=>{
  const p=admit(input(),clock);assert.equal(p.callerSha,HEAD);assert.equal(p.executorSha,EXEC);
});
for(const [name,change]of[
  ['Gate token context',x=>{x.env.GITHUB_REPOSITORY='markmalina/gate';}],['wrong repository ID',x=>{x.event.repository.id=42;}],
  ['wrong owner',x=>{x.event.sender.id=42;}],['foreign actor',x=>{x.env.GITHUB_ACTOR_ID='42';}],['rerun',x=>{x.env.GITHUB_RUN_ATTEMPT='2';}],
  ['fork',x=>{x.event.repository.fork=true;}],['PR',x=>{x.event.pull_request={};}],['push CI',x=>{x.env.GITHUB_EVENT_NAME='push';}],
  ['wrong caller head',x=>{x.callerHead=EXEC;}],['wrong workflow source',x=>{x.env.GITHUB_WORKFLOW_SHA=EXEC;}],
  ['wrong caller ref',x=>{x.env.GITHUB_WORKFLOW_REF='other';}],['unknown action ref',x=>{x.env.PAUSE_EXECUTOR_SHA='main';}],
  ['private Ops executor',x=>{x.env.PAUSE_EXECUTOR_REPOSITORY='markmalina/ops';}],['wrong operation/body',x=>{x.event.inputs.operation='ENABLE';}],
  ['extra selector',x=>{x.event.inputs.workflow='other';}],['expired',x=>{x.event.inputs.expires_at='2029-12-31T23:59:59.000Z';}],
  ['not yet valid',x=>{x.event.inputs.not_before='2030-01-01T00:01:00.000Z';}],['overlong grant',x=>{x.event.inputs.expires_at='2099-01-01T00:00:00.000Z';}],
  ['caller bytes',x=>{x.callerBytes=Buffer.from('changed');}],['executor bytes',x=>{x.files={...files,'action.yml':Buffer.from('changed')};}],
  ['wrong fixed profile',x=>{x.profile={...PROFILE,workflowId:42};}],['missing token',x=>{delete x.env.MARK_ACTIONS_TOKEN;}]
])test(`rejects ${name} before all HTTP/PUT`,async()=>{
  const x=input();change(x);const f=fake();const r=await execute(x,f.fetch,()=>clock);
  assert.equal(r.outcome,'STOP');assert.equal(f.calls.length,0);assert.equal(f.puts,0);assertReceipt(r);
});
test('one fixed PUT, disabled readback, two complete 1229-run/13-page passes',async()=>{
  const f=fake(),r=await execute(input(),f.fetch,()=>clock);
  assert.equal(r.outcome,'PAUSED_QUEUE_EMPTY');assert.equal(f.puts,1);assert.equal(r.disabledVerified,true);
  assert.equal(r.coverage.complete,true);assert.equal(r.coverage.stable,true);assert.equal(r.coverage.passes.length,2);
  assert.deepEqual(r.coverage.passes.map(p=>[p.pages,p.rows]),[[13,1229],[13,1229]]);
  assert.equal(r.counts.completed,1229);assert.equal(r.gateInFlightVerified,false);assert.equal(r.watchDriversOffVerified,false);
  assert.equal(f.calls.filter(c=>c.url.includes('/runs?')).length,26);
  assert.ok(!JSON.stringify(r).includes('synthetic-Mark'));assert.ok(!Object.hasOwn(r,'runs'));
});
test('already manually disabled is read-only; zero historical rows still needs two passes',async()=>{
  const f=fake({disabled:true,total:0}),r=await execute(input(),f.fetch,()=>clock);
  assert.equal(r.outcome,'PAUSED_QUEUE_EMPTY');assert.equal(f.puts,0);assert.equal(r.coverage.passes.length,2);
});
test('exact2000 boundary covers40 pages across two passes without filtered search',async()=>{
  const f=fake({total:2000,disabled:true}),r=await execute(input(),f.fetch,()=>clock);
  assert.equal(r.outcome,'PAUSED_QUEUE_EMPTY');assert.equal(f.puts,0);
  assert.deepEqual(r.coverage.passes.map(p=>[p.pages,p.rows]),[[20,2000],[20,2000]]);
});
for(const status of ['queued','in_progress','waiting','pending','requested'])test(`old ${status} beyond latest100 is NOT EMPTY and never cancelled/rearmed`,async()=>{
  const f=fake({status}),r=await execute(input(),f.fetch,()=>clock);
  assert.equal(r.outcome,'DISABLED_QUEUE_NOT_EMPTY');assert.equal(r.counts[status],1);assert.equal(r.coverage.complete,false);
  assert.equal(f.puts,1);assert.ok(f.calls.every(c=>! /enable|cancel|rerun/u.test(c.url)));
});
for(const [name,options,outcome]of[
  ['unknown nonterminal state',{status:'DO_NOT_EXPORT'},'STOP'],['overflow',{total:2001},'INCOMPLETE'],
  ['truncated after100',{truncate:true},'INCOMPLETE'],['missing pagination next',{missingNext:true},'INCOMPLETE'],
  ['extra next on final page',{total:2000,extraNext:true},'INCOMPLETE'],['count drift',{countDrift:true},'INCOMPLETE'],
  ['duplicate ID',{duplicate:true},'INCOMPLETE'],['foreign workflow run',{foreignRun:true},'INCOMPLETE'],
  ['snapshot drift',{snapshotDrift:true},'INCOMPLETE'],['bad readback',{badReadback:true},'STOP'],
  ['unknown workflow state',{workflowState:'disabled_inactivity'},'STOP'],['wrong workflow',{wrongWorkflow:true},'STOP'],
  ['wrong repo',{wrongRepo:true},'STOP'],['main drift',{mainDrift:true},'STOP'],['workflow blob drift',{sourceDrift:true},'STOP'],
  ['PUT permission denied',{putStatus:403},'STOP'],['PUT ambiguous/lost ACK',{lostAck:true},'STOP']
])test(`${name} cannot become empty; no blind retry`,async()=>{
  const f=fake(options),r=await execute(input(),f.fetch,()=>clock);assert.equal(r.outcome,outcome);assert.ok(f.puts<=1);
  assert.equal(r.coverage.complete,false);assert.ok(!JSON.stringify(r).includes('DO_NOT_EXPORT'));
  if(options.lostAck)assert.equal(r.disable.certainty,'unknown');if(options.putStatus)assert.equal(r.disable.httpStatus,403);
});
test('fixed transport rejects arbitrary URL, target, enable/cancel/rerun without HTTP',async()=>{
  let calls=0;const request=transport('synthetic-token-no-network',clock+10000,async()=>{calls++;},()=>clock);
  for(const [p,m]of[['https://evil.invalid','GET'],['/repos/other/mark','GET'],['/repos/markmalina/mark/actions/workflows/42/disable','PUT'],
    ['/repos/markmalina/mark/actions/workflows/376111707/enable','PUT'],['/repos/markmalina/mark/actions/runs/1/cancel','POST'],
    ['/repos/markmalina/mark/actions/workflows/376111707/runs?per_page=100&page=1&status=queued','GET']])
    await assert.rejects(request(p,m),/E_ROUTE/);assert.equal(calls,0);
});
test('HTTP errors redact payload/token and deadline/body/redirect are bounded',async()=>{
  for(const failure of [async()=>new Response('DO_NOT_EXPORT',{status:403}),async()=>new Response('x'.repeat(2097153)),
    async()=>{const r=Response.json({});Object.defineProperty(r,'redirected',{value:true});return r;},async()=>{throw Error('DO_NOT_EXPORT');}]) {
    const f=fake({failure}),r=await execute(input(),f.fetch,()=>clock);assert.equal(r.outcome,'STOP');assert.equal(f.puts,0);
    assert.ok(!JSON.stringify(r).includes('DO_NOT_EXPORT'));
  }
  let calls=0;const request=transport('synthetic-token-no-network',clock,async()=>{calls++;},()=>clock);
  await assert.rejects(request('/repos/markmalina/mark'),/E_DEADLINE/);assert.equal(calls,0);
});
test('fetch ignoring abort still times out without retries',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});let calls=0;
  const request=transport('synthetic-token-no-network',clock+180000,()=>{calls++;return new Promise(()=>{});},()=>clock);
  const p=request('/repos/markmalina/mark');const caught=p.catch(e=>e);t.mock.timers.tick(10001);
  assert.match(String(await caught),/E_TIMEOUT/);assert.equal(calls,1);t.mock.timers.reset();
});
test('strict original-byte receipt consumer rejects digest/provenance/coverage/private tampering',async()=>{
  const r=await execute(input(),fake({total:101}).fetch,()=>clock),p=admit(input(),clock);
  const encode=x=>Buffer.from(JSON.stringify(x)+'\n'),digest=b=>Buffer.from(`sha256:${hash(b)}\n`),b=encode(r);
  assert.equal(verifyReceipt(b,digest(b),p).outcome,'PAUSED_QUEUE_EMPTY');
  assert.throws(()=>verifyReceipt(b,Buffer.from('sha256:'+'0'.repeat(64)+'\n'),p),/E_RECEIPT/);
  for(const change of [x=>{x.provenance.executorSha=HEAD;},x=>{x.provenance.callerSha=EXEC;},x=>{x.provenance.runId='42';},
    x=>{x.coverage.complete=false;},x=>{x.coverage.passes.pop();},x=>{x.coverage.passes[1].snapshotSha256='0'.repeat(64);},
    x=>{x.privateBody='DO_NOT_EXPORT';},x=>{x.target.workflowId=42;},x=>{x.gateInFlightVerified=true;}]) {
    const x=structuredClone(r);change(x);const bytes=encode(x);assert.throws(()=>verifyReceipt(bytes,digest(bytes),p));
  }
  for(const bytes of [Buffer.concat([b,Buffer.from(' ')]),Buffer.from(b.toString().replace('{','{"schemaVersion":"discarded",')),
    Buffer.from([255,254])])assert.throws(()=>verifyReceipt(bytes,digest(bytes),p));
  const stopped=await execute(input(),fake({status:'waiting'}).fetch,()=>clock),bytes=encode(stopped);
  assert.throws(()=>verifyReceipt(bytes,digest(bytes),p),/E_NOT_EMPTY/);
});
test('action takes only the Mark job context token; no Gate workflow or permanent credential input',()=>{
  const s=files['action.yml'].toString();assert.ok(s.includes('MARK_ACTIONS_TOKEN: ${{ github.token }}'));
  assert.ok(!s.includes('inputs:'));assert.ok(!s.includes('secrets.'));assert.match(s,/pause.mjs.*run/);assert.match(s,/pause.mjs.*verify/);
  assert.deepEqual(JSON.parse(files['profile.json']),PROFILE);
});
