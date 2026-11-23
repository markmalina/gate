import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { makeFixedGateMaintenance, MaintenanceTransportError, RAILWAY, TARGETS } from "../../.github/actions/gate-deploy/gate-deploy.mjs";
const sha = "a".repeat(40), id = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222", token = "synthetic-project-token-not-secret";
const target = { ...RAILWAY, serviceId: TARGETS.gate.serviceId };
const item = (stopped = false) => ({ ...target, id, status: "SUCCESS", meta: { commitSha: sha }, deploymentStopped: stopped });
const auth = () => ({ projectToken: { id: other, project: { id: target.projectId }, environment: { id: target.environmentId } },
  project: { id: target.projectId, services: { edges: [{ node: { id: target.serviceId } }] } } });
const fleet = (stopped = false) => ({ environment: { id: target.environmentId, unmergedChangesCount: 0 },
  environmentStagedChanges: { environmentId: target.environmentId, status: "STAGED", patch: {} },
  serviceInstance: { serviceId: target.serviceId, environmentId: target.environmentId, numReplicas: 1,
    source: { repo: "markmalina/gate", image: null }, latestDeployment: item(stopped), activeDeployments: stopped ? [] : [item()] },
  serviceInstanceAutoDeployStatus: { enabled: false } });
function fake({ edit = () => {}, stop, stopped = false, clock, sleep } = {}) {
  const calls = [];
  let stopCount = 0;
  const provider = makeFixedGateMaintenance({ token, clock, sleep, fetchFn: async (url, options) => {
    assert.equal(url, "https://backboard.railway.com/graphql/v2");
    assert.equal(options.redirect, "error"); assert.equal(options.method, "POST");
    assert.equal(options.headers["Project-Access-Token"], token);
    assert.ok(!Object.hasOwn(options.headers, "Authorization"));
    const { query, variables } = JSON.parse(options.body);
    const kind = query.match(/(?:query|mutation) GateMaintenance(\w+)/u)[1];
    calls.push(kind);
    if (kind === "Stop") {
      stopCount++; assert.deepEqual(variables, { id });
      return stop ? stop(options) : Response.json({ data: { deploymentStop: true } });
    }
    let data;
    if (kind === "Auth") { assert.deepEqual(variables, { projectId: target.projectId }); data = auth(); }
    else if (kind === "Fleet") { assert.deepEqual(variables, target); data = fleet(stopped); }
    else { assert.equal(kind, "Deployment"); assert.deepEqual(variables, { id }); data = { deployment: item(stopped) }; }
    edit(data, kind, calls);
    return Response.json({ data });
  } });
  return { provider, calls, stops: () => stopCount };
}
const stopInput = { deploymentId: id, expectedSha: sha };
const unknown = (error) => error instanceof MaintenanceTransportError && error.uncertain === true &&
  error.result === "UNKNOWN_STOP" && error.retryable === false && !JSON.stringify(error).includes("PRIVATE");

