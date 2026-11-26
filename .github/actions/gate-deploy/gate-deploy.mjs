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

