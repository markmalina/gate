import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { deflateRawSync } from "node:zlib";
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

import { createMaintenanceGitHub } from "../../.github/actions/gate-deploy/maintenance-github.mjs";
import { prepareHostedMaintenance, verifyHostedMaintenance, MAINTENANCE_WORKFLOW_NAME } from "../../.github/actions/gate-deploy/maintenance-entry.mjs";
const name = "maintenance-hold-receipt.json";
function crc(bytes) { let n = 0xffffffff; for (const b of bytes) { n ^= b; for (let j = 0; j < 8; j++) n = (n >>> 1) ^ (n & 1 ? 0xedb88320 : 0); } return (n ^ 0xffffffff) >>> 0; }
// Independent ZIP writer supplies actual original bytes (both compression modes).
function zipFiles(files, method = 8, descriptor = false) {
  const local = [], central = []; let offset = 0;
  for (const [key, raw] of Object.entries(files)) {
    const filename = Buffer.from(key), data = method === 8 ? deflateRawSync(raw) : raw, sum = crc(raw);
    const h = Buffer.alloc(30); h.writeUInt32LE(0x04034b50); h.writeUInt16LE(20, 4); h.writeUInt16LE(descriptor ? 8 : 0, 6); h.writeUInt16LE(method, 8);
    if (!descriptor) { h.writeUInt32LE(sum, 14); h.writeUInt32LE(data.length, 18); h.writeUInt32LE(raw.length, 22); }
    h.writeUInt16LE(filename.length, 26);
    const c = Buffer.alloc(46); c.writeUInt32LE(0x02014b50); c.writeUInt16LE(20, 4); c.writeUInt16LE(20, 6); c.writeUInt16LE(descriptor ? 8 : 0, 8); c.writeUInt16LE(method, 10);
    c.writeUInt32LE(sum, 16); c.writeUInt32LE(data.length, 20); c.writeUInt32LE(raw.length, 24); c.writeUInt16LE(filename.length, 28); c.writeUInt32LE(offset, 42);
    const d = descriptor ? Buffer.alloc(16) : Buffer.alloc(0);
    if (descriptor) { d.writeUInt32LE(0x08074b50); d.writeUInt32LE(sum, 4); d.writeUInt32LE(data.length, 8); d.writeUInt32LE(raw.length, 12); }
    local.push(h, filename, data, d); central.push(c, filename); offset += h.length + filename.length + data.length + d.length;
  }
  const directory = Buffer.concat(central), e = Buffer.alloc(22); e.writeUInt32LE(0x06054b50); e.writeUInt16LE(2, 8); e.writeUInt16LE(2, 10); e.writeUInt32LE(directory.length, 12); e.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, e]);
}

// Reference framing taken verbatim from compress-commons6.0.2, the dependency
// locked by upload-artifact043fb46… -> @actions/artifact6.2.0/archiver7.0.1.
// https://github.com/archiverjs/node-compress-commons/blob/6.0.2/lib/archivers/zip/zip-archive-output-stream.js
// Source blob f56cea93a76a78f382bc5989a4dfe8462c7d1eb8. MIT, Copyright(c)2014 Chris Talkington/contributors.
// Fake file entries only; actual upstream framing methods run offline, no action/API/upload.
const constants = { LONG_ZERO: Buffer.alloc(4), SHORT_ZERO: Buffer.alloc(2), SIG_LFH: 0x04034b50,
  SIG_CFH: 0x02014b50, SIG_DD: 0x08074b50, SIG_EOCD: 0x06054b50,
  MIN_VERSION_ZIP64: 45, VERSION_MADEBY: 45, ZIP64_MAGIC: 0xffffffff, ZIP64_MAGIC_SHORT: 0xffff };
const zipUtil = { getShortBytes: n => { const b=Buffer.alloc(2);b.writeUInt16LE(n);return b; },
  getLongBytes: n => { const b=Buffer.alloc(4);b.writeUInt32LE(n);return b; } };
