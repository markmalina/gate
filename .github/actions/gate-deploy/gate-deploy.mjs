import { appendFile, mkdir, rename, writeFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SHA_RE = /^[0-9a-f]{40}$/u;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;
const NUMERIC_RE = /^[1-9][0-9]{0,19}$/u;
const API_TIMEOUT_MS = 15_000;
const MAX_PROVIDER_BODY_BYTES = 512 * 1024;
const DEPLOY_TIMEOUT_MS = 900_000;
const TRANSIENT = new Set(["BUILDING", "DEPLOYING", "INITIALIZING", "WAITING", "QUEUED"]);
const TERMINAL = new Set(["SUCCESS", "FAILED", "CRASHED", "REMOVED", "SLEEPING"]);
const GRAPHQL_CODES = new Set(["GRAPHQL_PARSE_FAILED", "GRAPHQL_VALIDATION_FAILED", "BAD_USER_INPUT",
  "UNAUTHENTICATED", "FORBIDDEN", "INTERNAL_SERVER_ERROR", "TOO_MANY_REQUESTS", "RATE_LIMITED", "NOT_FOUND"]);
const safeHttpStatus = (value) => Number.isInteger(value) && value >= 100 && value <= 599 ? value : undefined;
const isUuid = (value) => typeof value === "string" && UUID_RE.test(value);
const safeGraphqlCodes = (errors) => [...new Set((Array.isArray(errors) && errors.length ? errors.slice(0, 8) : [null])
  .map((error) => GRAPHQL_CODES.has(error?.extensions?.code) ? error.extensions.code : "UNCLASSIFIED"))].sort();
const safeDeployReason = (errors) => Array.isArray(errors) && errors.length === 1
  && errors[0]?.extensions?.code === "BAD_USER_INPUT" && errors[0]?.message === "Commit not found"
  ? "COMMIT_NOT_FOUND" : "UNCLASSIFIED";

export const RAILWAY = Object.freeze({
  projectId: "972b6ab7-988a-421c-abfa-425451ad714f",
  environmentId: "a148b138-44b3-4349-9d4b-292880503055",
});

export const TARGETS = Object.freeze({
  gate: Object.freeze({
    repository: "markmalina/gate",
    serviceId: "099cbf8c-1d94-43fe-8714-90725bf93998",
  }),
});

export class FastDeployError extends Error {
  constructor(code, stage, uncertain = false, details = {}) {
    super(code);
    this.code = code;
    this.stage = stage;
    this.uncertain = uncertain;
    this.httpStatus = safeHttpStatus(details.httpStatus);
    this.graphqlCodes = details.graphqlCodes;
    this.deployReason = details.deployReason;
    this.deploymentId = isUuid(details.deploymentId) ? details.deploymentId : undefined;
  }
}

function fail(code, stage, uncertain = false, details = {}) {
  throw new FastDeployError(code, stage, uncertain, details);
}

function safeError(error, fallbackStage) {
  return {
    code: error instanceof FastDeployError && /^[A-Z0-9_]{3,64}$/u.test(error.code) ? error.code : "E_INTERNAL",
    stage: error instanceof FastDeployError && /^[a-z0-9-]{2,64}$/u.test(error.stage) ? error.stage : fallbackStage,
    ...(error instanceof FastDeployError && safeHttpStatus(error.httpStatus) !== undefined ? { httpStatus: error.httpStatus } : {}),
    ...(error instanceof FastDeployError && error.graphqlCodes !== undefined ? {
      graphqlCodes: safeGraphqlCodes(Array.isArray(error.graphqlCodes)
        ? error.graphqlCodes.map((code) => ({ extensions: { code } })) : null),
    } : {}),
    ...(error instanceof FastDeployError && error.stage === "deploy"
      ? { deployReason: error.deployReason === "COMMIT_NOT_FOUND" ? "COMMIT_NOT_FOUND" : "UNCLASSIFIED" } : {}),
  };
}

function exactTarget(value) {
  if (value !== "gate") fail("E_TARGET", "input");
  return value;
}

export function selectedTargets(value) {
  const target = exactTarget(value);
  return [target];
}

export function validateRuntime(environment) {
  const expected = {
    AGENT_OPS_CALLER_REPOSITORY: "markmalina/gate",
    AGENT_OPS_REF: "refs/heads/main",
    AGENT_OPS_WORKFLOW_REF: "markmalina/gate/.github/workflows/ops-deploy.yml@refs/heads/main",
    AGENT_OPS_RUN_ATTEMPT: "1",
    AGENT_OPS_ACTOR: "markmalina",
    AGENT_OPS_ACTOR_ID: "23397294",
    AGENT_OPS_EXECUTOR_REPOSITORY: "markmalina/gate",
    AGENT_OPS_CALLER_REPOSITORY_ID: "1293746267",
  };
  for (const [name, value] of Object.entries(expected)) {
    if (environment[name] !== value) fail("E_TRUSTED_TRIGGER", "trusted-trigger");
  }
  if (!new Set(["issue_comment", "workflow_dispatch"]).has(environment.AGENT_OPS_EVENT_NAME)) {
    fail("E_TRUSTED_TRIGGER", "trusted-trigger");
  }
  if (!SHA_RE.test(environment.AGENT_OPS_EXECUTOR_SOURCE_SHA ?? "")) fail("E_EXECUTOR_SHA", "trusted-trigger");
  if (environment.AGENT_OPS_WORKFLOW_SHA !== environment.AGENT_OPS_EXECUTOR_SOURCE_SHA
      || environment.AGENT_OPS_CHECKED_OUT_SHA !== environment.AGENT_OPS_EXECUTOR_SOURCE_SHA) {
    fail("E_EXECUTOR_PROVENANCE", "trusted-trigger");
  }
  if (!NUMERIC_RE.test(environment.AGENT_OPS_RUN_ID ?? "")) fail("E_TRANSACTION", "trusted-trigger");
  if (!SHA_RE.test(environment.AGENT_OPS_EXPECTED_SOURCE_SHA ?? "")) fail("E_SOURCE_SHA", "trusted-trigger");
  return {
    expectedSourceSha: environment.AGENT_OPS_EXPECTED_SOURCE_SHA,
    target: exactTarget(environment.AGENT_OPS_DEPLOY_TARGET),
    actor: environment.AGENT_OPS_ACTOR,
    actorId: environment.AGENT_OPS_ACTOR_ID,
    runId: environment.AGENT_OPS_RUN_ID,
    executorSha: environment.AGENT_OPS_EXECUTOR_SOURCE_SHA,
  };
}

async function boundedFetch(fetchFn, url, options, stage, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchFn(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === "AbortError" || error?.name === "TimeoutError") fail("E_TIMEOUT", stage, true);
    fail("E_NETWORK", stage, true);
  } finally {
    clearTimeout(timer);
  }
}

