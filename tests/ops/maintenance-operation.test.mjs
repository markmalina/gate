import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { createMaintenanceOperation } from "../../.github/actions/gate-deploy/maintenance-operation.mjs";
import { verifyMaintenanceReceipt } from "../../.github/actions/gate-deploy/maintenance-receipt.mjs";
import { createMaintenanceLedger } from "../../.github/actions/gate-deploy/maintenance-ledger.mjs";
import { MAINTENANCE_TARGET as target, MAINTENANCE_CAPS, MAINTENANCE_CONTRACT_SHA256 as contract,
  PROPOSED_WORKFLOW_REF as workflow } from "../../.github/actions/gate-deploy/maintenance-admission.mjs";
const executor = "a".repeat(40), runtime = "b".repeat(40), evidence = "c".repeat(64);
const id = "11111111-1111-4111-8111-111111111111", op = "22222222-2222-4222-8222-222222222222";
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
// Independent JSON replacer implements the public sorted, compact byte contract.
const encode = (value) => Buffer.from(JSON.stringify(value, (key, item) =>
  item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map((name) => [name, item[name]])) : item));
const at = (seconds) => new Date(Date.UTC(2030, 0, 1, 0, 0, seconds)).toISOString();
const clone = (value) => structuredClone(value);
function admission(sourceRepository = "markmalina/gate", patch = {}) {
  const grant = { schemaVersion: "gate.maintenance-owner-request/v1", operation: "GATE_MAINTENANCE_HOLD_V1",
    operationId: op, repositoryId: 1293746267, ownerId: 23397294, repository: "markmalina/gate", target: "gate",
    ...target, expectedDeploymentId: id, expectedSha: runtime, sourceRepository, expectedExecutorSha: executor,
    evidenceSha256: evidence, fullGateDowntime: true, contractSha256: contract,
    notBefore: at(0), expiresAt: at(900), caps: { ...MAINTENANCE_CAPS }, ...patch };
  const grantBytes = encode(grant), command = `/ops-gate-maintenance hold expected-source-sha=${runtime} expected-deployment-id=${id} grant-digest=${sha256(grantBytes)}`;
  const context = { repository: "markmalina/gate", repositoryId: 1293746267, ownerId: 23397294,
    actor: "markmalina", actorId: 23397294, sender: "markmalina", senderId: 23397294,
    eventName: "issue_comment", action: "created", issueNumber: 815, isPullRequest: false,
    ref: "refs/heads/main", runAttempt: 1, workflowRef: workflow, sourceContextSha: executor,
    currentMainSha: executor, checkedOutSha: executor, workflowSha: executor, clientSha: executor,
    executorSha: executor, runId: "33000000000", commentBody: command, evidenceSha256: evidence };
  return { grantBytes, command, context, now: at(700) };
}
const API = "https://api.github.com/repos/markmalina/gate/contents/ops-state/gate-maintenance/v1.json";
const branch = "ops-state/gate-maintenance", path = "ops-state/gate-maintenance/v1.json";
const githubToken = "synthetic-github-token", providerToken = "synthetic-provider-token-different";
const blob = x => createHash("sha1").update(`blob ${Buffer.byteLength(x)}\0`).update(x).digest("hex");
const seed = () => encode({ schemaVersion: "gate.maintenance-ledger/v1", target: { ...target,
  repository: "markmalina/gate", repositoryId: 1293746267 }, identity: null, history: [] }).toString();
function metadata(content) { const sha = blob(content); return { type: "file", path, name: "v1.json", sha,
  size: Buffer.byteLength(content), encoding: "base64", content: Buffer.from(content).toString("base64"), url: API,
  html_url: `https://github.com/markmalina/gate/blob/${branch}/${path}`,
  git_url: `https://api.github.com/repos/markmalina/gate/git/blobs/${sha}` }; }