test("import and construction never touch fetch or credentials", () => {
  const moduleUrl = pathToFileURL(resolve(".github/actions/gate-deploy/gate-deploy.mjs")).href;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", `globalThis.fetch=()=>{throw Error('network')}; const m=await import(${JSON.stringify(moduleUrl)}); m.makeFixedGateMaintenance({});`], { encoding: "utf8", env: { PATH: process.env.PATH } });
  assert.equal(result.status, 0, result.stderr);
});
test("strict input syntax fails before HTTP including object/array coercion", async () => {
  const f = fake();
  for (const expectedSha of [undefined, null, [], [sha], "A".repeat(40), sha.slice(1)]) {
    await assert.rejects(f.provider.inspectFixedGate(expectedSha, id));
    await assert.rejects(f.provider.stopExactDeployment({ expectedSha, deploymentId: id }));
  }
  for (const deploymentId of [undefined, null, [], [id], "not-id", other.toUpperCase().replace("2222", "AAAA")]) {
    await assert.rejects(f.provider.stopExactDeployment({ expectedSha: sha, deploymentId }));
  }
  assert.deepEqual(f.calls, []);
});
test("fixed auth and all target/source/UUID/SHA boundary drift deny with zero stop", async () => {
  const edits = [
    (d,k) => { if(k === "Auth") d.projectToken.environment.id = other; },
    (d,k) => { if(k === "Auth") d.projectToken.project.id = other; },
    (d,k) => { if(k === "Auth") d.project.services.edges = []; },
    (d,k) => { if(k === "Auth") d.project.id = other; },
    (d,k) => { if(k === "Fleet") d.serviceInstance.serviceId = other; },
    (d,k) => { if(k === "Fleet") d.environment.id = other; },
    (d,k) => { if(k === "Fleet") d.serviceInstance.environmentId = other; },
    (d,k) => { if(k === "Fleet") d.serviceInstance.source.repo = "AlphaCenter/gate"; },
    (d,k) => { if(k === "Fleet") d.serviceInstance.source.image = "private"; },
    (d,k) => { if(k === "Deployment") d.deployment.id = other; },
    ...Object.keys(target).map(key => (d,k) => { if(k === "Deployment") d.deployment[key] = other; }),
    (d,k) => { if(k === "Deployment") d.deployment.meta.commitSha = "b".repeat(40); },
    (d,k) => { if(k === "Deployment") d.deployment.meta.commitHash = "b".repeat(40); },
    (d,k) => { if(k === "Deployment") d.deployment.meta.commitSha = [sha]; },
  ];
  for (const edit of edits) { const f = fake({ edit }); await assert.rejects(f.provider.stopExactDeployment(stopInput)); assert.equal(f.stops(), 0); }
});
test("empty, second, incomplete, staged, transient, unknown and changed fleet deny new stop", async () => {
  const edits = [
    d => { d.serviceInstance.activeDeployments = []; d.serviceInstance.latestDeployment.deploymentStopped = true; },
    d => { d.serviceInstance.activeDeployments.push({ ...item(), id: other }); },
    d => { delete d.serviceInstance.activeDeployments; }, d => { delete d.serviceInstance.latestDeployment; },
    d => { d.serviceInstance.latestDeployment = null; },
    d => { delete d.environment.unmergedChangesCount; }, d => { delete d.environmentStagedChanges.patch; },
    d => { d.environment.unmergedChangesCount = 1; }, d => { d.environmentStagedChanges.patch = { private: true }; },
    d => { d.serviceInstanceAutoDeployStatus.enabled = true; },
    d => { d.serviceInstance.latestDeployment.status = "NEEDS_APPROVAL"; },
    d => { d.serviceInstance.latestDeployment.status = "BUILDING"; },
    d => { d.serviceInstance.activeDeployments[0].status = "UNKNOWN"; },
    d => { d.serviceInstance.numReplicas = null; }, d => { d.serviceInstance.numReplicas = 9; },
  ];
  for (const edit of edits) { const f = fake({ edit: (d,k) => { if(k === "Fleet") edit(d); } }); await assert.rejects(f.provider.stopExactDeployment(stopInput)); assert.equal(f.stops(), 0); }
  const f = fake({ edit: (d,k,calls) => { if(k === "Fleet" && calls.filter(x => x === "Fleet").length === 2) d.serviceInstance.numReplicas = 2; } });
  await assert.rejects(f.provider.stopExactDeployment(stopInput), /E_STATE_DRIFT/); assert.equal(f.stops(), 0);
});
test("exact observed active fleet stops at most once even concurrent preflights", async () => {
  const f = fake();
  const results = await Promise.allSettled([f.provider.stopExactDeployment(stopInput), f.provider.stopExactDeployment(stopInput)]);
  assert.equal(results.filter(r => r.status === "fulfilled").length, 1);
  assert.equal(f.stops(), 1);
  const before = f.calls.length;
  await assert.rejects(f.provider.stopExactDeployment(stopInput), unknown);
  assert.equal(f.calls.length, before);
  assert.ok(f.calls.lastIndexOf("Fleet") < f.calls.indexOf("Stop"));
});
test("every unsuccessful ACK is UNKNOWN_STOP with no mutation retry or raw data", async () => {
  for (const stop of [
    async () => { throw Error("PRIVATE_NETWORK"); },
    async () => { throw new DOMException("PRIVATE_TIMEOUT", "TimeoutError"); },
    () => Response.json({ data: { deploymentStop: true }, errors: [{ message: "PRIVATE", extensions: { code: "FORBIDDEN" } }] }),
    () => Response.json({ data: { deploymentStop: false } }),
    () => Response.json({ data: { deploymentStop: "true" } }),
    () => Response.json({ data: { deploymentStop: true } }, { status: 503 }),
    () => new Response('{"data":{"deploymentStop":true},PRIVATE'),
    () => new Response("PRIVATE".repeat(100_000)),
    () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('{"data":')); c.error(Error("PRIVATE_PARTIAL")); } })),
  ]) {
    const f = fake({ stop });
    await assert.rejects(f.provider.stopExactDeployment(stopInput), unknown);
    const n = f.calls.length;
    await assert.rejects(f.provider.stopExactDeployment(stopInput), unknown);
    await assert.rejects(f.provider.waitStopped({ ...stopInput, deadline: Date.now() + 60_000 }), unknown);
    assert.equal(f.calls.length, n); assert.equal(f.stops(), 1);
  }
});
test("header plus body share one 15s deadline, including ignored abort and stalled stream", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const mode of ["headers", "body"]) {
    let startBody;
    const f = fake({ stop: async () => {
      if(mode === "headers") return new Promise(() => {});
      return new Response(new ReadableStream({ start(c) { startBody = c; } }));
    } });
    const pending = assert.rejects(f.provider.stopExactDeployment(stopInput), unknown);
    for(let i=0;i<30 && f.stops() === 0;i++) await new Promise(setImmediate);
    assert.equal(f.stops(), 1);
    t.mock.timers.tick(15_001); await pending;
    try { startBody?.close(); } catch { /* cancelled */ }
    assert.equal(f.stops(), 1);
  }
  t.mock.timers.reset();
});
test("read-only complete stopped empty fleet permits exact readback with zero mutation", async () => {
  const f = fake({ stopped: true });
  const inspected = await f.provider.inspectFixedGate(sha, id);
  assert.equal(inspected.deployment.stopped, true); assert.deepEqual(inspected.fleet.activeDeployments, []);
  const result = await f.provider.waitStopped({ ...stopInput, deadline: Date.now() + 60_000 });
  assert.equal(result.stopped, true); assert.equal(f.stops(), 0);
  assert.deepEqual(f.calls.slice(-5), ["Auth", "Deployment", "Fleet", "Deployment", "Fleet"]);
});
test("stopped readback needs exact target/id/SHA/stopped and stable complete fleets", async () => {
  for(const edit of [
    (d,k) => { if(k === "Deployment") d.deployment.id = other; },
    (d,k) => { if(k === "Deployment") d.deployment.meta.commitSha = "b".repeat(40); },
    (d,k) => { if(k === "Deployment") d.deployment.serviceId = other; },
    (d,k) => { if(k === "Deployment") d.deployment.deploymentStopped = false; },
    (d,k) => { if(k === "Fleet") delete d.serviceInstance.activeDeployments; },
    (d,k) => { if(k === "Fleet") d.serviceInstance.latestDeployment.id = other; },
  ]) {
    let now = 0;
    const f = fake({ stopped: true, edit, clock: () => now, sleep: async ms => { now += ms; } });
    await assert.rejects(f.provider.waitStopped({ ...stopInput, deadline: 60_000 })); assert.equal(f.stops(), 0);
  }
});
test("after ACK a failed read remains uncertain and does not repeat stop", async () => {
  let after = false;
  const f = fake({ stop: () => { after = true; return Response.json({ data: { deploymentStop: true } }); },
    edit: (d,k) => { if(after && k === "Deployment") d.deployment = null; } });
  await f.provider.stopExactDeployment(stopInput);
  await assert.rejects(f.provider.waitStopped({ ...stopInput, deadline: Date.now() + 60_000 }), unknown);
  assert.equal(f.stops(), 1);
});
test("request budget20, poll12, interval5s and total10min have hard bounds", async () => {
  const f = fake();
  for(let i=0;i<5;i++) await f.provider.inspectFixedGate(sha,id);
  assert.equal(f.calls.length,20);
  await assert.rejects(f.provider.inspectFixedGate(sha,id), /E_REQUEST_LIMIT/); assert.equal(f.calls.length,20);
  let now=0, sleeps=[];
  const poll = fake({ clock:()=>now, sleep:async ms=>{sleeps.push(ms); now+=ms;} });
  await assert.rejects(poll.provider.waitStopped({...stopInput,deadline:600_000}), /E_TIMEOUT/);
  assert.equal(poll.calls.filter(x=>x === "Deployment").length,12);
  assert.deepEqual(sleeps,Array(11).fill(5000)); assert.equal(poll.stops(),0);
  const total=fake({clock:()=>now}); await total.provider.inspectFixedGate(sha,id);
  now+=600_000; const count=total.calls.length;
  await assert.rejects(total.provider.stopExactDeployment(stopInput),/E_TIMEOUT/); assert.equal(total.calls.length,count);
});
test("ACK then complete stopped readback has two empty fleets and no recovery", async () => {
  let stopped=false;
  const f=fake({stop:()=>{stopped=true;return Response.json({data:{deploymentStop:true}});},
    edit:(d,k)=>{if(stopped && k === "Fleet") Object.assign(d,fleet(true)); if(stopped && k === "Deployment") d.deployment=item(true);} });
  await f.provider.stopExactDeployment(stopInput);
  const result=await f.provider.waitStopped({...stopInput,deadline:Date.now()+60_000});
  assert.equal(result.stopped,true);assert.equal(f.stops(),1);assert.equal(f.calls.length,10);
  assert.ok(!f.calls.some(x=>/Deploy$|Restart|Variable/u.test(x)));
});