export function sourceWorkflowToken(environment) {
  // The Gate-owned caller supplies its ephemeral, repository-scoped contents:read token.
  // Never accept a permanent App key or mint a wider installation credential here.
  const token = environment.AGENT_OPS_SOURCE_GITHUB_TOKEN;
  if (typeof token !== "string" || token.length < 20 || token.length > 512 || /\s/u.test(token)) {
    fail("E_GITHUB_AUTH", "github-workflow-token");
  }
  return token;
}

export function makeGithub({ token, fetchFn = fetch }) {
  return {
    async resolveMain(repository) {
      if (!Object.values(TARGETS).some((item) => item.repository === repository)) fail("E_TARGET", "github-main");
      const response = await boundedFetch(fetchFn, `https://api.github.com/repos/${repository}/commits/main`, {
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
        },
      }, "github-main");
      if (!response.ok) fail("E_GITHUB_API", "github-main");
      const commit = await response.json();
      if (!SHA_RE.test(commit?.sha ?? "")) fail("E_GITHUB_SHAPE", "github-main");
      return commit.sha;
    },
  };
}

async function providerEnvelope(response, stage, uncertain, details) {
  let reader, timer;
  const expires = Date.now() + API_TIMEOUT_MS;
  try {
    reader = response.body.getReader();
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new FastDeployError("E_TIMEOUT", stage, uncertain, details)), API_TIMEOUT_MS);
    });
    const chunks = [];
    let size = 0;
    for (;;) {
      if (Date.now() >= expires) fail("E_TIMEOUT", stage, uncertain, details);
      const { done, value } = await Promise.race([reader.read(), timeout]);
      if (Date.now() >= expires) fail("E_TIMEOUT", stage, uncertain, details);
      if (done) break;
      if (!(value instanceof Uint8Array)) fail("E_PROVIDER_SHAPE", stage, uncertain, details);
      size += value.byteLength;
      if (size > MAX_PROVIDER_BODY_BYTES) fail("E_BODY_LIMIT", stage, uncertain, details);
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
  } catch (error) {
    if (error instanceof FastDeployError) throw error;
    const timeout = error?.name === "AbortError" || error?.name === "TimeoutError";
    fail(timeout ? "E_TIMEOUT" : response.ok ? "E_PROVIDER_SHAPE" : "E_PROVIDER_API", stage, uncertain, details);
  } finally {
    clearTimeout(timer);
    // Cancellation is best effort; cleanup may not hold receipt persistence open.
    try { void reader?.cancel().catch(() => {}); } catch { /* no provider error text */ }
    try { reader?.releaseLock(); } catch { /* a pending read may still be settling */ }
  }
}

