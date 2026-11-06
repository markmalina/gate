import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFile, writeFile, open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PROFILE = Object.freeze({ schemaVersion:'mark.caller-pause-profile/v1', repository:'markmalina/mark',
  repositoryId:1336157811, ownerId:23397294, workflowId:376111707,
  workflowPath:'.github/workflows/gate-watch-voice-drain.yml', workflowBlob:'3103cf2855bbfca5a62b19ea646e3f20494674f8',
  workflowSha256:'6a5b4db5e428ca13524eb2140e86298447bb520fa33e24a067aa8f8d7a026d34',
  callerPath:'.github/workflows/gate-watch-caller-pause.yml', pageSize:100, maxPages:20, maxRows:2000,
  requestTimeoutMs:10000, deadlineMs:180000, maxRequests:64, maxResponseBytes:2097152, maxGrantMs:600000 });
const SHA=/^[a-f0-9]{40}$/u, HEX=/^[a-f0-9]{64}$/u, RUN=/^[1-9][0-9]{0,19}$/u;
const BASE='/repos/markmalina/mark', WF=`${BASE}/actions/workflows/376111707`;
const SOURCE=`${BASE}/contents/.github/workflows/gate-watch-voice-drain.yml`;
const ACTIVE=['queued','in_progress','waiting','pending','requested'];
const CONCLUSIONS=['success','failure','neutral','cancelled','timed_out','action_required','skipped','stale','startup_failure'];
const FILES=['action.yml','pause.mjs','profile.json'];
const hash=b=>createHash('sha256').update(b).digest('hex');
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const keys=(x,k)=>object(x)&&Object.keys(x).sort().join(',')===[...k].sort().join(',');
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const positive=x=>Number.isSafeInteger(x)&&x>0;
export class Stop extends Error { constructor(code,stage,httpStatus=null){super(code);this.code=code;this.stage=stage;this.httpStatus=httpStatus;} }
const fail=(code,stage)=>{throw new Stop(code,stage);};

export function admit({env,event,lock,callerBytes,files,callerHead,profile=PROFILE},now=Date.now()) {
  const i=event?.inputs,r=event?.repository;
  if (!same(profile,PROFILE) || env.GITHUB_EVENT_NAME!=='workflow_dispatch'||env.GITHUB_REF!=='refs/heads/main'||
    env.GITHUB_REPOSITORY!==PROFILE.repository||env.GITHUB_REPOSITORY_ID!==String(PROFILE.repositoryId)||
    r?.id!==PROFILE.repositoryId||r.full_name!==PROFILE.repository||r.owner?.id!==PROFILE.ownerId||r.private!==true||r.fork!==false||
    event?.pull_request!==undefined||event.sender?.id!==PROFILE.ownerId||event.sender.login!=='markmalina'||
    env.GITHUB_ACTOR_ID!==String(PROFILE.ownerId)||env.GITHUB_ACTOR!=='markmalina'||env.GITHUB_TRIGGERING_ACTOR!=='markmalina'||
    env.GITHUB_RUN_ATTEMPT!=='1'||!RUN.test(env.GITHUB_RUN_ID??'')||
    env.GITHUB_WORKFLOW_REF!==`${PROFILE.repository}/${PROFILE.callerPath}@refs/heads/main`||
    !keys(i,['operation','expected_caller_sha','expected_executor_sha','not_before','expires_at'])||i.operation!=='MARK_CALLER_PAUSE'||
    !SHA.test(i.expected_caller_sha??'')||i.expected_caller_sha!==env.GITHUB_SHA||i.expected_caller_sha!==callerHead||
    env.GITHUB_WORKFLOW_SHA!==callerHead||env.PAUSE_EXECUTOR_REPOSITORY!=='markmalina/gate'||
    !SHA.test(i.expected_executor_sha??'')||env.PAUSE_EXECUTOR_SHA!==i.expected_executor_sha) fail('E_ADMISSION','admission');
  if (!keys(lock,['schemaVersion','executorRepository','executorSha','callerWorkflowSha256','executorFilesSha256'])||
    lock.schemaVersion!=='mark.caller-pause-lock/v1'||lock.executorRepository!=='markmalina/gate'||lock.executorSha!==i.expected_executor_sha||
    !Buffer.isBuffer(callerBytes)||lock.callerWorkflowSha256!==hash(callerBytes)||
    !keys(lock.executorFilesSha256,FILES)||FILES.some(p=>!Buffer.isBuffer(files[p])||lock.executorFilesSha256[p]!==hash(files[p]))) fail('E_SOURCE','admission');
  const start=Date.parse(i.not_before),end=Date.parse(i.expires_at);
  if (![start,end].every(Number.isFinite)||new Date(start).toISOString()!==i.not_before||new Date(end).toISOString()!==i.expires_at||
    end<=start||end-start>PROFILE.maxGrantMs||now<start||now>=end) fail('E_GRANT','admission');
  return {callerSha:callerHead,executorSha:i.expected_executor_sha,runId:env.GITHUB_RUN_ID,attempt:1,
    grantSha256:hash(JSON.stringify(i)),notBefore:i.not_before,expiresAt:i.expires_at};
}
export async function loadInput(env=process.env) {
  const workspace=resolve(env.GITHUB_WORKSPACE??process.cwd()),action=dirname(fileURLToPath(import.meta.url));
  const files=Object.fromEntries(await Promise.all(FILES.map(async p=>[p,await readFile(resolve(action,p))])));
  const callerHead=execFileSync('git',['rev-parse','HEAD'],{cwd:workspace,encoding:'utf8',stdio:['ignore','pipe','ignore'],timeout:5000,maxBuffer:1024}).trim();
  return {env,event:JSON.parse(await readFile(env.GITHUB_EVENT_PATH,'utf8')),
    lock:JSON.parse(await readFile(resolve(workspace,'.gate/mark-caller-pause.lock.json'),'utf8')),
    callerBytes:await readFile(resolve(workspace,PROFILE.callerPath)),files,callerHead,profile:JSON.parse(files['profile.json'])};
}

