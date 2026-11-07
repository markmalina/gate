// Unregistered source only. No default transport, env/FS credential reads or I/O
// on import/construction. The future trusted caller owns context/auth/grant gates.
import { createHash } from "node:crypto";
import { MAINTENANCE_TARGET } from "./maintenance-admission.mjs";

export const LEDGER_BOUNDARY = Object.freeze({
  repository: "markmalina/gate", repositoryId: 1293746267,
  branch: "ops-state/gate-maintenance", path: "ops-state/gate-maintenance/v1.json",
});
const TARGET = Object.freeze({ ...MAINTENANCE_TARGET, repository: LEDGER_BOUNDARY.repository,
  repositoryId: LEDGER_BOUNDARY.repositoryId });
const API = `https://api.github.com/repos/${LEDGER_BOUNDARY.repository}/contents/${LEDGER_BOUNDARY.path}`;
const GET = `${API}?ref=${encodeURIComponent(LEDGER_BOUNDARY.branch)}`;
const SCHEMA = "gate.maintenance-ledger/v1";
const MAX_BODY = 65536, MAX_SLOT = 8192, TIMEOUT_MS = 15000;
const SHA = /^[0-9a-f]{40}$/u, DIGEST = /^[0-9a-f]{64}$/u;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;
const ID_KEYS = ["operationId", "grantDigest", "executorSha", "runtimeSha", "deploymentId"];
const NEXT = Object.freeze({ EMPTY: ["CLAIMED"], CLAIMED: ["STOP_INTENT", "FAILED"],
  STOP_INTENT: ["HELD_STOPPED", "UNKNOWN_STOP", "FAILED"] });
const check = (ok) => { if (!ok) throw new Error("INVALID"); };
const matches = (re, x) => typeof x === "string" && re.test(x);
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const blobHash = (bytes) => createHash("sha1").update(`blob ${Buffer.byteLength(bytes)}\0`).update(bytes).digest("hex");
const canon = (x) => Array.isArray(x) ? `[${x.map(canon).join(",")}]` : x !== null && typeof x === "object"
  ? `{${Object.keys(x).sort().map((k) => `${JSON.stringify(k)}:${canon(x[k])}`).join(",")}}` : JSON.stringify(x);
function record(x, keys) {
  check(x !== null && typeof x === "object" && Object.getPrototypeOf(x) === Object.prototype);
  const d = Object.getOwnPropertyDescriptors(x);
  check(Reflect.ownKeys(d).length === keys.length && keys.every((k) => Object.hasOwn(d, k)
    && Object.hasOwn(d[k], "value") && d[k].enumerable));
  return Object.fromEntries(keys.map((k) => [k, d[k].value]));
}
function identity(x) {
  const i = record(x, ID_KEYS);
  check(matches(UUID, i.operationId) && matches(UUID, i.deploymentId) && matches(DIGEST, i.grantDigest)
    && matches(SHA, i.executorSha) && matches(SHA, i.runtimeSha));
  return i;
}
function instant(x) {
  check(typeof x === "string" && x.length === 24 && Number.isFinite(Date.parse(x))
    && new Date(x).toISOString() === x);
  return Date.parse(x);
}
const stateOf = (doc) => doc.history.at(-1)?.state ?? "EMPTY";
const seed = () => ({ schemaVersion: SCHEMA, target: TARGET, identity: null, history: [] });
function parseSlot(bytes) {
  check(typeof bytes === "string" && Buffer.byteLength(bytes) > 0 && Buffer.byteLength(bytes) <= MAX_SLOT);
  const d = record(JSON.parse(bytes), ["schemaVersion", "target", "identity", "history"]);
  check(canon(d) === bytes && d.schemaVersion === SCHEMA && canon(record(d.target, Object.keys(TARGET))) === canon(TARGET));
  check(Array.isArray(d.history) && d.history.length <= 3);
  if (!d.history.length) check(d.identity === null);
  else identity(d.identity);
  let previous = seed(), previousTime = -Infinity;
  for (const item of d.history) {
    const e = record(item, ["state", "at", "previousDigest"]);
    check(NEXT[stateOf(previous)]?.includes(e.state) && e.previousDigest === hash(canon(previous)));
    const t = instant(e.at); check(t >= previousTime); previousTime = t;
    previous = { ...d, history: [...previous.history, e] };
  }
  return d;
}
function snapshot(blobSha, content) {
  const doc = parseSlot(content);
  check(matches(SHA, blobSha) && blobSha === blobHash(content));
  return Object.freeze({ blobSha, content, digest: hash(content), state: stateOf(doc) });
}
function suppliedSnapshot(x) {
  const s = record(x, ["blobSha", "content", "digest", "state"]);
  const valid = snapshot(s.blobSha, s.content);
  check(s.digest === valid.digest && s.state === valid.state);
  return valid;
}
function decodeContent(x) {
  check(x && x.type === "file" && x.path === LEDGER_BOUNDARY.path && x.name === "v1.json"
    && matches(SHA, x.sha) && (x.url === API || x.url === GET) && x.html_url === `https://github.com/${LEDGER_BOUNDARY.repository}/blob/${LEDGER_BOUNDARY.branch}/${LEDGER_BOUNDARY.path}`
    && x.git_url === `https://api.github.com/repos/${LEDGER_BOUNDARY.repository}/git/blobs/${x.sha}`);
  check(x.encoding === "base64" && typeof x.content === "string" && x.content.length <= MAX_SLOT * 2);
  const b64 = x.content.replace(/\n/gu, "");
  check(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(b64));
  const b = Buffer.from(b64, "base64");
  check(Number.isInteger(x.size) && x.size === b.length && b.length <= MAX_SLOT && b.toString("base64") === b64);
  const content = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(b);
  return snapshot(x.sha, content);
}