test("read budgets count auth, expired wait does no HTTP, short deadline bounds auth body", async (t) => {
  const f=fake();
  await assert.rejects(f.provider.waitStopped({...stopInput,deadline:Date.now()-1}));assert.deepEqual(f.calls,[]);
  t.mock.timers.enable({apis:["setTimeout"]});
  let calls=0;
  const p=makeFixedGateMaintenance({token,fetchFn:async()=>{calls++;return new Promise(()=>{});}});
  const pending=assert.rejects(p.waitStopped({...stopInput,deadline:Date.now()+1000}),error=>error.code === "E_TIMEOUT" && !error.uncertain);
  t.mock.timers.tick(1001);await pending;assert.equal(calls,1);t.mock.timers.reset();
});
test("stopped empty branch rejects later fleet/source drift and does not imply original receipt", async () => {
  const f=fake({stopped:true,edit:(d,k,c)=>{if(k === "Fleet" && c.filter(x=>x === "Fleet").length === 2)d.serviceInstance.numReplicas=2;}});
  await assert.rejects(f.provider.waitStopped({...stopInput,deadline:Date.now()+60_000}),/E_NOT_STOPPED/);assert.equal(f.stops(),0);
  const otherState=fake({stopped:true,edit:(d,k)=>{if(k === "Fleet")d.serviceInstance.source.repo="AlphaCenter/gate";}});
  await assert.rejects(otherState.provider.inspectFixedGate(sha,id));assert.equal(otherState.stops(),0);
});
test("valid null pending count needs an independently complete exact empty staged patch", async () => {
  const accepted=fake({edit:(d,k)=>{if(k === "Fleet")d.environment.unmergedChangesCount=null;}});
  await accepted.provider.stopExactDeployment(stopInput);assert.equal(accepted.stops(),1);
  const denied=fake({edit:(d,k)=>{if(k === "Fleet"){d.environment.unmergedChangesCount=null;delete d.environmentStagedChanges;}}});
  await assert.rejects(denied.provider.stopExactDeployment(stopInput));assert.equal(denied.stops(),0);
});