export function makeRailway({ token, fetchFn = fetch, sleep = (ms) => new Promise((done) => setTimeout(done, ms)) }) {
  const endpoint = "https://backboard.railway.com/graphql/v2";
  let authenticated = false;
  async function gql(query, variables, stage, timeoutMs = API_TIMEOUT_MS) {
    if (typeof token !== "string" || token.length < 20 || token.length > 512 || /\s/u.test(token)) fail("E_AUTH", "auth-token");
    const response = await boundedFetch(fetchFn, endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Project-Access-Token": token },
      body: JSON.stringify({ query, variables }),
    }, stage, timeoutMs);
    const isMutation = stage === "deploy";
    const uncertain = isMutation || stage === "wait";
    const details = { httpStatus: safeHttpStatus(response.status) };
    const code = response.status === 401 || response.status === 403 ? "E_AUTH" : "E_PROVIDER_API";
    let envelope;
    try {
      envelope = await providerEnvelope(response, stage, uncertain, details);
    } catch (error) {
      if (error instanceof FastDeployError) {
        if (code === "E_AUTH" && error.code === "E_PROVIDER_API") error.code = code;
        throw error;
      }
      const timeout = error?.name === "AbortError" || error?.name === "TimeoutError";
      fail(timeout ? "E_TIMEOUT" : response.ok ? "E_PROVIDER_SHAPE" : code, stage, uncertain, details);
    }
    // Only the already-requested mutation's UUID may cross the error boundary.
    // A partial response never triggers a wait, another mutation, or a retry.
    if (isMutation && isUuid(envelope?.data?.serviceInstanceDeployV2)) {
      details.deploymentId = envelope.data.serviceInstanceDeployV2;
    }
    if (envelope?.errors) {
      details.graphqlCodes = safeGraphqlCodes(envelope.errors);
      if (isMutation) details.deployReason = safeDeployReason(envelope.errors);
    }
    if (!response.ok || envelope?.errors || !envelope?.data) fail(code, stage, uncertain, details);
    if (isMutation && !isUuid(envelope.data.serviceInstanceDeployV2)) {
      fail("E_PROVIDER_SHAPE", stage, true, details);
    }
    return envelope.data;
  }
  return {
    async authenticate() {
      const data = await gql(`query FastDeployAuth($projectId: String!) {
        projectToken { id project { id } environment { id } }
        project(id: $projectId) { id services { edges { node { id } } } }
      }`, { projectId: RAILWAY.projectId }, "authenticate");
      const ids = data.project?.services?.edges?.map(({ node }) => node?.id) ?? [];
      if (!UUID_RE.test(data.projectToken?.id ?? "") || data.projectToken?.project?.id !== RAILWAY.projectId ||
          data.projectToken?.environment?.id !== RAILWAY.environmentId || data.project?.id !== RAILWAY.projectId ||
          !Object.values(TARGETS).every((item) => ids.includes(item.serviceId))) {
        fail("E_AUTH", "authenticate");
      }
      authenticated = true;
    },
    async deploy(serviceId, sha) {
      if (!authenticated || !Object.values(TARGETS).some((item) => item.serviceId === serviceId) || !SHA_RE.test(sha)) {
        fail("E_TARGET", "deploy");
      }
      const data = await gql(`mutation FastDeploy($serviceId: String!, $environmentId: String!, $commitSha: String!) {
        serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId, commitSha: $commitSha)
      }`, { serviceId, environmentId: RAILWAY.environmentId, commitSha: sha }, "deploy", DEPLOY_TIMEOUT_MS);
      if (!UUID_RE.test(data.serviceInstanceDeployV2 ?? "")) fail("E_PROVIDER_SHAPE", "deploy", true);
      return data.serviceInstanceDeployV2;
    },
    async wait(serviceId, deploymentId, sha) {
      const expires = Date.now() + DEPLOY_TIMEOUT_MS;
      for (;;) {
        if (Date.now() >= expires) fail("E_DEPLOY_TIMEOUT", "wait", true);
        const data = await gql(`query FastDeployStatus($environmentId: String!, $serviceId: String!) {
          serviceInstance(serviceId: $serviceId, environmentId: $environmentId) {
            latestDeployment { id status meta deploymentStopped }
            activeDeployments { id status meta deploymentStopped }
          }
        }`, { environmentId: RAILWAY.environmentId, serviceId }, "wait");
        const candidates = [data.serviceInstance?.latestDeployment, ...(data.serviceInstance?.activeDeployments ?? [])];
        const item = candidates.find((candidate) => candidate?.id === deploymentId);
        if (!item) {
          await sleep(5_000);
          continue;
        }
        const observedSha = item.meta?.commitSha ?? item.meta?.commitHash ?? item.meta?.sourceSha ?? item.meta?.repoCommit;
        if (observedSha !== sha) fail("E_DEPLOYED_SHA", "wait");
        if (item.status === "SUCCESS" && item.deploymentStopped === false) return;
        if (!TRANSIENT.has(item.status) && TERMINAL.has(item.status)) fail("E_DEPLOYMENT_FAILED", "wait");
        if (!TRANSIENT.has(item.status)) fail("E_PROVIDER_SHAPE", "wait", true);
        await sleep(5_000);
      }
    },
  };
}

