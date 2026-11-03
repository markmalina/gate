import test from 'node:test';
import assert from 'node:assert/strict';
import {inspect} from './inventory.mjs';
function fixture(repo='markmalina/gate'){
 const ops=repo==='markmalina/ops',id=ops?1331657982:1293746267,sha='a'.repeat(40);
 return{env:{GITHUB_REPOSITORY:repo,GITHUB_EVENT_NAME:'pull_request',GITHUB_RUN_ATTEMPT:'1',GITHUB_ACTOR_ID:'23397294',GITHUB_RUN_ID:'123',GITHUB_SHA:'b'.repeat(40),EXPECTED_HEAD_SHA:sha,GH_TOKEN:'synthetic'},
 event:{action:'synchronize',sender:{id:23397294},repository:{id,full_name:repo,owner:{id:23397294}},pull_request:{number:ops?414:766,head:{repo:{id},ref:ops?'agent/gate736-s1-watch0119':'agent/736-autonomous-watch-queue',sha},base:{repo:{id}}}}};
}
const response=(environments,total_count=environments.length)=>new Response(JSON.stringify({total_count,environments}));
for(const repo of ['markmalina/gate','markmalina/ops'])test('fixed metadata-only route '+repo,async()=>{
 const f=fixture(repo),calls=[];const r=await inspect({...f,fetchFn:async(url,opts)=>{calls.push(url);assert.equal(opts.method,'GET');assert.equal(opts.redirect,'error');return response([{id:1,name:'gate745-isolated-fixture',reviewers:['PRIVATE_SENTINEL']},{id:2,name:'OTHER_PRIVATE_NAME'}]);}});
 assert.equal(r.result,'OBSERVED');assert.deepEqual(r.environments.map(e=>e.presence),['PRESENT','ABSENT']);assert.equal(calls.length,1);assert.equal(r.providerCalls,0);assert(!JSON.stringify(r).includes('PRIVATE'));assert.equal(r.railwayResourceExistence,'NOT_OBSERVED');
});
for(const code of [401,403,404,429,500])test('HTTP '+code+' stays UNKNOWN without retry',async()=>{let calls=0;const r=await inspect({...fixture(),fetchFn:async()=>{calls++;return new Response('secret',{status:code})}});assert.equal(r.result,'UNKNOWN');assert.equal(calls,1);assert(r.environments.every(e=>e.presence==='UNKNOWN'));});
test('complete bounded second page',async()=>{let n=0;const r=await inspect({...fixture(),fetchFn:async()=>++n===1?response(Array.from({length:100},(_,i)=>({id:i+1,name:'irrelevant'+i})),101):response([{id:101,name:'agent-ops-disposable-backup-fixture'}],101)});assert.equal(r.result,'OBSERVED');assert.equal(n,2);assert.equal(r.environments[1].presence,'PRESENT');});
for(const mode of ['too-many','changed-total','duplicate','short-page','malformed'])test('incomplete '+mode,async()=>{let n=0;const r=await inspect({...fixture(),fetchFn:async()=>{n++;if(mode==='too-many')return response([],201);if(mode==='short-page')return response([],1);if(mode==='malformed')return new Response('raw-secret');if(n===1)return response(Array.from({length:100},(_,i)=>({id:i+1,name:'x'+i})),101);return response([{id:mode==='duplicate'?1:101,name:'x'}],mode==='changed-total'?102:101);}});assert.equal(r.result,'UNKNOWN');assert(n<=2);assert(r.environments.every(e=>e.presence==='UNKNOWN'));});
for(const mutation of [f=>f.env.GITHUB_RUN_ATTEMPT='2',f=>f.env.GITHUB_ACTOR_ID='1',f=>f.env.EXPECTED_HEAD_SHA='c'.repeat(40),f=>f.event.pull_request.head.repo.id=1,f=>f.event.pull_request.number=781,f=>f.env.GITHUB_EVENT_NAME='workflow_dispatch'])test('wrong caller never reaches token/network',async()=>{const f=fixture();mutation(f);let calls=0;await assert.rejects(inspect({...f,fetchFn:async()=>{calls++;throw Error()}}));assert.equal(calls,0);});