const pinnedFrames = {
_writeLocalFileHeader: function(ae) {
  var gpb = ae.getGeneralPurposeBit();
  var method = ae.getMethod();
  var name = ae.getName();
  var extra = ae.getLocalFileDataExtra();

  if (ae.isZip64()) {
    gpb.useDataDescriptor(true);
    ae.setVersionNeededToExtract(constants.MIN_VERSION_ZIP64);
  }

  if (gpb.usesUTF8ForNames()) {
    name = Buffer.from(name);
  }

  ae._offsets.file = this.offset;

  // signature
  this.write(zipUtil.getLongBytes(constants.SIG_LFH));

  // version to extract and general bit flag
  this.write(zipUtil.getShortBytes(ae.getVersionNeededToExtract()));
  this.write(gpb.encode());

  // compression method
  this.write(zipUtil.getShortBytes(method));

  // datetime
  this.write(zipUtil.getLongBytes(ae.getTimeDos()));

  ae._offsets.data = this.offset;

  // crc32 checksum and sizes
  if (gpb.usesDataDescriptor()) {
    this.write(constants.LONG_ZERO);
    this.write(constants.LONG_ZERO);
    this.write(constants.LONG_ZERO);
  } else {
    this.write(zipUtil.getLongBytes(ae.getCrc()));
    this.write(zipUtil.getLongBytes(ae.getCompressedSize()));
    this.write(zipUtil.getLongBytes(ae.getSize()));
  }

  // name length
  this.write(zipUtil.getShortBytes(name.length));

  // extra length
  this.write(zipUtil.getShortBytes(extra.length));

  // name
  this.write(name);

  // extra
  this.write(extra);

  ae._offsets.contents = this.offset;
},
_writeCentralFileHeader: function(ae) {
  var gpb = ae.getGeneralPurposeBit();
  var method = ae.getMethod();
  var fileOffset = ae._offsets.file;

  var size = ae.getSize();
  var compressedSize = ae.getCompressedSize();

  if (ae.isZip64() || fileOffset > constants.ZIP64_MAGIC) {
    size = constants.ZIP64_MAGIC;
    compressedSize = constants.ZIP64_MAGIC;
    fileOffset = constants.ZIP64_MAGIC;

    ae.setVersionNeededToExtract(constants.MIN_VERSION_ZIP64);

    var extraBuf = Buffer.concat([
      zipUtil.getShortBytes(constants.ZIP64_EXTRA_ID),
      zipUtil.getShortBytes(24),
      zipUtil.getEightBytes(ae.getSize()),
      zipUtil.getEightBytes(ae.getCompressedSize()),
      zipUtil.getEightBytes(ae._offsets.file)
    ], 28);

    ae.setExtra(extraBuf);
  }

  // signature
  this.write(zipUtil.getLongBytes(constants.SIG_CFH));

  // version made by
  this.write(zipUtil.getShortBytes((ae.getPlatform() << 8) | constants.VERSION_MADEBY));

  // version to extract and general bit flag
  this.write(zipUtil.getShortBytes(ae.getVersionNeededToExtract()));
  this.write(gpb.encode());

  // compression method
  this.write(zipUtil.getShortBytes(method));

  // datetime
  this.write(zipUtil.getLongBytes(ae.getTimeDos()));

  // crc32 checksum
  this.write(zipUtil.getLongBytes(ae.getCrc()));

  // sizes
  this.write(zipUtil.getLongBytes(compressedSize));
  this.write(zipUtil.getLongBytes(size));

  var name = ae.getName();
  var comment = ae.getComment();
  var extra = ae.getCentralDirectoryExtra();

  if (gpb.usesUTF8ForNames()) {
    name = Buffer.from(name);
    comment = Buffer.from(comment);
  }

  // name length
  this.write(zipUtil.getShortBytes(name.length));

  // extra length
  this.write(zipUtil.getShortBytes(extra.length));

  // comments length
  this.write(zipUtil.getShortBytes(comment.length));

  // disk number start
  this.write(constants.SHORT_ZERO);

  // internal attributes
  this.write(zipUtil.getShortBytes(ae.getInternalAttributes()));

  // external attributes
  this.write(zipUtil.getLongBytes(ae.getExternalAttributes()));

  // relative offset of LFH
  this.write(zipUtil.getLongBytes(fileOffset));

  // name
  this.write(name);

  // extra
  this.write(extra);

  // comment
  this.write(comment);
},
_writeDataDescriptor: function(ae) {
  // signature
  this.write(zipUtil.getLongBytes(constants.SIG_DD));

  // crc32 checksum
  this.write(zipUtil.getLongBytes(ae.getCrc()));

  // sizes
  if (ae.isZip64()) {
    this.write(zipUtil.getEightBytes(ae.getCompressedSize()));
    this.write(zipUtil.getEightBytes(ae.getSize()));
  } else {
    this.write(zipUtil.getLongBytes(ae.getCompressedSize()));
    this.write(zipUtil.getLongBytes(ae.getSize()));
  }
},
_writeCentralDirectoryEnd: function() {
  var records = this._entries.length;
  var size = this._archive.centralLength;
  var offset = this._archive.centralOffset;

  if (this.isZip64()) {
    records = constants.ZIP64_MAGIC_SHORT;
    size = constants.ZIP64_MAGIC;
    offset = constants.ZIP64_MAGIC;
  }

  // signature
  this.write(zipUtil.getLongBytes(constants.SIG_EOCD));

  // disk numbers
  this.write(constants.SHORT_ZERO);
  this.write(constants.SHORT_ZERO);

  // number of entries
  this.write(zipUtil.getShortBytes(records));
  this.write(zipUtil.getShortBytes(records));

  // length and location of CD
  this.write(zipUtil.getLongBytes(size));
  this.write(zipUtil.getLongBytes(offset));

  // archive comment
  var comment = this.getComment();
  var commentLength = Buffer.byteLength(comment);
  this.write(zipUtil.getShortBytes(commentLength));
  this.write(comment);
}
};
function supportedUploadZip(files) {
  const chunks=[], entries=[];
  const archive = { ...pinnedFrames, offset:0, _entries:entries, _archive:{ centralOffset:0,centralLength:0 },
    write(value) { const b=Buffer.isBuffer(value)?value:Buffer.from(value);chunks.push(b);this.offset+=b.length; },
    isZip64:()=>false, getComment:()=>"" };
  for(const [filename, raw] of Object.entries(files)) {
    const data=deflateRawSync(raw,{level:6}),gpb={usesDataDescriptor:()=>true,usesUTF8ForNames:()=>true,encode:()=>zipUtil.getShortBytes(0x808)};
    const ae={_offsets:{},getGeneralPurposeBit:()=>gpb,getMethod:()=>8,getName:()=>filename,
      getLocalFileDataExtra:()=>Buffer.alloc(0),getCentralDirectoryExtra:()=>Buffer.alloc(0),isZip64:()=>false,
      getVersionNeededToExtract:()=>20,getTimeDos:()=>0,getCrc:()=>crc(raw),getCompressedSize:()=>data.length,
      getSize:()=>raw.length,getPlatform:()=>3,getComment:()=>"",getInternalAttributes:()=>0,getExternalAttributes:()=>0x81800020};
    archive._writeLocalFileHeader(ae);archive.write(data);archive._writeDataDescriptor(ae);entries.push(ae);
  }
  archive._archive.centralOffset=archive.offset;entries.forEach(ae=>archive._writeCentralFileHeader(ae));
  archive._archive.centralLength=archive.offset-archive._archive.centralOffset;archive._writeCentralDirectoryEnd();
  return Buffer.concat(chunks);
}