function baseReceipt(request, now) {
  return {
    schemaVersion: "gate.local-deploy/v1",
    operation: "DEPLOY_CURRENT_MAIN",
    environment: "production",
    target: request.target ?? null,
    expectedSourceSha: SHA_RE.test(request.expectedSourceSha ?? "") ? request.expectedSourceSha : null,
    runId: NUMERIC_RE.test(request.runId ?? "") ? request.runId : null,
    executorSha: SHA_RE.test(request.executorSha ?? "") ? request.executorSha : null,
    executorRepository: "markmalina/gate",
    executorPath: ".github/actions/gate-deploy/gate-deploy.mjs",
    upstreamRepository: "markmalina/ops",
    upstreamSha: "02ef5f1c6f4273ca2a3194643383d646dbf37648",
    sourceContract: "gate.public-local-executor.v1",
    startedAt: now(),
    finishedAt: null,
    deployments: [],
    result: "FAILED",
    diagnostic: null,
  };
}

export async function fastDeploy(dependencies, request) {
  const now = dependencies.now ?? (() => new Date().toISOString());
  const receipt = baseReceipt(request, now);
  let stage = "authenticate";
  try {
    await dependencies.railway.authenticate();
    for (const name of selectedTargets(request.target)) {
      const target = TARGETS[name];
      stage = `resolve-${name}`;
      const sha = await dependencies.github.resolveMain(target.repository);
      if (sha !== request.expectedSourceSha) fail("E_SOURCE_DRIFT", "github-main");
      const entry = { name, repository: target.repository, serviceId: target.serviceId, sha, deploymentId: null, status: "REQUESTED" };
      receipt.deployments.push(entry);
      stage = `deploy-${name}`;
      entry.deploymentId = await dependencies.railway.deploy(target.serviceId, sha);
      entry.status = "DEPLOYING";
      stage = `wait-${name}`;
      await dependencies.railway.wait(target.serviceId, entry.deploymentId, sha);
      entry.status = "SUCCESS";
    }
    receipt.result = "SUCCEEDED";
  } catch (error) {
    receipt.diagnostic = safeError(error, stage);
    const active = receipt.deployments.at(-1);
    if (active && error instanceof FastDeployError && isUuid(error.deploymentId)) {
      active.deploymentId = error.deploymentId;
    }
    if (active && active.status !== "SUCCESS") active.status = error?.uncertain ? "UNKNOWN" : "FAILED";
    receipt.result = error?.uncertain ? "UNKNOWN" : "FAILED";
    if (active && receipt.result === "UNKNOWN") receipt.diagnostic.settlement = "UNCONFIRMED";
  } finally {
    receipt.finishedAt = now();
  }
  return receipt;
}

