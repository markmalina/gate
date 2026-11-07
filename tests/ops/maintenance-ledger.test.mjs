import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createMaintenanceLedger } from "../../.github/actions/gate-deploy/maintenance-ledger.mjs";

const API = "https://api.github.com/repos/markmalina/gate/contents/ops-state/gate-maintenance/v1.json";
const BRANCH = "ops-state/gate-maintenance", PATH = "ops-state/gate-maintenance/v1.json";
const GET = `${API}?ref=ops-state%2Fgate-maintenance`;
const TOKEN = "synthetic-test-secret-never-log";
const now = () => "2026-11-07T05:30:00.000Z";
const id = { operationId: "11111111-1111-4111-8111-111111111111", grantDigest: "a".repeat(64),
  executorSha: "b".repeat(40), runtimeSha: "c".repeat(40), deploymentId: "22222222-2222-4222-8222-222222222222" };
const canonical = (x) => Array.isArray(x) ? `[${x.map(canonical)}]` : x !== null && typeof x === "object"
  ? `{${Object.keys(x).sort().map(k => `${JSON.stringify(k)}:${canonical(x[k])}`).join(",")}}` : JSON.stringify(x);
const digest = x => createHash("sha256").update(x).digest("hex");
const blob = x => createHash("sha1").update(`blob ${Buffer.byteLength(x)}\0${x}`).digest("hex");
const seed = () => ({ schemaVersion: "gate.maintenance-ledger/v1", target: {
  projectId: "972b6ab7-988a-421c-abfa-425451ad714f", environmentId: "a148b138-44b3-4349-9d4b-292880503055",
  serviceId: "099cbf8c-1d94-43fe-8714-90725bf93998", repository: "markmalina/gate", repositoryId: 1293746267,
}, identity: null, history: [] });
function file(content) { const sha = blob(content); return { type: "file", path: PATH, name: "v1.json", sha,
  size: Buffer.byteLength(content), encoding: "base64", content: Buffer.from(content).toString("base64"), url: API,
  html_url: `https://github.com/markmalina/gate/blob/${BRANCH}/${PATH}`,
  git_url: `https://api.github.com/repos/markmalina/gate/git/blobs/${sha}` }; }
function response(url, data, status = 200) {
  const r = new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  Object.defineProperty(r, "url", { value: url }); return r;
}
function server({ content = canonical(seed()), hook } = {}) {
  const calls = []; let commits = 0;
  const fetchFn = async (url, opts) => {
    calls.push({ url, ...opts });
    assert.equal(opts.redirect, "error"); assert.ok(opts.signal instanceof AbortSignal);
    assert.equal(opts.headers.Authorization, `Bearer ${TOKEN}`);
    assert.equal(opts.headers.Accept, "application/vnd.github+json");
    assert.equal(opts.headers["X-GitHub-Api-Version"], "2022-11-28");
    if (hook) { const r = await hook({ url, opts, calls, get content() { return content; }, set content(v) { content = v; } }); if (r) return r; }
    if (opts.method === "GET") { assert.equal(url, GET); assert.equal(opts.body, undefined); return response(url, file(content)); }
    assert.equal(url, API); assert.equal(opts.method, "PUT");
    assert.equal(opts.headers["Content-Type"], "application/json");
    const p = JSON.parse(opts.body); assert.equal(p.branch, BRANCH);
    assert.deepEqual(Object.keys(p).sort(), ["branch", "content", "message", "sha"]);
    if (p.sha !== blob(content)) return response(url, { message: TOKEN }, 409);
    content = Buffer.from(p.content, "base64").toString(); commits++;
    const sha = String(commits).repeat(40);
    return response(url, { content: file(content), commit: { sha,
      url: `https://api.github.com/repos/markmalina/gate/git/commits/${sha}`,
      html_url: `https://github.com/markmalina/gate/commit/${sha}` } });
  };
  return { calls, fetchFn, get content() { return content; }, get commits() { return commits; } };
}
const make = s => createMaintenanceLedger({ token: TOKEN, fetchFn: s.fetchFn, clock: now });
async function claimed(s, a = make(s)) {
  const o = await a.observe(); assert.equal(o.status, "OBSERVED");
  const c = await a.transition({ expected: o.snapshot, identity: id, to: "CLAIMED" });
  assert.equal(c.status, "COMMITTED"); return { a, c };
}
async function intent(s) { const { a, c } = await claimed(s);
  const i = await a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
  assert.equal(i.status, "COMMITTED"); return { a, c, i }; }