function response(url, data, status = 200) { const r = Response.json(data, { status }); Object.defineProperty(r,"url",{value:url}); return r; }
function scenario({ repo = "markmalina/gate", stopMode, ledgerMode, oversizedKind, edit = () => {}, startMs = 1000 } = {}) {
  let ms = startMs, content = seed(), stopped = false, commits = 0;
  const calls = [];
  const now = () => new Date(Date.parse(at(0)) + ms).toISOString();
  const githubFetch = async (url, options) => {
    ms++; calls.push(`GH:${options.method}`);
    assert.equal(options.headers.Authorization, `Bearer ${githubToken}`); assert.equal(options.redirect,"error");
    assert.ok(!Object.hasOwn(options.headers,"Project-Access-Token"));
    if (ledgerMode === "missing") return response(url,{},404);
    if(options.method === "GET") { assert.equal(url,`${API}?ref=ops-state%2Fgate-maintenance`); return response(url,metadata(content)); }
    assert.equal(url,API); const p=JSON.parse(options.body); assert.equal(p.branch,branch);
    if(p.sha!==blob(content)) return response(url,{},409);
    const next=Buffer.from(p.content,"base64").toString(), state=JSON.parse(next).history.at(-1).state;
    content=next; commits++;
    if(ledgerMode===`lost:${state}`) throw Error("PRIVATE");
    if(ledgerMode===`expire:${state}`) ms=900000;
    const sha=String(commits).repeat(40);
    return response(url,{content:metadata(content),commit:{sha,url:`https://api.github.com/repos/markmalina/gate/git/commits/${sha}`,html_url:`https://github.com/markmalina/gate/commit/${sha}`}});
  };
  const item = () => ({...target,id,status:"SUCCESS",meta:{commitSha:runtime},deploymentStopped:stopped});
  const providerFetch = async (url, options) => {
    ms++; const {query,variables}=JSON.parse(options.body), kind=query.match(/(?:query|mutation) GateMaintenance(\w+)/u)[1];
    calls.push(kind); assert.equal(url,"https://backboard.railway.com/graphql/v2"); assert.equal(options.redirect,"error");
    assert.equal(options.headers["Project-Access-Token"],providerToken); assert.ok(!Object.hasOwn(options.headers,"Authorization"));
    let data;
    if(kind === oversizedKind) return new Response("x".repeat(524289));
    if(kind==="Stop") {
      assert.deepEqual(variables,{id}); stopped=true;
      if(stopMode==="hang") return new Promise(()=>{});
      if(stopMode==="lost") throw Error("PRIVATE");
      if(stopMode==="timeout") throw new DOMException("PRIVATE","TimeoutError");
      if(stopMode==="expire") {ms=900001;throw new DOMException("PRIVATE","TimeoutError");}
      if(stopMode==="oversized") return new Response("x".repeat(524289));
      if(stopMode==="http503") return Response.json({data:{deploymentStop:true}},{status:503});
      if(stopMode==="errors") return Response.json({data:{deploymentStop:true},errors:[{message:"PRIVATE"}]});
      if(stopMode==="partial") return new Response('{"data":');
      return Response.json({data:{deploymentStop:true}});
    }
    if(kind==="Auth") data={projectToken:{id:op,project:{id:target.projectId},environment:{id:target.environmentId}},
      project:{id:target.projectId,services:{edges:[{node:{id:target.serviceId}}]}}};
    else if(kind==="Fleet") data={environment:{id:target.environmentId,unmergedChangesCount:0},
      environmentStagedChanges:{environmentId:target.environmentId,status:"STAGED",patch:{}},
      serviceInstance:{serviceId:target.serviceId,environmentId:target.environmentId,numReplicas:1,source:{repo,image:null},latestDeployment:item(),activeDeployments:stopped?[]:[item()]},
      serviceInstanceAutoDeployStatus:{enabled:false}};
    else {assert.equal(kind,"Deployment");data={deployment:item()};}
    edit(data,kind,calls,stopped); return Response.json({data});
  };
  const deps={githubToken,providerToken,githubFetch,providerFetch,clock:now,sleep:async n=>{ms+=n;}};
  return {calls,deps,run:(request=admission(repo),originalHeld=null)=>createMaintenanceOperation(deps).run({admissionRequest:request,artifactId:"44000000000",originalHeld}),
    get content(){return content;},set content(v){content=v;},get ms(){return ms;},set ms(v){ms=v;},set stopped(v){stopped=v;}};
}
const checkReceipt = (r,request=admission(),originalHeld=null) => {
  assert.ok(r.receipt,JSON.stringify(r));
  return verifyMaintenanceReceipt({...r.receipt,admissionRequest:{...request,now:JSON.parse(r.receipt.receiptBytes).finishedAt},originalHeld});
};
test("construction/defaults inert and import does not access default environment or transports",async()=>{
  assert.equal((await createMaintenanceOperation().run()).state,"DENIED");
  const r=spawnSync(process.execPath,["--input-type=module","-e",`globalThis.fetch=()=>{throw Error('I/O')}; const {createMaintenanceOperation}=await import('./.github/actions/gate-deploy/maintenance-operation.mjs'); await createMaintenanceOperation().run();`],{encoding:"utf8"});
  assert.equal(r.status,0,r.stderr);
});
test("actual components produce HELD receipt, exact HTTP order, counters and immutable terminal CAS",async()=>{
  const s=scenario(),r=await s.run(); assert.equal(r.state,"HELD_STOPPED");assert.equal(r.success,true);
  const v=checkReceipt(r);assert.equal(v.success,true);assert.equal(v.receipt.counters.providerRequests,14);assert.equal(v.receipt.counters.stop,1);
  assert.equal(r.githubRequests,10);assert.equal(JSON.parse(s.content).history.at(-1).state,"HELD_STOPPED");
  assert.deepEqual(s.calls,["GH:GET","GH:GET","GH:PUT","GH:GET","Auth","Fleet","Deployment","Fleet","GH:GET","GH:PUT","GH:GET",
    "Auth","Fleet","Deployment","Fleet","Stop","Auth","Deployment","Fleet","Deployment","Fleet","GH:GET","GH:PUT","GH:GET"]);
  assert.equal(v.receipt.beforeFleets[0].observedAt,"2030-01-01T00:00:01.013Z");assert.ok(v.receipt.afterFleets[0].sha256);
});
test("same-operation original HELD replay NOOP through read-only actual chain, bytes unchanged",async()=>{
  const s=scenario(),held=await s.run(),bytes=Buffer.from(held.receipt.receiptBytes);s.calls.length=0;s.ms+=100;
  const r=await s.run(admission(),held.receipt);assert.equal(r.state,"NOOP_ALREADY_HELD");assert.equal(r.success,true);
  assert.equal(checkReceipt(r,admission(),held.receipt).receipt.originalReceiptSha256,sha256(bytes));
  assert.deepEqual(s.calls,["GH:GET","Auth","Fleet","Deployment","Fleet"]);assert.deepEqual(held.receipt.receiptBytes,bytes);
});
test("missing/corrupt ledger and bad owner or grant deny before provider/effect",async()=>{
  for(const mode of ["missing","corrupt","owner","grant"]){const s=scenario({ledgerMode:mode}),q=admission();
    if(mode==="corrupt")s.content="{}";if(mode==="owner")q.context.actorId=1;if(mode==="grant")q.grantBytes[0]=0;
    assert.equal((await s.run(q)).success,false);assert.equal(s.calls.filter(x=>!x.startsWith("GH:")).length,0);assert.equal(s.calls.includes("GH:PUT"),false);
  }
});
test("prior UNKNOWN FAILED STOP_INTENT and new operation cannot bypass fixed slot latch",async()=>{
  for(const terminal of ["UNKNOWN_STOP","FAILED","STOP_INTENT"]){const s=scenario(),q=admission();
    const a=createMaintenanceLedger({token:githubToken,fetchFn:s.deps.githubFetch,clock:s.deps.clock});let o=await a.observe();
    const identity={operationId:op,grantDigest:sha256(q.grantBytes),executorSha:executor,runtimeSha:runtime,deploymentId:id};
    o=await a.transition({expected:o.snapshot,identity,to:"CLAIMED"});o=await a.transition({expected:o.snapshot,identity,to:"STOP_INTENT"});
    if(terminal!=="STOP_INTENT")await a.transition({expected:o.snapshot,identity,to:terminal});const before=s.content;
    for(const request of [q,admission("markmalina/gate",{operationId:"33333333-3333-4333-8333-333333333333"})]){s.calls.length=0;const r=await s.run(request);assert.equal(r.state,"DENIED");assert.deepEqual(s.calls,["GH:GET"]);assert.equal(s.content,before);}
  }
});
test("HELD ledger alone or altered original receipt never permits provider reads",async()=>{
  const s=scenario(),h=await s.run();for(const proof of [null,{...h.receipt,receiptBytes:Buffer.from("{}")}]){s.calls.length=0;assert.equal((await s.run(admission(),proof)).state,"DENIED");assert.deepEqual(s.calls,["GH:GET"]);}
});
test("empty fleet without original proof and malformed source/UUID/fleet deny zero stop",async()=>{
  for(const change of [d=>{d.serviceInstance.activeDeployments=[];d.serviceInstance.latestDeployment.deploymentStopped=true;},
    d=>{d.serviceInstance.source.repo="other/repo";},d=>{d.serviceInstance.activeDeployments.push(d.serviceInstance.latestDeployment);},
    d=>{delete d.serviceInstance.activeDeployments;},d=>{d.serviceInstance.latestDeployment.id=op;}]){
    const s=scenario({edit:(d,k)=>{if(k==="Fleet")change(d);}}),r=await s.run();assert.equal(r.state,"FAILED");assert.equal(s.calls.includes("Stop"),false);assert.equal(checkReceipt(r).success,false);
  }
});
test("lost ACK HTTP200 errors partial body and timeout are UNKNOWN with one actual stop and no retry",async()=>{
  for(const stopMode of ["lost","errors","partial","timeout"]){const s=scenario({stopMode}),r=await s.run();assert.equal(r.state,"UNKNOWN_STOP");assert.equal(r.success,false);
    const v=checkReceipt(r);assert.equal(v.receipt.counters.stop,1);assert.equal(v.receipt.ack,false);assert.equal(s.calls.filter(x=>x==="Stop").length,1);
    const n=s.calls.length;assert.equal((await s.run()).state,"DENIED");assert.deepEqual(s.calls.slice(n),["GH:GET"]);
    assert.equal(JSON.stringify(r).includes("PRIVATE"),false);
  }
});
test("post-stop readback failure remains UNKNOWN with truthful prior observed running deployment",async()=>{
  const s=scenario({edit:(d,k,c,stopped)=>{if(stopped&&k==="Deployment")d.deployment.id=op;}}),r=await s.run();assert.equal(r.state,"UNKNOWN_STOP");
  const v=checkReceipt(r);assert.equal(v.receipt.ack,true);assert.equal(v.receipt.stopped,false);assert.equal(v.receipt.observedDeploymentId,id);
});
test("failure before actual exact read retains null observed identity",async()=>{
  const s=scenario({edit:(d,k)=>{if(k==="Auth")d.projectToken.environment.id=op;}}),r=await s.run();
  const v=checkReceipt(r);assert.equal(v.result,"FAILED");assert.equal(v.receipt.deployment,null);assert.equal(v.receipt.observedSha,null);assert.equal(v.receipt.observedAt,null);
});
test("concurrent real coordinators CAS one fixed identity and issue at most one stop",async()=>{
  const s=scenario(),r=await Promise.all([s.run(),s.run()]);assert.equal(r.filter(x=>x.success).length,1);assert.equal(s.calls.filter(x=>x==="Stop").length,1);
});
test("lost durable intent ACK and terminal persistence ACK never claim success or retry",async()=>{
  for(const ledgerMode of ["lost:STOP_INTENT","lost:HELD_STOPPED"]){const s=scenario({ledgerMode}),r=await s.run();assert.equal(r.success,false);
    assert.equal(s.calls.filter(x=>x==="Stop").length,ledgerMode.endsWith("HELD_STOPPED")?1:0);
    assert.equal(r.state,ledgerMode.endsWith("HELD_STOPPED")?"UNKNOWN_STOP":"FAILED");checkReceipt(r);
    const before=s.content;s.calls.length=0;assert.equal((await s.run()).state,"DENIED");assert.deepEqual(s.calls,["GH:GET"]);assert.equal(s.content,before);
  }
});
test("historical source binding preserved end-to-end without canonical relabelling",async()=>{
  const s=scenario({repo:"AlphaCenter/gate"}),r=await s.run();assert.equal(r.success,true);
  const v=checkReceipt(r,admission("AlphaCenter/gate"));assert.equal(v.sourceMatches,false);assert.equal(v.receipt.afterFleets[0].source.repo,"AlphaCenter/gate");
});
test("expiry before stop denies zero actual stop and preserves truthful failure evidence",async()=>{
  const s=scenario({ledgerMode:"expire:STOP_INTENT",startMs:899900}),r=await s.run();assert.equal(r.success,false);assert.equal(s.calls.includes("Stop"),false);checkReceipt(r);
});
test("stop timeout crossing expiry produces truthful retrospective UNKNOWN, never fake finishedAt",async()=>{
  const s=scenario({startMs:899900,stopMode:"expire"}),r=await s.run();assert.equal(r.state,"UNKNOWN_STOP");assert.equal(r.success,false);
  const v=checkReceipt(r);assert.equal(v.receipt.finishedAt,"2030-01-01T00:15:00.001Z");assert.equal(v.receipt.stopAttempted,true);assert.equal(v.receipt.stopped,false);
});
test("exact admission target UUID time and cap drift denies before either HTTP channel",async()=>{
  for(const patch of [{serviceId:op},{expectedDeploymentId:"not-uuid"},{sourceRepository:"other/repo"},
    {notBefore:at(10)},{expiresAt:at(0)},{caps:{...MAINTENANCE_CAPS,stop:2}},{fullGateDowntime:false}]){
    const s=scenario(),r=await s.run(admission("markmalina/gate",patch));assert.equal(r.state,"DENIED");assert.deepEqual(s.calls,[]);
  }
  const s=scenario({startMs:900000});assert.equal((await s.run()).state,"DENIED");assert.deepEqual(s.calls,[]);
});
test("fresh pre-stop drift after durable intent cannot stop or replan",async()=>{
  const s=scenario({edit:(d,k,c)=>{if(k==="Fleet"&&c.filter(x=>x==="Fleet").length===3)d.serviceInstance.numReplicas=2;}});
  const r=await s.run();assert.equal(r.state,"FAILED");assert.equal(s.calls.includes("Stop"),false);checkReceipt(r);
});
test("two different operation IDs still compete for only one fixed slot",async()=>{
  const s=scenario(),q=admission("markmalina/gate",{operationId:"33333333-3333-4333-8333-333333333333"});
  const r=await Promise.all([s.run(),s.run(q)]);assert.equal(r.filter(x=>x.success).length,1);assert.equal(s.calls.filter(x=>x==="Stop").length,1);
});
test("ignored abort at actual HTTP timeout remains UNKNOWN, no retry, real consumer accepts bytes",async(t)=>{
  const s=scenario({stopMode:"hang"});t.mock.timers.enable({apis:["setTimeout"]});const pending=s.run();
  for(let i=0;i<100&&!s.calls.includes("Stop");i++)await new Promise(setImmediate);
  assert.equal(s.calls.includes("Stop"),true);s.ms+=14999;t.mock.timers.tick(15000);const r=await pending;
  assert.equal(r.state,"UNKNOWN_STOP");assert.equal(checkReceipt(r).receipt.counters.maxHttpMs,15000);assert.equal(s.calls.filter(x=>x==="Stop").length,1);
});
test("terminal ledger ACK after expiry cannot return the earlier green receipt",async()=>{
  const s=scenario({startMs:899900,ledgerMode:"expire:HELD_STOPPED"}),r=await s.run();assert.equal(r.state,"UNKNOWN_STOP");assert.equal(r.success,false);
  const v=checkReceipt(r);assert.equal(v.result,"UNKNOWN_STOP");assert.equal(v.receipt.stopped,true);assert.equal(v.receipt.finishedAt,"2030-01-01T00:15:00.000Z");
  assert.equal(s.calls.filter(x=>x==="Stop").length,1);assert.equal(JSON.parse(s.content).history.at(-1).state,"HELD_STOPPED");
});
test("stable complete fleet drift between initial admission and fresh pre-stop chain refuses replan",async()=>{
  const s=scenario({edit:(d,k,c)=>{if(k==="Fleet"&&c.filter(x=>x==="Fleet").length>=3)d.serviceInstance.numReplicas=2;}}),r=await s.run();
  assert.equal(r.state,"FAILED");assert.equal(s.calls.includes("Stop"),false);assert.equal(checkReceipt(r).success,false);
});
test("exact stopped deployment inconsistent with latest refuses replay success",async()=>{
  let drift=false;const s=scenario({edit:(d,k)=>{if(drift&&k==="Deployment")d.deployment.status="FAILED";}}),h=await s.run();drift=true;s.ms+=100;
  const r=await s.run(admission(),h.receipt);assert.equal(r.state,"FAILED");assert.equal(r.success,false);assert.equal(checkReceipt(r).success,false);
});
test("oversized stop ACK preserves truthful exact failure count and safe HTTP in accepted UNKNOWN receipt",async()=>{
  const s=scenario({stopMode:"oversized"}),r=await s.run();assert.equal(r.state,"UNKNOWN_STOP");assert.equal(r.success,false);
  const v=checkReceipt(r);assert.equal(v.receipt.counters.maxBodyBytes,524289);assert.equal(v.receipt.diagnostic.code,"E_BODY");
  assert.equal(v.receipt.diagnostic.category,"TRANSPORT");assert.equal(v.receipt.diagnostic.httpStatus,200);
  assert.equal(v.receipt.counters.stop,1);assert.equal(JSON.parse(s.content).history.at(-1).state,"UNKNOWN_STOP");
  assert.equal(s.calls.filter(x=>x==="Stop").length,1);
});
test("oversized first auth read is truthful FAILED with null observed and zero stop",async()=>{
  const s=scenario({oversizedKind:"Auth"}),r=await s.run();assert.equal(r.state,"FAILED");const v=checkReceipt(r);
  assert.equal(v.receipt.counters.maxBodyBytes,524289);assert.equal(v.receipt.deployment,null);assert.equal(v.receipt.observedSha,null);
  assert.equal(v.receipt.diagnostic.code,"E_BODY");assert.equal(v.receipt.diagnostic.httpStatus,200);assert.equal(s.calls.includes("Stop"),false);
});
test("real transport safe HTTP status is retained without exposing provider payload",async()=>{
  const s=scenario({stopMode:"http503"}),r=await s.run();assert.equal(checkReceipt(r).receipt.diagnostic.httpStatus,503);assert.equal(r.success,false);
});