export async function persistReceipt(workspace, receipt) {
  const root = resolve(workspace);
  const path = join(root, "ops-fast-deploy-receipt.json");
  const digestPath = join(root, "ops-fast-deploy-receipt.sha256");
  await mkdir(dirname(path), { recursive: true });
  const content = `${JSON.stringify(receipt, null, 2)}\n`;
  const digest = `sha256:${createHash("sha256").update(content).digest("hex")}`;
  const suffix = randomUUID();
  const receiptTemp = `${path}.${suffix}.tmp`;
  const digestTemp = `${digestPath}.${suffix}.tmp`;
  await writeFile(receiptTemp, content, { mode: 0o600 });
  await writeFile(digestTemp, `${digest}  ${basename(path)}\n`, { mode: 0o600 });
  await rename(digestTemp, digestPath);
  await rename(receiptTemp, path);
  return { path, digestPath, digest };
}

async function writeOutputs(environment, receipt, persisted) {
  if (environment.GITHUB_OUTPUT) {
    await appendFile(environment.GITHUB_OUTPUT, [
      `result=${receipt.result}`,
      `receipt-path=${persisted.path}`,
      `receipt-digest=${persisted.digest}`,
      "",
    ].join("\n"));
  }
  if (environment.GITHUB_STEP_SUMMARY) {
    const rows = receipt.deployments.map((item) => `| ${item.name} | \`${item.sha}\` | \`${item.status}\` |`).join("\n");
    await appendFile(environment.GITHUB_STEP_SUMMARY, [
      "## Ops fast deploy",
      "",
      `Result: \`${receipt.result}\``,
      "",
      "| Service | SHA | Status |",
      "| --- | --- | --- |",
      rows || "| none | — | — |",
      "",
      `Receipt: \`${persisted.digest}\``,
      "",
    ].join("\n"));
  }
}

