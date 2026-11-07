// Fixed public GitHub REST readers. No upload, provider, ledger or write API.
// docs.github.com/en/rest/actions/artifacts#download-an-artifact
// docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run
export const GITHUB_MAINTENANCE_CAPS = Object.freeze({ requests: 12, totalMs: 60000, httpMs: 10000, jsonBytes: 65536, zipBytes: 131072 });
const REPO = "markmalina/gate", API = `https://api.github.com/repos/${REPO}`;
const ID = /^[1-9][0-9]{0,19}$/u;
const need = ok => { if (!ok) throw Error("GATE_MAINTENANCE_GITHUB_DENIED"); };
const id = value => { need(typeof value === "string" && ID.test(value)); return value; };

export function createMaintenanceGitHub({ token, fetchFn, clock = Date.now } = {}) {
  const enabled = typeof token === "string" && token.length > 0 && typeof fetchFn === "function" && typeof clock === "function";
  let count = 0, start = null, last = null;
  async function request(url, limit, authenticated, redirect = false) {
    need(enabled);
    const t = clock(); need(Number.isFinite(t) && (last === null || t >= last)); last = t;
    if (start === null) start = t;
    need(t - start < GITHUB_MAINTENANCE_CAPS.totalMs && count < GITHUB_MAINTENANCE_CAPS.requests); count++;
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(Error("E_TIMEOUT")); },
        Math.min(GITHUB_MAINTENANCE_CAPS.httpMs, GITHUB_MAINTENANCE_CAPS.totalMs - (t - start))); }), (async () => {
        const response = await fetchFn(url, { method: "GET", redirect: "manual", credentials: "omit", signal: controller.signal,
          headers: authenticated ? { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "X-GitHub-Api-Version": "2026-03-10" } : { Accept: "application/zip" } });
        need(!response.redirected && (!response.url || response.url === url));
        if (redirect) { need(response.status === 302); const location = response.headers.get("location"); await response.body?.cancel(); return location; }
        need(response.status === 200);
        const length = response.headers.get("content-length");
        if (length !== null) need(/^[0-9]+$/u.test(length) && Number(length) <= limit);
        need(response.body !== null); const reader = response.body.getReader(), chunks = []; let size = 0;
        try { for (;;) { const { value, done } = await reader.read(); if (done) break; size += value.byteLength;
          need(size <= limit); chunks.push(Buffer.from(value)); } } finally { await reader.cancel().catch(() => {}); }
        need(size > 0); return Buffer.concat(chunks, size);
      })()]);
    } finally { clearTimeout(timer); controller.abort(); }
  }
  const json = async path => JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await request(`${API}${path}`, 65536, true)));
  const repo = value => need(value === REPO);
  return Object.freeze({
    readMain: () => json("/git/ref/heads/main"),
    readRun: (r, n) => { repo(r); return json(`/actions/runs/${id(n)}`); },
    readWorkflow: (r, n) => { repo(r); return json(`/actions/workflows/${id(n)}`); },
    readArtifact: (r, n) => { repo(r); return json(`/actions/artifacts/${id(n)}`); },
    readComment: n => json(`/issues/comments/${id(n)}`),
    listArtifacts: n => json(`/actions/runs/${id(n)}/artifacts?per_page=100&page=1`),
    async downloadArchive(r, n) {
      repo(r); const location = await request(`${API}/actions/artifacts/${id(n)}/zip`, 1, true, true);
      need(typeof location === "string" && location.length <= 8192);
      const u = new URL(location);
      // GitHub's supported signed Azure artifact location; one hop, no auth or
      // cookies on the storage origin, no caller-provided/general URL endpoint.
      need(u.protocol === "https:" && /^productionresultssa[0-9]+\.blob\.core\.windows\.net$/u.test(u.hostname)
        && !u.username && !u.password && !u.port && !u.hash && u.pathname.startsWith("/actions-results/"));
      return request(u.href, 131072, false);
    },
    counters: () => Object.freeze({ requests: count, retries: 0 })
  });
}