export function transport(token,deadline,fetchFn=fetch,now=Date.now) {
  if (typeof token!=='string'||token.length<20||token.length>2048||/\s/u.test(token)) fail('E_TOKEN','admission');
  let requests=0,putUsed=false;
  return async function request(path,method='GET',stage='source') {
    const allowed=path===BASE||path===`${BASE}/git/ref/heads/main`||path===WF||
      new RegExp(`^${SOURCE.replaceAll('.','\\.')}\\?ref=[a-f0-9]{40}$`,'u').test(path)||
      new RegExp(`^${WF}/runs\\?per_page=100&page=([1-9]|1[0-9]|20)$`,'u').test(path);
    if ((method==='PUT'&&(path!==`${WF}/disable`||putUsed))||(method!=='PUT'&&(method!=='GET'||!allowed))) fail('E_ROUTE',stage);
    if (now()>=deadline) fail('E_DEADLINE',stage);
    if (++requests>PROFILE.maxRequests) fail('E_REQUEST_CAP',stage);
    if (method==='PUT') putUsed=true;
    const url='https://api.github.com'+path,controller=new AbortController();let timer,reader,response;
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Stop('E_TIMEOUT',stage));},Math.max(1,Math.min(PROFILE.requestTimeoutMs,deadline-now())));});
    try {
      response=await Promise.race([fetchFn(url,{method,redirect:'error',signal:controller.signal,
        headers:{Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'}}),timeout]);
      if (response.redirected||(response.url&&response.url!==url)) fail('E_REDIRECT',stage);
      if (response.status!==(method==='PUT'?204:200)) throw new Stop('E_HTTP',stage,response.status);
      if (method==='PUT') return {body:null,link:null};
      if (!response.body) fail('E_BODY',stage);reader=response.body.getReader();const chunks=[];let length=0;
      for (;;) {const row=await Promise.race([reader.read(),timeout]);if(row.done)break;
        length+=row.value.byteLength;if(length>PROFILE.maxResponseBytes)fail('E_BODY_LIMIT',stage);chunks.push(Buffer.from(row.value));}
      if(now()>=deadline)fail('E_DEADLINE',stage);
      let body;try{body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));}catch{fail('E_JSON',stage);}
      return {body,link:response.headers.get('link')};
    } catch(e) {if(e instanceof Stop)throw e;throw new Stop('E_NETWORK',stage);}
    finally {clearTimeout(timer);controller.abort();try{void (reader?reader.cancel():response?.body?.cancel())?.catch(()=>{});}catch{}try{reader?.releaseLock();}catch{}}
  };
}
function nextLink(link,page,totalPages) {
  if (link!==null&&(typeof link!=='string'||link.length>4096))fail('E_COVERAGE','queue');
  const matches=[...(link??'').matchAll(/<([^>]+)>;\s*rel="([a-z]+)"/gu)];
  if(link&&matches.map(m=>m[0]).join(', ').replaceAll(' ','')!==link.replaceAll(' ',''))fail('E_COVERAGE','queue');
  const next=matches.filter(m=>m[2]==='next');if(next.length>1)fail('E_COVERAGE','queue');
  if(page<totalPages) {
    if(next.length!==1)fail('E_COVERAGE','queue');const u=new URL(next[0][1]);
    if(u.origin!=='https://api.github.com'||u.pathname!==`${WF}/runs`||u.searchParams.size!==2||
      u.searchParams.get('per_page')!=='100'||u.searchParams.get('page')!==String(page+1))fail('E_COVERAGE','queue');
  } else if(next.length)fail('E_COVERAGE','queue');
}
async function workflow(request,disabled=false) {
  const {body:w}=await request(WF,'GET','workflow');
  if(w?.id!==PROFILE.workflowId||w.path!==PROFILE.workflowPath||!['active','disabled_manually'].includes(w.state))fail('E_WORKFLOW','workflow');
  if(disabled&&w.state!=='disabled_manually')fail('E_DISABLED_READBACK','workflow');return w.state;
}
export async function fullQueue(request) {
  const rows=[],ids=new Set();let total=null,pages=1;
  for(let page=1;page<=pages;page++) {
    const {body:b,link}=await request(`${WF}/runs?per_page=100&page=${page}`,'GET','queue');
    if(!object(b)||!Number.isSafeInteger(b.total_count)||b.total_count<0||b.total_count>PROFILE.maxRows||!Array.isArray(b.workflow_runs))fail('E_COVERAGE','queue');
    if(total===null){total=b.total_count;pages=Math.max(1,Math.ceil(total/100));}
    if(total!==b.total_count||pages>PROFILE.maxPages||b.workflow_runs.length!==Math.min(100,Math.max(0,total-(page-1)*100)))fail('E_COVERAGE','queue');
    nextLink(link,page,pages);
    for(const r of b.workflow_runs) {
      if(!positive(r?.id)||ids.has(r.id)||r.workflow_id!==PROFILE.workflowId||r.repository?.id!==PROFILE.repositoryId||
        !positive(r.run_attempt))fail('E_RUN_SHAPE','queue');ids.add(r.id);
      if(!['completed',...ACTIVE].includes(r.status)||(r.status==='completed'&&!CONCLUSIONS.includes(r.conclusion))||
        (r.status!=='completed'&&r.conclusion!==null))fail('E_RUN_STATE','queue');
      rows.push([r.id,r.status,r.conclusion,r.run_attempt]);
    }
  }
  if(ids.size!==total)fail('E_COVERAGE','queue');rows.sort((a,b)=>a[0]-b[0]);
  const counts=Object.fromEntries(['completed',...ACTIVE].map(s=>[s,rows.filter(r=>r[1]===s).length]));
  return {pages,rows:total,counts,snapshotSha256:hash(JSON.stringify(rows))};
}
const TARGET={repository:'markmalina/mark',repositoryId:1336157811,workflowId:376111707};
function diagnostic(e){return e instanceof Stop?{code:e.code,stage:e.stage,httpStatus:e.httpStatus}:{code:'E_INTERNAL',stage:'local',httpStatus:null};}
export async function execute(input,fetchFn=fetch,now=Date.now) {
  const r={schemaVersion:'mark.caller-pause/v1',operation:'MARK_CALLER_PAUSE',target:TARGET,provenance:null,
    startedAt:new Date(now()).toISOString(),finishedAt:null,outcome:'STOP',disabledVerified:false,
    disable:{attempted:false,certainty:'not_attempted',httpStatus:null},coverage:{complete:false,stable:false,passes:[]},
    counts:null,diagnostic:null,gateInFlightVerified:false,watchDriversOffVerified:false,privateContentExposed:false};
  try {
    const p=admit(input,now());r.provenance=p;
    const request=transport(input.env.MARK_ACTIONS_TOKEN,Math.min(Date.parse(p.expiresAt),now()+PROFILE.deadlineMs),fetchFn,now);
    const main=async()=>{const {body:b}=await request(`${BASE}/git/ref/heads/main`);if(b?.ref!=='refs/heads/main'||b.object?.type!=='commit'||b.object.sha!==p.callerSha)fail('E_MAIN_DRIFT','source');};
    const {body:repo}=await request(BASE);if(repo?.id!==PROFILE.repositoryId||repo.full_name!==PROFILE.repository||repo.owner?.id!==PROFILE.ownerId||repo.private!==true||repo.fork!==false||repo.archived!==false)fail('E_REPOSITORY','source');
    await main();const {body:f}=await request(`${SOURCE}?ref=${p.callerSha}`);
    if(f?.type!=='file'||f.path!==PROFILE.workflowPath||f.sha!==PROFILE.workflowBlob||f.encoding!=='base64'||!Number.isSafeInteger(f.size)||f.size<1||f.size>32768||typeof f.content!=='string'||f.content.length>50000)fail('E_WORKFLOW_SOURCE','source');
    // Authenticated GitHub metadata binds this fixed path at the reviewed
    // caller commit to the immutable reviewed Git blob. No private source copy.
    const state=await workflow(request);await main();
    if(state==='active') {
      r.disable={attempted:true,certainty:'unknown',httpStatus:null};
      try{await request(`${WF}/disable`,'PUT','disable');r.disable={attempted:true,certainty:'confirmed',httpStatus:204};}
      catch(e){if(e instanceof Stop&&e.code==='E_HTTP'){r.disable.certainty='rejected';r.disable.httpStatus=e.httpStatus;}throw e;}
    }
    await workflow(request,true);r.disabledVerified=true;
    for(let pass=0;pass<2;pass++) {
      await workflow(request,true);const q=await fullQueue(request);await workflow(request,true);
      r.coverage.passes.push(q);r.counts=q.counts;
      if(ACTIVE.some(s=>q.counts[s]>0)){r.outcome='DISABLED_QUEUE_NOT_EMPTY';break;}
    }
    await main();
    if(r.outcome!=='DISABLED_QUEUE_NOT_EMPTY') {
      if(!same(r.coverage.passes[0],r.coverage.passes[1]))fail('E_QUEUE_DRIFT','queue');
      r.coverage.complete=true;r.coverage.stable=true;r.outcome='PAUSED_QUEUE_EMPTY';
    }
  } catch(e) {r.diagnostic=diagnostic(e);r.outcome=e instanceof Stop&&['E_COVERAGE','E_QUEUE_DRIFT','E_RUN_SHAPE'].includes(e.code)?'INCOMPLETE':'STOP';}
  r.finishedAt=new Date(now()).toISOString();assertReceipt(r);return r;
}
export function assertReceipt(r,expected) {
  if(!keys(r,['schemaVersion','operation','target','provenance','startedAt','finishedAt','outcome','disabledVerified','disable','coverage','counts','diagnostic','gateInFlightVerified','watchDriversOffVerified','privateContentExposed'])||
    r.schemaVersion!=='mark.caller-pause/v1'||r.operation!=='MARK_CALLER_PAUSE'||!same(r.target,TARGET)||
    r.privateContentExposed!==false||r.gateInFlightVerified!==false||r.watchDriversOffVerified!==false||
    !['STOP','INCOMPLETE','DISABLED_QUEUE_NOT_EMPTY','PAUSED_QUEUE_EMPTY'].includes(r.outcome)||typeof r.disabledVerified!=='boolean'||
    ![r.startedAt,r.finishedAt].every(t=>typeof t==='string'&&Number.isFinite(Date.parse(t))&&new Date(t).toISOString()===t)||Date.parse(r.finishedAt)<Date.parse(r.startedAt))fail('E_RECEIPT','receipt');
  const p=r.provenance;
  if(p!==null&&(!keys(p,['callerSha','executorSha','runId','attempt','grantSha256','notBefore','expiresAt'])||
    ![p.callerSha,p.executorSha].every(x=>SHA.test(x??''))||!RUN.test(p.runId??'')||p.attempt!==1||!HEX.test(p.grantSha256??'')||
    ![p.notBefore,p.expiresAt].every(x=>Number.isFinite(Date.parse(x)))))fail('E_RECEIPT','receipt');
  if(expected&&!same(p,expected))fail('E_RECEIPT','receipt');
  if(!keys(r.disable,['attempted','certainty','httpStatus'])||typeof r.disable.attempted!=='boolean'||
    !['not_attempted','unknown','rejected','confirmed'].includes(r.disable.certainty)||
    (r.disable.attempted&&r.disable.certainty==='not_attempted')||
    (r.disable.certainty==='unknown'&&r.disable.httpStatus!==null)||
    (r.disable.certainty==='rejected'&&(r.disable.httpStatus===null||r.disable.httpStatus===204))||
    (r.disable.certainty==='confirmed'&&(!r.disable.attempted||r.disable.httpStatus!==204))||
    (!r.disable.attempted&&(r.disable.certainty!=='not_attempted'||r.disable.httpStatus!==null))||
    (r.disable.httpStatus!==null&&(!Number.isInteger(r.disable.httpStatus)||r.disable.httpStatus<100||r.disable.httpStatus>599)))fail('E_RECEIPT','receipt');
  const c=r.coverage;if(!keys(c,['complete','stable','passes'])||typeof c.complete!=='boolean'||typeof c.stable!=='boolean'||
    !Array.isArray(c.passes)||c.passes.length>2)fail('E_RECEIPT','receipt');
  const count=x=>keys(x,['completed',...ACTIVE])&&Object.values(x).every(n=>Number.isSafeInteger(n)&&n>=0&&n<=2000);
  for(const q of c.passes)if(!keys(q,['pages','rows','counts','snapshotSha256'])||!positive(q.pages)||q.pages>20||
    !Number.isSafeInteger(q.rows)||q.rows<0||q.rows>2000||!count(q.counts)||Object.values(q.counts).reduce((a,b)=>a+b,0)!==q.rows||
    q.pages!==Math.max(1,Math.ceil(q.rows/100))||
    !HEX.test(q.snapshotSha256??''))fail('E_RECEIPT','receipt');
  if((c.stable!==c.complete)||(c.complete&&(c.passes.length!==2||!same(c.passes[0],c.passes[1]))))fail('E_RECEIPT','receipt');
  if(r.counts!==null&&(!count(r.counts)||!same(r.counts,c.passes.at(-1)?.counts)))fail('E_RECEIPT','receipt');
  if(r.diagnostic!==null&&(!keys(r.diagnostic,['code','stage','httpStatus'])||!/^E_[A-Z_]{2,40}$/u.test(r.diagnostic.code??'')||
    !['admission','source','workflow','queue','disable','local','receipt'].includes(r.diagnostic.stage)||
    (r.diagnostic.httpStatus!==null&&(!Number.isInteger(r.diagnostic.httpStatus)||r.diagnostic.httpStatus<100||r.diagnostic.httpStatus>599))))fail('E_RECEIPT','receipt');
  if(r.outcome==='PAUSED_QUEUE_EMPTY'&&(!p||!r.disabledVerified||r.diagnostic!==null||!c.complete||!c.stable||c.passes.length!==2||
    (r.disable.attempted&&r.disable.certainty!=='confirmed')||Date.parse(r.startedAt)<Date.parse(p.notBefore)||Date.parse(r.finishedAt)>=Date.parse(p.expiresAt)||
    !same(c.passes[0],c.passes[1])||!r.counts||ACTIVE.some(s=>r.counts[s]!==0)))fail('E_RECEIPT','receipt');
  if(r.outcome==='DISABLED_QUEUE_NOT_EMPTY'&&(!p||!r.disabledVerified||!r.counts||!ACTIVE.some(s=>r.counts[s]>0)))fail('E_RECEIPT','receipt');
  return r;
}
export function verifyReceipt(bytes,digestBytes,expected) {
  if(!Buffer.isBuffer(bytes)||bytes.length<1||bytes.length>65536||!Buffer.isBuffer(digestBytes)||
    !digestBytes.equals(Buffer.from(`sha256:${hash(bytes)}\n`)))fail('E_RECEIPT','receipt');
  let r;try{r=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch{fail('E_RECEIPT','receipt');}
  if(!bytes.equals(Buffer.from(JSON.stringify(r)+'\n')))fail('E_RECEIPT','receipt');assertReceipt(r,expected);
  if(r.outcome!=='PAUSED_QUEUE_EMPTY')fail('E_NOT_EMPTY','receipt');return r;
}
async function artifact(name,max) {const f=await open(name,constants.O_RDONLY|constants.O_NOFOLLOW);try{const s=await f.stat();
  if(!s.isFile()||s.size<1||s.size>max)fail('E_RECEIPT','receipt');const b=await f.readFile();if(b.length!==s.size)fail('E_RECEIPT','receipt');return b;}finally{await f.close();}}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try {
    if(process.argv.length===3&&process.argv[2]==='run') {
      let input;try{input=await loadInput();}catch{input={env:{}};}
      const r=await execute(input),bytes=Buffer.from(JSON.stringify(r)+'\n');
      await writeFile('mark-caller-pause.json',bytes,{mode:0o600,flag:'wx'});
      await writeFile('mark-caller-pause.sha256',`sha256:${hash(bytes)}\n`,{mode:0o600,flag:'wx'});
      process.stdout.write(`${r.outcome} sha256:${hash(bytes)}\n`);if(r.outcome!=='PAUSED_QUEUE_EMPTY')process.exitCode=1;
    } else if(process.argv.length===3&&process.argv[2]==='verify') {
      const input=await loadInput();
      verifyReceipt(await artifact('mark-caller-pause.json',65536),await artifact('mark-caller-pause.sha256',80),admit(input));
      process.stdout.write('MARK_CALLER_PAUSE_RECEIPT_VERIFIED\n');
    } else fail('E_ADMISSION','local');
  }catch{process.stderr.write('MARK_CALLER_PAUSE_REJECTED\n');process.exitCode=1;}
}