// No default fetch or token lookup. Missing dependencies produce a disabled
// object; they are never sourced from globals. All later I/O is explicit.
export function createMaintenanceLedger({ token, fetchFn, clock } = {}) {
  const enabled = typeof token === "string" && token.length >= 1 && token.length <= 4096
    && /^[\x21-\x7e]+$/u.test(token) && typeof fetchFn === "function" && typeof clock === "function";
  let quarantined = false, busy = false, capability = null;
  const deny = (category = "DENIED", phase = "INPUT", httpStatus = null) => Object.freeze({
    status: quarantined ? "QUARANTINED" : "DENIED", durableIntent: false, stopCapability: null,
    diagnostic: Object.freeze({ phase, category, httpStatus }), retryable: false,
  });
  function quarantine(category, phase, httpStatus = null) {
    quarantined = true; capability = null; return deny(category, phase, httpStatus);
  }
  async function request(url, method, payload, phase) {
    const controller = new AbortController();
    let timer, reader, responseBody, status = null;
    const deadline = new Promise((_, reject) => { timer = setTimeout(() => {
      controller.abort(); reject({ category: "TIMEOUT", phase, httpStatus: status });
    }, TIMEOUT_MS); });
    const run = async () => {
      try {
        const r = await fetchFn(url, { method, headers: {
          Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28", ...(method === "PUT" ? { "Content-Type": "application/json" } : {}),
        }, redirect: "error", signal: controller.signal, ...(payload ? { body: JSON.stringify(payload) } : {}) });
        responseBody = r?.body; check(!controller.signal.aborted);
        status = Number.isInteger(r?.status) && r.status >= 100 && r.status <= 599 ? r.status : null;
        if (status !== 200) throw { category: status === 404 ? "MISSING" : status === 409 || status === 422 ? "CONFLICT" : "HTTP", phase, httpStatus: status };
        check(r.url === url && r.redirected === false && /^application\/json(?:;|$)/iu.test(r.headers.get("content-type") ?? ""));
        const length = r.headers.get("content-length");
        check(length === null || (/^[0-9]+$/u.test(length) && Number(length) <= MAX_BODY));
        reader = r.body.getReader();
        let total = 0; const chunks = [];
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          check(value instanceof Uint8Array); total += value.length; check(total <= MAX_BODY);
          chunks.push(Buffer.from(value));
        }
        return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)));
      } catch (e) {
        try { Promise.resolve(reader ? reader.cancel() : responseBody?.cancel()).catch(() => {}); } catch {}
        if (e?.phase === phase && ["MISSING", "CONFLICT", "HTTP"].includes(e.category)) throw e;
        throw { category: "INVALID_OR_NETWORK", phase, httpStatus: status };
      }
    };
    try { return await Promise.race([run(), deadline]); }
    finally {
      clearTimeout(timer);
      try { Promise.resolve(reader ? reader.cancel() : responseBody?.cancel()).catch(() => {}); } catch {}
    }
  }
  async function read(phase) { return decodeContent(await request(GET, "GET", null, phase)); }
  function safe(e, fallback) {
    const phase = ["GET_CURRENT", "PUT", "GET_READBACK"].includes(e?.phase) ? e.phase : fallback;
    const category = ["TIMEOUT", "MISSING", "CONFLICT", "HTTP", "INVALID_OR_NETWORK"].includes(e?.category) ? e.category : "INVALID";
    const httpStatus = Number.isInteger(e?.httpStatus) && e.httpStatus >= 100 && e.httpStatus <= 599 ? e.httpStatus : null;
    return { phase, category, httpStatus };
  }
  return Object.freeze({
    async observe() {
      if (!enabled || quarantined || busy) return deny();
      busy = true; capability = null;
      try { return Object.freeze({ status: "OBSERVED", snapshot: await read("GET_CURRENT"),
        durableIntent: false, stopCapability: null, retryable: false }); }
      catch (e) { const d = safe(e, "GET_CURRENT"); return deny(d.category, d.phase, d.httpStatus); }
      finally { busy = false; }
    },
    async transition(input) {
      if (!enabled || quarantined || busy) return deny();
      capability = null;
      let expected, id, to, doc, at;
      try {
        ({ expected, identity: id, to } = record(input, ["expected", "identity", "to"]));
        expected = suppliedSnapshot(expected); id = identity(id); doc = parseSlot(expected.content);
        check(NEXT[expected.state]?.includes(to));
        check(doc.identity === null || canon(doc.identity) === canon(id));
        at = clock(); const t = instant(at); check(!doc.history.length || t >= instant(doc.history.at(-1).at));
      } catch { return deny(); }
      busy = true; capability = null;
      let phase = "GET_CURRENT";
      try {
        const current = await read(phase);
        if (canon(current) !== canon(expected)) return quarantine("STALE", phase);
        const content = canon({ ...doc, identity: id,
          history: [...doc.history, { state: to, at, previousDigest: expected.digest }] });
        const next = snapshot(blobHash(content), content);
        phase = "PUT";
        const ack = await request(API, "PUT", { message: `gate maintenance ledger ${to}`,
          content: Buffer.from(content).toString("base64"), sha: expected.blobSha, branch: LEDGER_BOUNDARY.branch }, phase);
        check(ack?.content?.sha === next.blobSha && ack.content.path === LEDGER_BOUNDARY.path
          && (ack.content.url === API || ack.content.url === GET) && ack.content.git_url === `https://api.github.com/repos/${LEDGER_BOUNDARY.repository}/git/blobs/${next.blobSha}`
          && ack.content.html_url === `https://github.com/${LEDGER_BOUNDARY.repository}/blob/${LEDGER_BOUNDARY.branch}/${LEDGER_BOUNDARY.path}`
          && ack.content.type === "file" && ack.content.size === Buffer.byteLength(content)
          && matches(SHA, ack.commit?.sha) && ack.commit.url === `https://api.github.com/repos/${LEDGER_BOUNDARY.repository}/git/commits/${ack.commit.sha}`
          && ack.commit.html_url === `https://github.com/${LEDGER_BOUNDARY.repository}/commit/${ack.commit.sha}`);
        phase = "GET_READBACK";
        const actual = await read(phase);
        check(canon(actual) === canon(next));
        if (to === "STOP_INTENT") capability = Object.freeze({});
        return Object.freeze({ status: "COMMITTED", snapshot: actual, commitSha: ack.commit.sha,
          durableIntent: to === "STOP_INTENT", stopCapability: capability, retryable: false });
      } catch (e) { const d = safe(e, phase); return quarantine(d.category, d.phase, d.httpStatus); }
      finally { busy = false; }
    },
    // Identity equality or an observed pre-existing intent cannot mint this
    // process-local, one-use capability. No stop primitive is included here.
    consumeStopCapability(value) {
      const allowed = !quarantined && !busy && capability !== null && value === capability;
      if (allowed) capability = null;
      return allowed;
    },
  });
}