test("import/default construction do no I/O and missing injection stays disabled", async () => {
  let calls = 0; const a = createMaintenanceLedger({ fetchFn: () => { calls++; throw Error(); } });
  const b = createMaintenanceLedger(); assert.equal(calls, 0);
  assert.equal((await a.observe()).status, "DENIED"); assert.equal((await b.transition({})).status, "DENIED"); assert.equal(calls, 0);
});
test("existing seed => claim => durable own intent => terminal, preserving exact prefix digests and CAS", async () => {
  const s = server(), { a, c, i } = await intent(s);
  assert.equal(c.durableIntent, false); assert.equal(i.durableIntent, true);
  assert.deepEqual(s.calls.map(c => c.method), ["GET", "GET", "PUT", "GET", "GET", "PUT", "GET"]);
  const doc = JSON.parse(i.snapshot.content), prior = JSON.parse(c.snapshot.content);
  assert.deepEqual(doc.history.slice(0, 1), prior.history);
  assert.equal(doc.history[0].previousDigest, digest(canonical(seed())));
  assert.equal(doc.history[1].previousDigest, digest(c.snapshot.content));
  assert.equal(JSON.parse(s.calls[5].body).sha, c.snapshot.blobSha);
  assert.equal(a.consumeStopCapability({}), false); assert.equal(a.consumeStopCapability(i.stopCapability), true);
  assert.equal(a.consumeStopCapability(i.stopCapability), false);
  const t = await a.transition({ expected: i.snapshot, identity: id, to: "HELD_STOPPED" });
  assert.equal(t.status, "COMMITTED"); assert.equal(t.durableIntent, false); assert.equal(s.commits, 3);
});
test("two callers with same expected blob perform at most one CAS write/readback and mint one capability", async () => {
  const s = server(), { c } = await claimed(s), a = make(s), b = make(s);
  const before = s.calls.length;
  const results = await Promise.all([a, b].map(x => x.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" })));
  assert.equal(results.filter(r => r.durableIntent).length, 1);
  assert.equal(results.filter(r => r.status === "QUARANTINED").length, 1);
  assert.equal(s.calls.slice(before).filter(x => x.method === "PUT").length, 2);
  assert.equal(s.calls.slice(before).filter(x => x.method === "GET").length, 3);
  assert.equal(s.commits, 2); // claim + one intent, loser never retries
  const loser = results[0].status === "QUARANTINED" ? a : b, count = s.calls.length;
  assert.equal((await loser.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" })).status, "QUARANTINED");
  assert.equal(s.calls.length, count);
});
test("observed intent on fresh instance or later time never grants stop or accepts reset/new operation", async () => {
  const s = server(), { i } = await intent(s), fresh = createMaintenanceLedger({ token: TOKEN, fetchFn: s.fetchFn,
    clock: () => "2027-11-07T05:30:00.000Z" });
  const observed = await fresh.observe(); assert.equal(observed.snapshot.state, "STOP_INTENT");
  assert.equal(observed.durableIntent, false); assert.equal(observed.stopCapability, null);
  assert.equal(fresh.consumeStopCapability(i.stopCapability), false);
  const before = s.calls.length;
  for (const to of ["CLAIMED", "STOP_INTENT", "EMPTY"]) assert.equal((await fresh.transition({ expected: observed.snapshot, identity: id, to })).status, "DENIED");
  assert.equal((await fresh.transition({ expected: observed.snapshot, identity: { ...id, operationId: "33333333-3333-4333-8333-333333333333" }, to: "FAILED" })).status, "DENIED");
  assert.equal(s.calls.length, before);
});
test("FAILED UNKNOWN_STOP and HELD_STOPPED terminal records deny all changes and remain byte-identical", async () => {
  for (const state of ["FAILED", "UNKNOWN_STOP", "HELD_STOPPED"]) {
    const s = server(), { a, i } = await intent(s), t = await a.transition({ expected: i.snapshot, identity: id, to: state });
    const before = s.content, count = s.calls.length;
    for (const to of ["CLAIMED", "STOP_INTENT", "HELD_STOPPED", "FAILED", "UNKNOWN_STOP"]) {
      assert.equal((await a.transition({ expected: t.snapshot, identity: id, to })).status, "DENIED");
    }
    assert.equal(s.calls.length, count); assert.equal(s.content, before);
  }
});
test("claimed can fail without intent, but cannot jump directly to stopped", async () => {
  const s = server(), { a, c } = await claimed(s), count = s.calls.length;
  assert.equal((await a.transition({ expected: c.snapshot, identity: id, to: "HELD_STOPPED" })).status, "DENIED");
  assert.equal(s.calls.length, count);
  assert.equal((await a.transition({ expected: c.snapshot, identity: id, to: "FAILED" })).status, "COMMITTED");
});
test("missing slot never bootstraps and corrupt snapshots never write", async () => {
  for (const content of ["{}", canonical({ ...seed(), extra: 1 }), canonical({ ...seed(), target: { ...seed().target, repositoryId: "1293746267" } }),
    canonical(seed()) + "\n", '{"identity":null,"identity":null}', "\ufeff" + canonical(seed())]) {
    const s = server({ content }); assert.equal((await make(s).observe()).status, "DENIED"); assert.equal(s.commits, 0);
  }
  const s = server({ hook: ({url}) => response(url, { message: TOKEN }, 404) }), a = make(s);
  assert.equal((await a.observe()).diagnostic.category, "MISSING");
  assert.deepEqual(s.calls.map(x => x.method), ["GET"]);
});
test("complete supplied snapshot and every identity field bind before I/O", async () => {
  const s = server(), { a, c } = await claimed(s), count = s.calls.length;
  for (const expected of [{ ...c.snapshot, digest: "d".repeat(64) }, { ...c.snapshot, blobSha: "d".repeat(40) },
    { ...c.snapshot, state: "EMPTY" }, { ...c.snapshot, extra: true }]) {
    assert.equal((await a.transition({ expected, identity: id, to: "STOP_INTENT" })).status, "DENIED");
  }
  for (const k of Object.keys(id)) assert.equal((await a.transition({ expected: c.snapshot,
    identity: { ...id, [k]: k.endsWith("Sha") ? "d".repeat(40) : k === "grantDigest" ? "d".repeat(64) : "33333333-3333-4333-8333-333333333333" }, to: "STOP_INTENT" })).status, "DENIED");
  assert.equal(s.calls.length, count);
});
test("stale expected state is quarantined with zero PUT and no reread retry", async () => {
  const s = server(), a = make(s), old = await a.observe(); await claimed(s);
  const before = s.calls.length, r = await a.transition({ expected: old.snapshot, identity: id, to: "CLAIMED" });
  assert.equal(r.status, "QUARANTINED"); assert.equal(r.diagnostic.category, "STALE");
  assert.deepEqual(s.calls.slice(before).map(x => x.method), ["GET"]);
});
test("wrong repository branch path and blob content provenance fail closed", async () => {
  for (const change of [x => x.path = "other", x => x.url += "?ref=main", x => x.html_url = x.html_url.replace(BRANCH, "main"),
    x => x.git_url = x.git_url.replace("markmalina/gate", "foreign/repo"), x => x.sha = "d".repeat(40),
    x => x.size++, x => x.content += "!", x => x.encoding = "utf-8", x => x.type = "dir"]) {
    const s = server({ hook: ({ url, content }) => { const f = file(content); change(f); return response(url, f); } });
    assert.equal((await make(s).observe()).status, "DENIED"); assert.equal(s.commits, 0);
  }
});
test("PUT errors, lost ACK after actual commit, malformed 200, or creation ACK quarantine without retry or secret leaks", async () => {
  for (const failure of [409, 422, 401, 201, "network", "lostACK", "bad200"]) {
    let inject = false;
    const s = server({ hook: (ctx) => {
      const {url, opts} = ctx;
      if (opts.method !== "PUT" || !inject) return;
      if (failure === "network") throw Error(TOKEN);
      if (failure === "lostACK") { ctx.content = Buffer.from(JSON.parse(opts.body).content, "base64").toString(); throw Error(TOKEN); }
      return response(url, { message: TOKEN }, failure === "bad200" ? 200 : failure);
    } }), { a, c } = await claimed(s); inject = true; const before = s.calls.length;
    const r = await a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
    assert.equal(r.status, "QUARANTINED"); assert.equal(r.durableIntent, false); assert.equal(r.stopCapability, null);
    assert.equal(JSON.stringify(r).includes(TOKEN), false);
    if (failure === "lostACK") assert.equal(JSON.parse(s.content).history.at(-1).state, "STOP_INTENT");
    assert.deepEqual(s.calls.slice(before).map(x => x.method), ["GET", "PUT"]);
    await a.observe(); await a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
    assert.equal(s.calls.length, before + 2);
  }
});
test("successful write with bad exact readback quarantines, committed intent is never replayed", async () => {
  let bad = false;
  const s = server({ hook: ({url, opts, calls}) => {
    if (bad && opts.method === "GET" && calls.at(-2)?.method === "PUT") return response(url, file(canonical(seed())));
  } }), { a, c } = await claimed(s); bad = true;
  const r = await a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
  assert.equal(r.status, "QUARANTINED"); assert.equal(s.commits, 2);
  const fresh = make(s), observed = await fresh.observe(); assert.equal(observed.snapshot.state, "STOP_INTENT");
  assert.equal(observed.durableIntent, false); assert.equal(fresh.consumeStopCapability({}), false);
});
test("HTTP body bounds and redirect metadata are enforced without write", async () => {
  for (const mode of ["body", "length", "redirect", "url", "malformed"]) {
    const s = server({ hook: ({url}) => {
      const r = mode === "body" ? response(url, "x".repeat(65537)) : response(url, mode === "malformed" ? { errors: [TOKEN] } : file(canonical(seed())));
      if (mode === "length") r.headers.set("content-length", "999999");
      if (mode === "redirect") Object.defineProperty(r, "redirected", { value: true });
      if (mode === "url") Object.defineProperty(r, "url", { value: "https://evil.test/" });
      return r;
    } });
    assert.equal((await make(s).observe()).status, "DENIED"); assert.equal(s.calls.length, 1); assert.equal(s.commits, 0);
  }
});
test("history cannot be rewritten, extended after terminal or disconnected from previous digest", async () => {
  const s = server(), { i } = await intent(s), doc = JSON.parse(i.snapshot.content);
  for (const corrupt of [ { ...doc, history: doc.history.map((x,n) => n ? { ...x, previousDigest: "f".repeat(64) } : x) },
    { ...doc, identity: { ...id, operationId: 1 } },
    { ...doc, history: [...doc.history, { state: "CLAIMED", at: now(), previousDigest: digest(i.snapshot.content) }] } ]) {
    const c = server({ content: canonical(corrupt) }); assert.equal((await make(c).observe()).status, "DENIED"); assert.equal(c.commits, 0);
  }
});

test("metadata permits exactly fixed optional ref, rejecting other query selectors", async () => {
  for (const suffix of ["", "?ref=ops-state%2Fgate-maintenance", "?ref=main", "?ref=ops-state%2Fgate-maintenance&x=1"]) {
    const s = server({ hook: ({url, opts, content}) => {
      if (opts.method === "GET") { const f = file(content); f.url += suffix; return response(url, f); }
    } });
    assert.equal((await make(s).observe()).status, ["", "?ref=ops-state%2Fgate-maintenance"].includes(suffix) ? "OBSERVED" : "DENIED");
  }
});
test("PUT ACK then stalled readback body hits deadline, cancels and quarantines with no retries", async (t) => {
  let stall = false, cancelled = 0;
  const s = server({ hook: ({url, opts, calls}) => {
    if (stall && opts.method === "GET" && calls.at(-2)?.method === "PUT") {
      const r = new Response(new ReadableStream({ pull() { return new Promise(() => {}); }, cancel() { cancelled++; } }),
        { headers: { "Content-Type": "application/json" } });
      Object.defineProperty(r, "url", { value: url }); return r;
    }
  } }), { a, c } = await claimed(s); stall = true;
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const pending = a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
  await new Promise(resolve => setImmediate(resolve)); t.mock.timers.tick(15000);
  const r = await pending; assert.equal(r.status, "QUARANTINED"); assert.equal(r.diagnostic.category, "TIMEOUT");
  assert.equal(r.diagnostic.phase, "GET_READBACK"); assert.equal(r.durableIntent, false);
  assert.equal(cancelled, 1); assert.equal(s.commits, 2);
  const count = s.calls.length; await a.observe(); assert.equal(s.calls.length, count);
});
test("PUT ignoring abort times out once without later readback or capability", async (t) => {
  let stall = false;
  const s = server({ hook: ({opts}) => { if (stall && opts.method === "PUT") return new Promise(() => {}); } });
  const { a, c } = await claimed(s); stall = true;
  t.mock.timers.enable({ apis: ["setTimeout"] }); const before = s.calls.length;
  const pending = a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
  await new Promise(resolve => setImmediate(resolve)); t.mock.timers.tick(15000);
  const r = await pending; assert.equal(r.status, "QUARANTINED"); assert.equal(r.diagnostic.category, "TIMEOUT");
  assert.equal(r.diagnostic.phase, "PUT"); assert.equal(r.durableIntent, false);
  assert.deepEqual(s.calls.slice(before).map(x => x.method), ["GET", "PUT"]);
});

test("a later observation revokes earlier own intent capability even when state is unchanged", async () => {
  const s = server(), { a, i } = await intent(s);
  const r = await a.observe(); assert.equal(r.durableIntent, false);
  assert.equal(a.consumeStopCapability(i.stopCapability), false);
});
test("incomplete or foreign ACK metadata cannot mint intent and makes no readback or retry", async () => {
  for (const field of ["commit", "path", "url", "html_url", "git_url", "size", "sha"]) {
    let bad = false;
    const s = server({ hook: (ctx) => {
      if (!bad || ctx.opts.method !== "PUT") return;
      const content = Buffer.from(JSON.parse(ctx.opts.body).content, "base64").toString(), f = file(content);
      const commitSha = "d".repeat(40), commit = { sha: commitSha,
        url: `https://api.github.com/repos/markmalina/gate/git/commits/${commitSha}`,
        html_url: `https://github.com/markmalina/gate/commit/${commitSha}` };
      if (field === "commit") commit.url = commit.url.replace("markmalina/gate", "other/repo");
      else f[field] = field === "size" ? 0 : "wrong";
      return response(ctx.url, { content: f, commit });
    } }), { a, c } = await claimed(s); bad = true; const count = s.calls.length;
    const r = await a.transition({ expected: c.snapshot, identity: id, to: "STOP_INTENT" });
    assert.equal(r.status, "QUARANTINED"); assert.equal(r.durableIntent, false);
    assert.deepEqual(s.calls.slice(count).map(x => x.method), ["GET", "PUT"]);
  }
});