function hosted(stopMode) {
  const effects = scenario(stopMode === "fail" ? { edit: (d, kind) => { if (kind === "Auth") d.projectToken.environment.id = op; } } : { stopMode }), q = admission(), body = `${q.command}\n\nmaintenance-grant-base64:${q.grantBytes.toString("base64")}`;
  const comment = { id: 99000, body, user: { id: 23397294, login: "markmalina" }, issue_url: "https://api.github.com/repos/markmalina/gate/issues/815", created_at: at(0), updated_at: at(0) };
  const env = { GITHUB_REPOSITORY: "markmalina/gate", GITHUB_REPOSITORY_ID: "1293746267", GITHUB_ACTOR: "markmalina", GITHUB_ACTOR_ID: "23397294",
    GITHUB_WORKFLOW_REF: "markmalina/gate/.github/workflows/ops-maintenance.yml@refs/heads/main", GITHUB_EVENT_NAME: "issue_comment", GITHUB_REF: "refs/heads/main", GITHUB_RUN_ID: q.context.runId, GITHUB_RUN_ATTEMPT: "1", GITHUB_SHA: executor, CHECKED_OUT_SHA: executor, GITHUB_WORKFLOW_SHA: executor };
  const event = { repository: { owner: { id: 23397294 } }, sender: { id: 23397294, login: "markmalina" }, issue: { number: 815 }, action: "created", comment };
  const input = { guard: "ARMED", event, env, now: at(1) };
  const run = { id: q.context.runId, name: MAINTENANCE_WORKFLOW_NAME, display_title: "Gate maintenance #99000", run_attempt: 1, head_sha: executor, head_branch: "main", event: "issue_comment",
    path: ".github/workflows/ops-maintenance.yml", workflow_id: 123, repository: { id: 1293746267, full_name: "markmalina/gate" }, head_repository: { id: 1293746267, full_name: "markmalina/gate" },
    actor: { id: 23397294 }, triggering_actor: { id: 23397294 }, status: "completed", conclusion: stopMode ? "failure" : "success", updated_at: at(50) };
  let archive, artifact, secretReads = 0;
  const http = [];
  const fetchFn = async (url, options) => {
    http.push({ url, options }); assert.equal(options.method, "GET"); assert.equal(options.redirect, "manual"); assert.equal(options.credentials, "omit");
    assert.ok(options.signal instanceof AbortSignal);
    if (url.startsWith("https://productionresultssa0.blob.core.windows.net/")) { assert.equal(Object.hasOwn(options.headers, "Authorization"), false); return new Response(archive); }
    assert.equal(options.headers.Authorization, `Bearer ${githubToken}`);
    const path = url.replace("https://api.github.com/repos/markmalina/gate", "");
    if (path === "/git/ref/heads/main") return Response.json({ object: { sha: executor } });
    if (path === `/actions/runs/${run.id}`) return Response.json(run);
    if (path === "/actions/workflows/123") return Response.json({ id: 123, path: run.path });
    if (path === "/issues/comments/99000") return Response.json(comment);
    if (path === `/actions/runs/${run.id}/artifacts?per_page=100&page=1`) return Response.json({ total_count: 1, artifacts: [artifact] });
    if (path === "/actions/artifacts/44000000000") return Response.json(artifact);
    if (path === "/actions/artifacts/44000000000/zip") return new Response(null, { status: 302, headers: { location: "https://productionresultssa0.blob.core.windows.net/actions-results/fake/receipt.zip?sig=FAKE" } });
    throw Error(`unexpected fixed path ${path}`);
  };
  const github = createMaintenanceGitHub({ token: githubToken, fetchFn });
  const deps = { github, readSecret: () => { secretReads++; return providerToken; }, operationDeps: effects.deps };
  return { input, deps, run, comment, effects, http, fetchFn, get secretReads() { return secretReads; },
    upload(payload) { archive = supportedUploadZip({ [name]: payload.receiptBytes, [`${name}.sha256`]: payload.digestBytes });
      artifact = { id: "44000000000", name, expired: false, digest: `sha256:${sha256(archive)}`, size_in_bytes: archive.length,
        workflow_run: { id: run.id, repository_id: 1293746267, head_repository_id: 1293746267, head_branch: "main", head_sha: executor } }; return artifact; },
    get artifact() { return artifact; },
    completion() { return { guard: "ARMED", now: at(60), event: { action: "completed", workflow_run: { id: run.id, name: run.name } },
      env: { ...env, GITHUB_WORKFLOW_REF: "markmalina/gate/.github/workflows/ops-maintenance-verify.yml@refs/heads/main", GITHUB_EVENT_NAME: "workflow_run", GITHUB_RUN_ID: "33000000001" } }; }
  };
}
for (const stopMode of [undefined, "lost", "fail"]) test(`actual hosted prepare → fake upload → completed event → fixed HTTP readers → binder ${stopMode ?? "HELD"}`, async () => {
  const f = hosted(stopMode); const prepared = await prepareHostedMaintenance(f.input, f.deps);
  assert.equal(prepared.effectState, stopMode === "fail" ? "FAILED" : stopMode ? "UNKNOWN_STOP" : "HELD_STOPPED"); assert.equal(prepared.retryable, false);
  const before = f.effects.calls.length; f.upload(prepared.payload);
  const bound = await verifyHostedMaintenance(f.completion(), { github: createMaintenanceGitHub({ token: githubToken, fetchFn: f.fetchFn }) });
  assert.equal(bound.provenanceState, "BOUND"); assert.equal(bound.effectState, prepared.effectState); assert.equal(bound.retryable, false);
  assert.equal(f.secretReads, 1); assert.equal(f.effects.calls.length, before); assert.equal(f.effects.calls.filter(x => x === "Stop").length, stopMode === "fail" ? 0 : 1);
});
test("both hosted defaults OFF have no event/secret/provider/ledger/API I/O", async () => {
  const f = hosted(); assert.equal((await prepareHostedMaintenance({}, f.deps)).state, "OFF");
  assert.equal((await verifyHostedMaintenance({}, f.deps)).state, "OFF"); assert.equal(f.secretReads, 0); assert.equal(f.http.length, 0); assert.equal(f.effects.calls.length, 0);
});
for (const [label, edit] of Object.entries({
  owner: f => { f.input.event.sender.id = 1; }, repo: f => { f.input.env.GITHUB_REPOSITORY_ID = "1"; },
  issue: f => { f.input.event.issue.number = 1; }, PR: f => { f.input.event.issue.pull_request = {}; },
  event: f => { f.input.env.GITHUB_EVENT_NAME = "push"; }, action: f => { f.input.event.action = "edited"; },
  ref: f => { f.input.env.GITHUB_REF = "refs/heads/other"; }, attempt: f => { f.input.env.GITHUB_RUN_ATTEMPT = "2"; },
  sha: f => { f.input.env.CHECKED_OUT_SHA = "b".repeat(40); }, command: f => { f.comment.body = "wrong"; },
  edited: f => { f.comment.updated_at = at(1); }, hash: f => { f.comment.body = f.comment.body.replace(/grant-digest=[0-9a-f]+/u, `grant-digest=${"d".repeat(64)}`); },
  time: f => { f.input.now = at(901); }
})) test(`hosted wrong ${label} denies before protected secret/effects`, async () => {
  const f = hosted(); edit(f); await assert.rejects(() => prepareHostedMaintenance(f.input, f.deps));
  assert.equal(f.secretReads, 0); assert.equal(f.effects.calls.length, 0);
});
for (const [label, edit] of Object.entries({
  running: f => { f.run.status = "in_progress"; }, attempt: f => { f.run.run_attempt = 2; },
  stale: f => { f.run.updated_at = at(-1000); }, owner: f => { f.run.actor.id = 1; },
  source: f => { f.run.head_sha = "b".repeat(40); }, workflow: f => { f.run.path = ".github/workflows/other.yml"; },
  carrier: f => { f.run.display_title = "other #99000"; }, editedComment: f => { f.comment.updated_at = at(1); }
})) test(`completed stage rejects ${label} original without effect replay`, async () => {
  const f = hosted(), prepared = await prepareHostedMaintenance(f.input, f.deps); f.upload(prepared.payload); const before = f.effects.calls.length; edit(f);
  await assert.rejects(() => verifyHostedMaintenance(f.completion(), { github: createMaintenanceGitHub({ token: githubToken, fetchFn: f.fetchFn }) }));
  assert.equal(f.effects.calls.length, before); assert.equal(f.secretReads, 1);
});
test("fixed reader auth scopes/path IDs/no retry and construction inertness", async () => {
  const calls = []; const g = createMaintenanceGitHub({ token: "FAKE", fetchFn: async (...args) => { calls.push(args); return Response.json({ object: { sha: executor } }); } });
  assert.equal(calls.length, 0); assert.throws(() => g.readRun("other/repo", "1")); assert.throws(() => g.readRun("markmalina/gate", "../x"));
  for (let i = 0; i < 12; i++) await g.readMain(); await assert.rejects(() => g.readMain()); assert.equal(calls.length, 12);
  const bad = createMaintenanceGitHub({ token: "FAKE", fetchFn: async () => { throw Error("PRIVATE"); } }); await assert.rejects(() => bad.readMain()); assert.equal(bad.counters().requests, 1);
  await assert.rejects(() => createMaintenanceGitHub().readMain());
});
for (const location of ["http://productionresultssa0.blob.core.windows.net/actions-results/x", "https://evil.test/x", "https://api.github.com/repos/other/x", "https://user:pass@productionresultssa0.blob.core.windows.net/actions-results/x"]) test("artifact redirect denies unapproved origin without second request", async () => {
  let count = 0; const g = createMaintenanceGitHub({ token: "FAKE", fetchFn: async () => { count++; return new Response(null, { status: 302, headers: { location } }); } });
  await assert.rejects(() => g.downloadArchive("markmalina/gate", "1")); assert.equal(count, 1);
});
test("readers bound body/status/redirect/deadline budgets and strip redirect auth", async () => {
  for (const make of [() => new Response("x".repeat(65537)), () => new Response("{}", { status: 503 }), () => new Response(null, { status: 302 })]) {
    const g = createMaintenanceGitHub({ token: "FAKE", fetchFn: async () => make() }); await assert.rejects(() => g.readMain()); assert.equal(g.counters().requests, 1);
  }
  let time = 0; const g = createMaintenanceGitHub({ token: "FAKE", clock: () => time, fetchFn: async () => Response.json({}) }); await g.readMain(); time = 60001; await assert.rejects(() => g.readMain());
});