export async function main(environment = process.env, fetchFn = fetch) {
  let request;
  let receipt;
  try {
    request = validateRuntime(environment);
    const githubToken = sourceWorkflowToken(environment);
    receipt = await fastDeploy({
      github: makeGithub({ token: githubToken, fetchFn }),
      railway: makeRailway({ token: environment.RAILWAY_PROJECT_TOKEN, fetchFn }),
    }, request);
  } catch (error) {
    request ??= {
      target: environment.AGENT_OPS_DEPLOY_TARGET,
      runId: environment.AGENT_OPS_RUN_ID,
      executorSha: environment.AGENT_OPS_EXECUTOR_SOURCE_SHA,
    };
    receipt = baseReceipt(request, () => new Date().toISOString());
    receipt.diagnostic = safeError(error, "bootstrap");
    receipt.finishedAt = new Date().toISOString();
  }
  const persisted = await persistReceipt(environment.GITHUB_WORKSPACE ?? process.cwd(), receipt);
  await writeOutputs(environment, receipt, persisted);
  if (receipt.result !== "SUCCEEDED") process.exitCode = 1;
  return { receipt, persisted };
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  await main();
}


// Gate-local, unregistered maintenance transport. No caller, grants, ledger or
// durable idempotency is supplied here. Constructing/importing performs no I/O.
export class MaintenanceTransportError extends FastDeployError {
  constructor(code, stage, attempted = false, details = {}) {
    super(code, stage, attempted, details);
    this.result = attempted ? "UNKNOWN_STOP" : "FAILED";
    this.retryable = false;
  }
}

