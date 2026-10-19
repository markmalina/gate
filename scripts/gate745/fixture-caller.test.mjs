import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {defaultEntry,inspectProposalSource,inspectOriginalEvent,COMMAND,CONTROLLER_SHA} from './fixture-caller.mjs';
const denied=code=>error=>error.code===code;
const runtime=()=>({repository:'AlphaCenter/gate',repositoryId:'1293746267',ownerId:'310893851',
  actor:'markmalina',actorId:'23397294',triggeringActor:'markmalina',eventName:'issue_comment',
  ref:'refs/heads/main',sha:'a'.repeat(40),runId:'12345',runAttempt:'1',
  workflowRef:'AlphaCenter/gate/.github/workflows/gate745-native-fixture.yml@refs/heads/main'});
const event=()=>({action:'created',repository:{id:1293746267,full_name:'AlphaCenter/gate',owner:{id:310893851}},
  sender:{id:23397294,login:'markmalina'},issue:{number:745,repository_url:'https://api.github.com/repos/AlphaCenter/gate'},
  comment:{id:12345,user:{id:23397294,login:'markmalina'},body:COMMAND,
    created_at:'2026-10-12T05:31:00Z',updated_at:'2026-10-12T05:31:00Z'}});
const proposal=()=>{const raw='{"synthetic":"source only"}';return {schemaVersion:'gate745.fixture-proposal/v2',
  state:'BOUND',enabled:true,manifestRaw:raw,manifestRawDigest:'sha256:'+createHash('sha256').update(raw).digest('hex'),
  reviews:[{},{},{},{}]};};
test('original synthetic event inspection returns no effect authority',()=>{
  assert.deepEqual(inspectOriginalEvent(event(),runtime()),{runId:'12345',clientSha:'a'.repeat(40),effectAuthority:false});
});
for(const [key,value] of Object.entries({repository:'AlphaCenter/aqub',repositoryId:'1',ownerId:'1',actor:'attacker',
  actorId:'1',triggeringActor:'attacker',eventName:'workflow_dispatch',ref:'refs/heads/other',sha:'head',
  runId:'0',runAttempt:'2',workflowRef:'untrusted'}))
  test('runtime identity denial '+key,()=>assert.throws(()=>inspectOriginalEvent(event(),{...runtime(),[key]:value}),denied('E_CALLER_RUNTIME')));