test("GitHub fetch plus body share a finite deadline even when cancellation stalls", async () => {
  let signal; const before = Date.now();
  const g = createMaintenanceGitHub({ token: "FAKE", fetchFn: async (url, options) => {
    signal = options.signal;
    return new Response(new ReadableStream({ pull() { return new Promise(() => {}); }, cancel() { return new Promise(() => {}); } }));
  } });
  await assert.rejects(() => g.readMain()); assert.equal(signal.aborted, true);
  assert.ok(Date.now() - before < 20000); assert.equal(g.counters().requests, 1);
});
test("preflight alone never reads protected secret and current-main drift rejects", async () => {
  const f = hosted(); const result = await prepareHostedMaintenance(f.input, { ...f.deps, preflightOnly: true });
  assert.equal(result.state, "ADMITTED"); assert.equal(f.secretReads, 0); assert.equal(f.effects.calls.length, 0);
  await assert.rejects(() => prepareHostedMaintenance(f.input, { ...f.deps, github: { readMain: async () => ({ object: { sha: "b".repeat(40) } }) } }));
  assert.equal(f.secretReads, 0);
});
test("upload interruption and binding interruption never replay prepare/stop", async () => {
  const f = hosted("lost"), prepared = await prepareHostedMaintenance(f.input, f.deps); f.upload(prepared.payload); const before = f.effects.calls.length;
  f.artifact.digest = `sha256:${"d".repeat(64)}`;
  const out = await verifyHostedMaintenance(f.completion(), { github: createMaintenanceGitHub({ token: githubToken, fetchFn: f.fetchFn }) });
  assert.equal(out.provenanceState, "NOT_BOUND"); assert.equal(out.retryable, false); assert.equal(f.effects.calls.length, before);
  const repeated = await prepareHostedMaintenance(f.input, f.deps); assert.equal(repeated.state, "DENIED");
  assert.equal(f.effects.calls.filter(x => x === "Stop").length, 1);
});