export function makeFixedGateMaintenance({ token, fetchFn = fetch, clock = Date.now,
  sleep = (ms) => new Promise((done) => setTimeout(done, ms)) }) {
  let expires = null, requests = 0, stopAttempted = false, stopAcknowledged = false;
  const target = { projectId: RAILWAY.projectId, environmentId: RAILWAY.environmentId,
    serviceId: TARGETS.gate.serviceId };
  const deny = (code, stage, details = {}) => {
    throw new MaintenanceTransportError(code, stage, stopAttempted, details);
  };
  const inputs = (sha, id) => {
    if (typeof sha !== "string" || !SHA_RE.test(sha) || !isUuid(id)) deny("E_INPUT", "input");
  };
  const checkBudget = (deadline = Infinity) => {
    expires ??= clock() + 600_000;
    if (clock() >= Math.min(expires, deadline)) deny("E_TIMEOUT", "maintenance");
    if (requests >= 20) deny("E_REQUEST_LIMIT", "maintenance");
  };
  async function request(query, variables, stage, mutation = false, deadline = Infinity) {
    if (typeof token !== "string" || token.length < 20 || token.length > 512 || /\s/u.test(token)) deny("E_AUTH", "auth-token");
    checkBudget(deadline);
    if (mutation) {
      // Synchronous reservation after all async preflights, before outgoing I/O.
      if (stopAttempted) deny("E_STOP_USED", "stop");
      stopAttempted = true;
    }
    requests++;
    const controller = new AbortController();
    const timeoutMs = Math.min(API_TIMEOUT_MS, expires - clock(), deadline - clock());
    const requestExpires = clock() + timeoutMs;
    let timer, reader;
    let details = {};
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new MaintenanceTransportError("E_TIMEOUT", stage, stopAttempted, details));
      }, timeoutMs);
    });
    try {
      const response = await Promise.race([fetchFn("https://backboard.railway.com/graphql/v2", {
        method: "POST", redirect: "error", cache: "no-store", signal: controller.signal,
        headers: { "Content-Type": "application/json", "Project-Access-Token": token },
        body: JSON.stringify({ query, variables }),
      }), timeout]);
      details = { httpStatus: safeHttpStatus(response.status) };
      reader = response.body.getReader();
      let size = 0;
      const chunks = [];
      for (;;) {
        const { done, value } = await Promise.race([reader.read(), timeout]);
        if (clock() >= requestExpires) deny("E_TIMEOUT", stage, details);
        if (done) break;
        if (!(value instanceof Uint8Array)) deny("E_PROVIDER_SHAPE", stage, details);
        size += value.byteLength;
        if (size > MAX_PROVIDER_BODY_BYTES) deny("E_BODY_LIMIT", stage, details);
        chunks.push(value);
      }
      const envelope = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, size)));
      if (envelope?.errors) details.graphqlCodes = safeGraphqlCodes(envelope.errors);
      if (!response.ok || envelope?.errors || !envelope?.data || typeof envelope.data !== "object" || Array.isArray(envelope.data)) {
        deny(response.status === 401 || response.status === 403 ? "E_AUTH" : "E_PROVIDER_API", stage, details);
      }
      return envelope.data;
    } catch (error) {
      if (error instanceof MaintenanceTransportError) throw error;
      deny(error?.name === "AbortError" || error?.name === "TimeoutError" ? "E_TIMEOUT" : "E_PROVIDER_RESPONSE", stage, details);
    } finally {
      clearTimeout(timer);
      try { void reader?.cancel().catch(() => {}); } catch { /* safe best effort */ }
      try { reader?.releaseLock(); } catch { /* pending read */ }
    }
  }
  async function auth(deadline = Infinity) {
    const data = await request(`query GateMaintenanceAuth($projectId: String!) {
      projectToken { id project { id } environment { id } }
      project(id: $projectId) { id services { edges { node { id } } } }
    }`, { projectId: target.projectId }, "authenticate", false, deadline);
    const services = data.project?.services?.edges;
    if (!isUuid(data.projectToken?.id) || data.projectToken.project?.id !== target.projectId ||
        data.projectToken.environment?.id !== target.environmentId || data.project?.id !== target.projectId ||
        !Array.isArray(services) || services.filter((edge) => edge?.node?.id === target.serviceId).length !== 1) deny("E_AUTH", "authenticate");
  }
  function deployment(item, sha, id) {
    const values = [item?.meta?.commitSha, item?.meta?.commitHash, item?.meta?.sourceSha, item?.meta?.repoCommit]
      .filter((value) => value !== undefined && value !== null);
    const statuses = new Set(["SUCCESS", "FAILED", "CRASHED", "REMOVED", "SKIPPED", "SLEEPING"]);
    if (item?.id !== id || !values.length || values.some((value) => typeof value !== "string" || value !== sha) ||
        typeof item.deploymentStopped !== "boolean" || !statuses.has(item.status)) deny("E_DEPLOYMENT_SCOPE", "inspect");
    return { id, sha, stopped: item.deploymentStopped, status: item.status };
  }
  async function fleet(sha, id, deadline = Infinity) {
    const data = await request(`query GateMaintenanceFleet($projectId: String!, $environmentId: String!, $serviceId: String!) {
      environment(id: $environmentId, projectId: $projectId) { id unmergedChangesCount }
      environmentStagedChanges(environmentId: $environmentId) { environmentId status patch(decryptVariables: false) }
      serviceInstance(serviceId: $serviceId, environmentId: $environmentId) {
        serviceId environmentId numReplicas source { repo image }
        latestDeployment { id status meta deploymentStopped }
        activeDeployments { id status meta deploymentStopped }
      }
      serviceInstanceAutoDeployStatus(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) { enabled }
    }`, target, "fleet", false, deadline);
    const env = data.environment, staged = data.environmentStagedChanges, service = data.serviceInstance;
    if (env?.id !== target.environmentId || !Object.hasOwn(env, "unmergedChangesCount") ||
        (env.unmergedChangesCount !== 0 && env.unmergedChangesCount !== null) ||
        staged?.environmentId !== target.environmentId || staged.status !== "STAGED" ||
        !staged.patch || typeof staged.patch !== "object" || Array.isArray(staged.patch) || Object.keys(staged.patch).length !== 0 ||
        service?.serviceId !== target.serviceId || service.environmentId !== target.environmentId ||
        service.source?.repo !== TARGETS.gate.repository || service.source.image !== null ||
        data.serviceInstanceAutoDeployStatus?.enabled !== false ||
        !Number.isSafeInteger(service.numReplicas) || service.numReplicas < 1 || service.numReplicas > 8 ||
        !Array.isArray(service.activeDeployments) || service.activeDeployments.length > 1 ||
        !Object.hasOwn(service, "latestDeployment")) deny("E_FLEET", "fleet");
    const active = service.activeDeployments.map((item) => deployment(item, sha, id));
    const latest = service.latestDeployment === null ? null : deployment(service.latestDeployment, sha, id);
    if (active.some((item) => item.stopped || item.status !== "SUCCESS") ||
        (latest && !latest.stopped && !active.some((item) => item.id === latest.id)) ||
        (latest && active.length && (latest.stopped !== active[0].stopped || latest.status !== active[0].status))) deny("E_FLEET", "fleet");
    return { ...target, repository: TARGETS.gate.repository, autoDeploy: false, pendingChanges: false,
      numReplicas: service.numReplicas, activeDeployments: active, latestDeployment: latest };
  }
  async function exact(sha, id, deadline = Infinity) {
    const data = await request(`query GateMaintenanceDeployment($id: String!) {
      deployment(id: $id) { id projectId environmentId serviceId status meta deploymentStopped }
    }`, { id }, "inspect", false, deadline);
    const item = data.deployment;
    if (Object.entries(target).some(([key, value]) => item?.[key] !== value)) deny("E_DEPLOYMENT_SCOPE", "inspect");
    return deployment(item, sha, id);
  }
  async function inspectFixedGate(expectedSha, expectedDeploymentId) {
    inputs(expectedSha, expectedDeploymentId);
    await auth();
    const before = await fleet(expectedSha, expectedDeploymentId);
    const recorded = await exact(expectedSha, expectedDeploymentId);
    const after = await fleet(expectedSha, expectedDeploymentId);
    if (JSON.stringify(before) !== JSON.stringify(after) ||
        (after.activeDeployments.length && recorded.stopped !== false)) deny("E_STATE_DRIFT", "inspect");
    return { fleet: after, deployment: recorded };
  }
  return {
    inspectFixedGate,
    async stopExactDeployment({ deploymentId, expectedSha }) {
      inputs(expectedSha, deploymentId);
      if (stopAttempted) deny("E_STOP_USED", "stop");
      const observed = await inspectFixedGate(expectedSha, deploymentId);
      const running = observed.fleet.activeDeployments;
      if (running.length !== 1 || observed.deployment.stopped !== false || observed.deployment.status !== "SUCCESS" ||
          observed.fleet.latestDeployment?.id !== deploymentId) deny("E_NOT_RUNNING", "stop");
      const data = await request(`mutation GateMaintenanceStop($id: String!) { deploymentStop(id: $id) }`,
        { id: deploymentId }, "stop", true);
      if (data.deploymentStop !== true) deny("E_STOP_ACK", "stop");
      stopAcknowledged = true;
      return { deploymentId, acknowledged: true };
    },
    async waitStopped({ deploymentId, expectedSha, deadline }) {
      inputs(expectedSha, deploymentId);
      if (typeof deadline !== "number" || !Number.isFinite(deadline) || deadline <= clock()) deny("E_INPUT", "input");
      if (stopAttempted && !stopAcknowledged) deny("E_STOP_UNCERTAIN", "wait-stopped");
      await auth(deadline);
      for (let poll = 0; poll < 12; poll++) {
        checkBudget(deadline);
        const recorded = await exact(expectedSha, deploymentId, deadline);
        if (recorded.stopped) {
          const before = await fleet(expectedSha, deploymentId, deadline);
          const confirmed = await exact(expectedSha, deploymentId, deadline);
          const after = await fleet(expectedSha, deploymentId, deadline);
          if (!confirmed.stopped || before.activeDeployments.length || after.activeDeployments.length ||
              JSON.stringify(before) !== JSON.stringify(after)) deny("E_NOT_STOPPED", "wait-stopped");
          return { deployment: confirmed, fleet: after, stopped: true };
        }
        if (poll === 11 || clock() + 5_000 >= Math.min(expires, deadline)) deny("E_TIMEOUT", "wait-stopped");
        await sleep(5_000);
      }
    },
  };
}