for(const [name,mutate] of [
  ['edit',e=>{e.action='edited';}],['other issue',e=>{e.issue.number=751;}],
  ['PR',e=>{e.issue.pull_request={};}],['other repository',e=>{e.issue.repository_url='https://api.github.com/repos/AlphaCenter/aqub';}],
  ['foreign sender',e=>{e.sender.id=1;}],['foreign comment author',e=>{e.comment.user.id=1;}],
  ['command suffix',e=>{e.comment.body=COMMAND+' target=all';}],['comment edit',e=>{e.comment.updated_at='2026-10-12T05:32:00Z';}],
  ['missing comment id',e=>{delete e.comment.id;}],['repository identity',e=>{e.repository.id=1;}],
  ['owner identity',e=>{e.repository.owner.id=1;}],['login alias',e=>{e.sender.login='another';}]
]) test('original event denial '+name,()=>{const e=event();mutate(e);assert.throws(()=>inspectOriginalEvent(e,runtime()),denied('E_CALLER_EVENT'));});
test('source proposal inspection validates bytes without authenticating reviews or effects',()=>{
  const p=proposal();const result=inspectProposalSource(Buffer.from(JSON.stringify(p)));
  assert.equal(result.effectAuthority,false);assert.equal(result.reviewCount,4);assert.equal(result.manifestRawDigest,p.manifestRawDigest);
});
for(const [name,mutate,code] of [
  ['disabled',p=>{p.enabled=false;},'E_CALLER_UNBOUND'],['unbound',p=>{p.state='UNBOUND';},'E_CALLER_UNBOUND'],
  ['true string',p=>{p.enabled='true';},'E_CALLER_UNBOUND'],['extra SQL',p=>{p.sql='SELECT *';},'E_CALLER_PROPOSAL_SHAPE'],
  ['hash mismatch',p=>{p.manifestRaw+='x';},'E_CALLER_PROPOSAL_SHAPE'],['absent reviews',p=>{p.reviews=[];},'E_CALLER_PROPOSAL_SHAPE'],
  ['non-string raw',p=>{p.manifestRaw={};},'E_CALLER_PROPOSAL_SHAPE'],['foreign schema',p=>{p.schemaVersion='unknown';},'E_CALLER_PROPOSAL_SHAPE']
]) test('proposal source denial '+name,()=>{const p=proposal();mutate(p);assert.throws(()=>inspectProposalSource(Buffer.from(JSON.stringify(p))),denied(code));});
test('bounded wire and UTF8 denial',()=>{
  for(const raw of [Buffer.from('null'),Buffer.from('[]'),Buffer.from('{'),Buffer.alloc(65537),Buffer.from([255]),'notbytes'])
    assert.throws(()=>inspectProposalSource(raw),denied('E_CALLER_PROPOSAL_SHAPE'));
});
test('shipped six-key proposal stays UNBOUND without fabricated physical manifest',()=>{
  const raw=readFileSync(new URL('../../.agent-ops/input/fixture-proposal.json',import.meta.url));
  const p=JSON.parse(raw);assert.deepEqual(Object.keys(p).sort(),['schemaVersion','state','enabled','manifestRaw','manifestRawDigest','reviews'].sort());
  assert.equal(p.manifestRaw,null);assert.equal(p.manifestRawDigest,null);assert.deepEqual(p.reviews,[]);
  assert.throws(()=>inspectProposalSource(raw),denied('E_CALLER_UNBOUND'));
});
test('compiled public barrier rejects forged authority without argument or network access',()=>{
  let reads=0,net=0;const evil=new Proxy({},{get(){reads++;throw Error('getter');},ownKeys(){reads++;throw Error('keys');}});
  const prior=globalThis.fetch;globalThis.fetch=()=>{net++;throw Error('network');};
  try {assert.throws(()=>defaultEntry(evil),denied('E_CALLER_UNBOUND'));
    assert.throws(()=>defaultEntry({state:'BOUND',enabled:true,admitted:true}),denied('E_CALLER_UNBOUND'));
    assert.equal(reads,0);assert.equal(net,0);
  } finally {globalThis.fetch=prior;}
});
test('actual CLI cannot enable through environment or command arguments',()=>{
  const file=new URL('./fixture-caller.mjs',import.meta.url);
  const r=spawnSync(process.execPath,[file.pathname,'preflight'],{encoding:'utf8',
    env:{PATH:process.env.PATH,GATE745_ENABLED:'true',GITHUB_EVENT_PATH:'/untrusted/event',GITHUB_OUTPUT:'/untrusted/output'}});
  assert.equal(r.status,1);assert.equal(r.stdout,'');assert.equal(r.stderr,'E_CALLER_UNBOUND\n');
  const mode=spawnSync(process.execPath,[file.pathname,'preflight','--enable'],{encoding:'utf8',env:{PATH:process.env.PATH}});
  assert.equal(mode.status,1);assert.equal(mode.stderr,'E_CALLER_MODE\n');
});
test('workflow retains exact trigger and literal disabled accepted C0 binding',()=>{
  const raw=readFileSync(new URL('../../.github/workflows/gate745-native-fixture.yml',import.meta.url),'utf8');
  assert.match(raw,/issue_comment:\s+types: \[created\]/u);
  assert.ok(raw.includes('uses: AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@'+CONTROLLER_SHA));
  assert.ok(raw.includes("if: ${{ false }}"));assert.ok(raw.includes("github.event.comment.body == '"+COMMAND+"'"));
  assert.doesNotMatch(raw,/workflow_dispatch|secrets:|inherit|inputs:/u);
  assert.match(raw,/persist-credentials: false/u);
});
