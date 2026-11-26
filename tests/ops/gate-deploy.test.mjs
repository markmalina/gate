import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  FastDeployError,
  RAILWAY,
  main,
  makeRailway,
  sourceWorkflowToken,
  TARGETS,
  fastDeploy,
  selectedTargets,
  validateRuntime,
} from "../../.github/actions/gate-deploy/gate-deploy.mjs";

const sha = (letter) => letter.repeat(40);
const trusted = {
  AGENT_OPS_DEPLOY_TARGET: "gate",
  AGENT_OPS_EXPECTED_SOURCE_SHA: sha("b"),
  AGENT_OPS_CALLER_REPOSITORY: "markmalina/gate",
  AGENT_OPS_EVENT_NAME: "issue_comment",
  AGENT_OPS_REF: "refs/heads/main",
  AGENT_OPS_WORKFLOW_REF: "markmalina/gate/.github/workflows/ops-deploy.yml@refs/heads/main",
  AGENT_OPS_RUN_ID: "33000000000",
  AGENT_OPS_RUN_ATTEMPT: "1",
  AGENT_OPS_ACTOR: "markmalina",
  AGENT_OPS_ACTOR_ID: "23397294",
  AGENT_OPS_EXECUTOR_REPOSITORY: "markmalina/gate",
  AGENT_OPS_CALLER_REPOSITORY_ID: "1293746267",
  AGENT_OPS_WORKFLOW_SHA: sha("a"),
  AGENT_OPS_CHECKED_OUT_SHA: sha("a"),
  AGENT_OPS_EXECUTOR_SOURCE_SHA: sha("a"),
};

test("Gate caller only, independent of AQUB and stale reruns", () => {
  assert.deepEqual(selectedTargets("gate"), ["gate"]);
  assert.deepEqual(Object.keys(TARGETS), ["gate"]);
  assert.equal(validateRuntime(trusted).target, "gate");
  for (const patch of [
    { AGENT_OPS_DEPLOY_TARGET: "aqub" },
    { AGENT_OPS_DEPLOY_TARGET: "all" },
    { AGENT_OPS_CALLER_REPOSITORY: "AlphaCenter/aqub" },
    { AGENT_OPS_WORKFLOW_REF: "AlphaCenter/aqub/.github/workflows/ops-deploy.yml@refs/heads/main" },
    { AGENT_OPS_ACTOR_ID: "1" }, { AGENT_OPS_RUN_ATTEMPT: "2" },
    { AGENT_OPS_EXECUTOR_SOURCE_SHA: "main" }, { AGENT_OPS_EVENT_NAME: "push" },
    { AGENT_OPS_REF: "refs/heads/other" }, { AGENT_OPS_EXPECTED_SOURCE_SHA: "main" },
  ]) assert.throws(() => validateRuntime({ ...trusted, ...patch }));
});

test("only Gate main is resolved and one Gate service deployed", async () => {
  const calls = [];
  const receipt = await fastDeploy({
    now: () => "2026-10-09T11:12:21.000Z",
    github: { async resolveMain(repo) { calls.push(["resolve", repo]); return sha("b"); } },
    railway: {
      async authenticate() { calls.push(["auth"]); },
      async deploy(service, rev) { calls.push(["deploy", service, rev]); return "11111111-1111-4111-8111-111111111111"; },
      async wait(service, id, rev) { calls.push(["wait", service, id, rev]); },
    },
  }, validateRuntime(trusted));
  assert.equal(receipt.result, "SUCCEEDED");
  assert.deepEqual(receipt.deployments.map(x => x.name), ["gate"]);
  assert.deepEqual(calls[1], ["resolve", "markmalina/gate"]);
  assert.equal(calls[2][1], TARGETS.gate.serviceId);
  assert.equal(calls.length, 4);
});

test("credential mismatch stops before source read or deploy", async () => {
  let effects = 0;
  const receipt = await fastDeploy({
    now: () => "2026-10-09T11:12:21.000Z",
    github: { async resolveMain() { effects++; return sha("b"); } },
    railway: { async authenticate() { throw new FastDeployError("E_AUTH", "authenticate"); } },
  }, validateRuntime(trusted));
  assert.equal(receipt.result, "FAILED");
  assert.equal(effects, 0);
});


test("a changed current main stops before any deploy", async () => {
  let mutations = 0;
  const receipt = await fastDeploy({
    now: () => "2026-10-09T11:12:21.000Z",
    github: { async resolveMain() { return sha("c"); } },
    railway: { async authenticate() {}, async deploy() { mutations++; } },
  }, validateRuntime(trusted));
  assert.equal(receipt.result, "FAILED");
  assert.equal(receipt.diagnostic.code, "E_SOURCE_DRIFT");
  assert.equal(mutations, 0);
});

const workflowToken = "synthetic-workflow-token-for-gate";
const railwayToken = "synthetic-railway-project-token";
const deploymentId = "11111111-1111-4111-8111-111111111111";

function fixtureFetch(calls, { currentSha = sha("b"), githubStatus = 200 } = {}) {
  return async (url, options) => {
    if (url === "https://api.github.com/repos/markmalina/gate/commits/main") {
      assert.equal(options.method, undefined);
      assert.equal(options.headers.Authorization, "Bearer " + workflowToken);
      calls.push("gate-main");
      return Response.json({ sha: currentSha }, { status: githubStatus });
    }
    assert.equal(url, "https://backboard.railway.com/graphql/v2");
    assert.equal(options.headers["Project-Access-Token"], railwayToken);
    const { query, variables } = JSON.parse(options.body);
    if (query.includes("query FastDeployAuth")) {
      calls.push("railway-read-auth");
      assert.equal(variables.projectId, RAILWAY.projectId);
      return Response.json({ data: {
        projectToken: { id: deploymentId, project: { id: RAILWAY.projectId }, environment: { id: RAILWAY.environmentId } },
        project: { id: RAILWAY.projectId, services: { edges: [{ node: { id: TARGETS.gate.serviceId } }] } },
      } });
    }
    assert.equal(variables.serviceId, TARGETS.gate.serviceId);
    assert.equal(variables.environmentId, RAILWAY.environmentId);
    if (query.includes("mutation FastDeploy")) {
      calls.push("gate-deploy");
      assert.equal(variables.commitSha, sha("b"));
      return Response.json({ data: { serviceInstanceDeployV2: deploymentId } });
    }
    assert.match(query, /query FastDeployStatus/u);
    calls.push("gate-read-status");
    return Response.json({ data: { serviceInstance: {
      latestDeployment: { id: deploymentId, status: "SUCCESS", meta: { commitSha: sha("b") }, deploymentStopped: false },
      activeDeployments: [],
    } } });
  };
}

async function invokeMain(patch = {}, fetchFn) {
  const workspace = await mkdtemp(join(tmpdir(), "gate-deploy-auth-test-"));
  const previousExitCode = process.exitCode;
  try {
    const environment = { ...trusted, GITHUB_WORKSPACE: workspace,
      AGENT_OPS_SOURCE_GITHUB_TOKEN: workflowToken, RAILWAY_PROJECT_TOKEN: railwayToken, ...patch };
    const result = await main(environment, fetchFn);
    const raw = await readFile(result.persisted.path, "utf8");
    assert.ok(!raw.includes(workflowToken));
    assert.ok(!raw.includes(railwayToken));
    assert.ok(!raw.includes("SYNTHETIC_PRIVATE_KEY"));
    return result.receipt;
  } finally {
    process.exitCode = previousExitCode;
    await rm(workspace, { recursive: true, force: true });
  }
}

test("real main uses ephemeral Gate token without App calls or permanent key", async () => {
  const calls = [];
  const receipt = await invokeMain({}, fixtureFetch(calls));
  assert.equal(receipt.result, "SUCCEEDED");
  assert.deepEqual(calls, ["railway-read-auth", "gate-main", "gate-deploy", "gate-read-status"]);
  assert.deepEqual(receipt.deployments.map(x => x.repository), ["markmalina/gate"]);
});

test("missing or malformed workflow token has zero network and no App-key fallback", async () => {
  for (const token of [undefined, "", "short", "x".repeat(513), "x".repeat(30) + "\n"]) {
    assert.throws(() => sourceWorkflowToken({ AGENT_OPS_SOURCE_GITHUB_TOKEN: token }), /E_GITHUB_AUTH/u);
    let calls = 0;
    const receipt = await invokeMain({ AGENT_OPS_SOURCE_GITHUB_TOKEN: token,
      AGENT_OPS_REVIEWER_PRIVATE_KEY: "SYNTHETIC_PRIVATE_KEY" }, async () => { calls++; });
    assert.equal(receipt.result, "FAILED");
    assert.deepEqual(receipt.diagnostic, { code: "E_GITHUB_AUTH", stage: "github-workflow-token" });
    assert.equal(calls, 0);
    assert.deepEqual(receipt.deployments, []);
  }
});

test("foreign caller with valid token stops before all network access", async () => {
  let calls = 0;
  const receipt = await invokeMain({ AGENT_OPS_CALLER_REPOSITORY: "AlphaCenter/aqub" }, async () => { calls++; });
  assert.equal(receipt.result, "FAILED");
  assert.equal(receipt.diagnostic.code, "E_TRUSTED_TRIGGER");
  assert.equal(calls, 0);
});

test("real source API failure or drift blocks every deployment mutation", async () => {
  for (const [options, code] of [[{ githubStatus: 401 }, "E_GITHUB_API"], [{ currentSha: sha("c") }, "E_SOURCE_DRIFT"]]) {
    const calls = [];
    const receipt = await invokeMain({}, fixtureFetch(calls, options));
    assert.equal(receipt.result, "FAILED");
    assert.equal(receipt.diagnostic.code, code);
    assert.deepEqual(calls, ["railway-read-auth", "gate-main"]);
    assert.deepEqual(receipt.deployments, []);
  }
});

async function mutationFailure(reply) {
  const calls = [];
  const fallback = fixtureFetch(calls);
  const receipt = await invokeMain({}, async (url, options) => {
    if (url === "https://backboard.railway.com/graphql/v2" && JSON.parse(options.body).query.includes("mutation FastDeploy")) {
      calls.push("gate-deploy");
      return reply();
    }
    return fallback(url, options);
  });
  assert.deepEqual(calls, ["railway-read-auth", "gate-main", "gate-deploy"]);
  assert.equal(receipt.result, "UNKNOWN");
  assert.equal(receipt.deployments[0].status, "UNKNOWN");
  assert.equal(receipt.diagnostic.settlement, "UNCONFIRMED");
  return receipt;
}

test("HTTP failures retain bounded status and never retry the submitted mutation", async () => {
  for (const status of [400, 401, 403, 429, 500, 503]) {
    const r = await mutationFailure(() => new Response("PRIVATE_BODY_DO_NOT_EXPORT", { status }));
    assert.equal(r.diagnostic.httpStatus, status);
    assert.equal(r.diagnostic.code, status === 401 || status === 403 ? "E_AUTH" : "E_PROVIDER_API");
    assert.equal(r.deployments[0].deploymentId, null);
    assert.ok(!JSON.stringify(r).includes("PRIVATE_BODY_DO_NOT_EXPORT"));
  }
});

test("GraphQL errors expose only finite codes and preserve valid partial deployment identity", async () => {
  for (const status of [200, 500]) {
    const r = await mutationFailure(() => Response.json({
      data: { serviceInstanceDeployV2: deploymentId },
      errors: [{ message: workflowToken, path: [railwayToken], extensions: { code: "FORBIDDEN", secret: railwayToken } },
        { message: "SYNTHETIC_PRIVATE_KEY", extensions: { code: "PRIVATE_UNKNOWN_CODE" } }],
    }, { status }));
    assert.equal(r.diagnostic.httpStatus, status);
    assert.deepEqual(r.diagnostic.graphqlCodes, ["FORBIDDEN", "UNCLASSIFIED"]);
    assert.equal(r.deployments[0].deploymentId, deploymentId);
    assert.ok(!JSON.stringify(r).includes("PRIVATE_UNKNOWN_CODE"));
    assert.deepEqual(Object.keys(r.diagnostic).sort(), ["code", "deployReason", "graphqlCodes", "httpStatus", "settlement", "stage"]);
  }
});

test("GraphQL error projection is bounded and rejects malformed or unknown codes", async () => {
  for (const errors of [[], "PRIVATE_RAW_ERROR", [{ extensions: { code: railwayToken } }],
    Array.from({ length: 100 }, (_, i) => ({ extensions: { code: i === 0 ? "RATE_LIMITED" : workflowToken } }))]) {
    const r = await mutationFailure(() => Response.json({ errors }));
    assert.equal(r.diagnostic.httpStatus, 200);
    assert.ok(r.diagnostic.graphqlCodes.length <= 8);
    assert.ok(r.diagnostic.graphqlCodes.every(code => ["RATE_LIMITED", "UNCLASSIFIED"].includes(code)));
  }
});

test("deploy cause is classified only for one exact documented error", async () => {
  const known = { message: "Commit not found", extensions: { code: "BAD_USER_INPUT" } };
  const canary = "SYNTHETIC_PRIVATE_CAUSE_CANARY";
  for (const [errors, reason] of [
    [[known], "COMMIT_NOT_FOUND"],
    [[{ ...known, message: canary }], "UNCLASSIFIED"],
    [[{ ...known, message: "Commit not found " }], "UNCLASSIFIED"],
    [[{ ...known, message: "x".repeat(524000) }], "UNCLASSIFIED"],
    [[{ ...known, message: ["Commit not found"] }], "UNCLASSIFIED"],
    [[{ message: "Commit not found", extensions: { code: "FORBIDDEN" } }], "UNCLASSIFIED"],
    [[{ extensions: { code: "BAD_USER_INPUT" } }], "UNCLASSIFIED"],
    [[known, { message: canary, extensions: { code: "BAD_USER_INPUT" } }], "UNCLASSIFIED"],
    [[known, known], "UNCLASSIFIED"],
    [[], "UNCLASSIFIED"], ["invalid", "UNCLASSIFIED"], [[null], "UNCLASSIFIED"],
  ]) {
    const r = await mutationFailure(() => Response.json({ errors, path: [canary] }));
    assert.equal(r.diagnostic.deployReason, reason);
    assert.ok(!JSON.stringify(r).includes(canary));
    assert.ok(!JSON.stringify(r).includes("Commit not found"));
  }
});

test("documented error at authentication or wait never classifies a deploy cause", async () => {
  for (const target of ["query FastDeployAuth", "query FastDeployStatus"]) {
    const calls = [], fallback = fixtureFetch(calls);
    const r = await invokeMain({}, async (url, options) => {
      if (url === "https://backboard.railway.com/graphql/v2" && JSON.parse(options.body).query.includes(target)) {
        calls.push(target);
        return Response.json({ errors: [{ message: "Commit not found", extensions: { code: "BAD_USER_INPUT" } }] });
      }
      return fallback(url, options);
    });
    assert.equal(r.diagnostic.deployReason, undefined);
    assert.ok(!JSON.stringify(r).includes("Commit not found"));
    assert.equal(r.result, target.includes("Auth") ? "FAILED" : "UNKNOWN");
    assert.equal(calls.filter(x => x === "gate-deploy").length, target.includes("Auth") ? 0 : 1);
  }
});

test("missing or malformed errors on a failed deploy remain unclassified", async () => {
  for (const response of [
    () => Response.json({}),
    () => Response.json({ errors: null }),
    () => Response.json({ errors: false }),
    () => Response.json({ errors: "" }),
    () => Response.json({ data: {} }, { status: 500 }),
    () => new Response("PRIVATE_INVALID_BODY", { status: 500 }),
  ]) {
    const r = await mutationFailure(response);
    assert.equal(r.diagnostic.deployReason, "UNCLASSIFIED");
    assert.ok(!JSON.stringify(r).includes("PRIVATE_INVALID_BODY"));
  }
});

test("malformed body, missing data and invalid deployment IDs remain unconfirmed", async () => {
  for (const reply of [
    () => new Response("PRIVATE_INVALID_JSON", { status: 200 }),
    () => Response.json(null),
    () => Response.json({ data: {} }),
    () => Response.json({ data: { serviceInstanceDeployV2: railwayToken }, errors: [{ extensions: { code: "BAD_USER_INPUT" } }] }),
  ]) {
    const r = await mutationFailure(reply);
    assert.equal(r.diagnostic.httpStatus, 200);
    assert.equal(r.deployments[0].deploymentId, null);
    assert.ok(!JSON.stringify(r).includes("PRIVATE_INVALID_JSON"));
  }
});

test("mutation timeout and network loss are unknown and make no follow-up calls", async () => {
  for (const name of ["AbortError", "TimeoutError", "Error"]) {
    const r = await mutationFailure(() => { const e = new Error(workflowToken); e.name = name; throw e; });
    assert.equal(r.diagnostic.code, name === "Error" ? "E_NETWORK" : "E_TIMEOUT");
    assert.equal(r.diagnostic.httpStatus, undefined);
  }
});

test("body-read timeout retains HTTP status without exposing its exception", async () => {
  const r = await mutationFailure(() => new Response(new ReadableStream({ start(controller) {
    const e = new Error(railwayToken); e.name = "TimeoutError"; controller.error(e);
  } }), { status: 200 }));
  assert.equal(r.diagnostic.code, "E_TIMEOUT");
  assert.equal(r.diagnostic.httpStatus, 200);
});

test("known authentication rejection stays FAILED before any mutation", async () => {
  let calls = 0;
  const r = await invokeMain({}, async () => {
    calls++;
    return Response.json({ errors: [{ message: railwayToken, extensions: { code: "FORBIDDEN" } }] }, { status: 403 });
  });
  assert.equal(calls, 1);
  assert.equal(r.result, "FAILED");
  assert.deepEqual(r.deployments, []);
  assert.deepEqual(r.diagnostic, { code: "E_AUTH", stage: "authenticate", httpStatus: 403, graphqlCodes: ["FORBIDDEN"] });
});

test("observed terminal deployment failure stays FAILED with its known ID", async () => {
  const calls = [];
  const fallback = fixtureFetch(calls);
  const r = await invokeMain({}, async (url, options) => {
    if (url === "https://backboard.railway.com/graphql/v2" && JSON.parse(options.body).query.includes("query FastDeployStatus")) {
      calls.push("gate-read-status");
      return Response.json({ data: { serviceInstance: { latestDeployment: {
        id: deploymentId, status: "FAILED", meta: { commitSha: sha("b") }, deploymentStopped: true,
      }, activeDeployments: [] } } });
    }
    return fallback(url, options);
  });
  assert.equal(r.result, "FAILED");
  assert.equal(r.deployments[0].deploymentId, deploymentId);
  assert.equal(r.diagnostic.code, "E_DEPLOYMENT_FAILED");
  assert.deepEqual(calls, ["railway-read-auth", "gate-main", "gate-deploy", "gate-read-status"]);
});

test("stalled body hits the real reader deadline and does not await hung cancellation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  const calls = [], fallback = fixtureFetch(calls);
  let controller, cancels = 0, settled = false;
  const response = new Response(new ReadableStream({
    start(value) { controller = value; },
    cancel() { cancels++; return new Promise(() => {}); },
  }), { status: 503 });
  const railway = makeRailway({ token: railwayToken, fetchFn: async (url, options) => {
    if (JSON.parse(options.body).query.includes("mutation FastDeploy")) { calls.push("gate-deploy"); return response; }
    return fallback(url, options);
  } });
  const pending = fastDeploy({ railway, github: { async resolveMain() { calls.push("gate-main"); return sha("b"); } } },
    validateRuntime(trusted)).then((value) => { settled = true; return value; });
  try {
    await new Promise(setImmediate);
    assert.equal(settled, false);
    t.mock.timers.tick(15_001);
    await new Promise(setImmediate);
    assert.equal(settled, true, "15s must settle an actually stalled stream");
    const r = await pending;
    assert.equal(r.result, "UNKNOWN");
    assert.deepEqual(r.diagnostic, { code: "E_TIMEOUT", stage: "deploy", httpStatus: 503,
      deployReason: "UNCLASSIFIED", settlement: "UNCONFIRMED" });
    assert.equal(cancels, 1);
    assert.deepEqual(calls, ["railway-read-auth", "gate-main", "gate-deploy"]);
  } finally {
    try { controller.close(); } catch { /* already cancelled */ }
    await pending;
    t.mock.timers.reset();
  }
});

test("provider body cap rejects bytes above512KiB and preserves HTTP status", async () => {
  const r = await mutationFailure(() => new Response("PRIVATE_BODY".repeat(50_000), { status: 500 }));
  assert.equal(r.diagnostic.code, "E_BODY_LIMIT");
  assert.equal(r.diagnostic.httpStatus, 500);
  assert.ok(!JSON.stringify(r).includes("PRIVATE_BODY"));
});

test("a complete valid body at512KiB retains the ordinary success path", async () => {
  const calls = [], fallback = fixtureFetch(calls);
  const json = JSON.stringify({ data: { serviceInstanceDeployV2: deploymentId } });
  const r = await invokeMain({}, async (url, options) => {
    if (url === "https://backboard.railway.com/graphql/v2" && JSON.parse(options.body).query.includes("mutation FastDeploy")) {
      calls.push("gate-deploy");
      return new Response(json.padEnd(512 * 1024, " "));
    }
    return fallback(url, options);
  });
  assert.equal(r.result, "SUCCEEDED");
  assert.equal(r.diagnostic, null);
  assert.deepEqual(calls, ["railway-read-auth", "gate-main", "gate-deploy", "gate-read-status"]);
});

test("new partial-ID boundaries reject array object number and boolean values", async () => {
  for (const value of [[deploymentId], [[deploymentId]], { id: deploymentId }, 123, true]) {
    const r = await mutationFailure(() => Response.json({ data: { serviceInstanceDeployV2: value },
      errors: [{ extensions: { code: "INTERNAL_SERVER_ERROR" } }] }));
    assert.equal(r.deployments[0].deploymentId, null);
    assert.ok(!JSON.stringify(r).includes(deploymentId));
    const error = new FastDeployError("E_PROVIDER_API", "deploy", true, { deploymentId: value });
    assert.equal(error.deploymentId, undefined);
  }
});

test("wait API failures are UNKNOWN with known ID and no retry", async () => {
  for (const reply of [
    () => Response.json({ errors: [{ extensions: { code: "INTERNAL_SERVER_ERROR" } }] }, { status: 500 }),
    () => Response.json({ errors: [{ extensions: { code: "FORBIDDEN" } }] }),
    () => new Response("PRIVATE_INVALID_WAIT_JSON"),
  ]) {
    const calls = [], fallback = fixtureFetch(calls);
    const r = await invokeMain({}, async (url, options) => {
      if (url === "https://backboard.railway.com/graphql/v2" && JSON.parse(options.body).query.includes("query FastDeployStatus")) {
        calls.push("gate-read-status"); return reply();
      }
      return fallback(url, options);
    });
    assert.equal(r.result, "UNKNOWN");
    assert.equal(r.deployments[0].deploymentId, deploymentId);
    assert.equal(r.diagnostic.stage, "wait");
    assert.equal(r.diagnostic.settlement, "UNCONFIRMED");
    assert.ok(!JSON.stringify(r).includes("PRIVATE_INVALID_WAIT_JSON"));
    assert.deepEqual(calls, ["railway-read-auth", "gate-main", "gate-deploy", "gate-read-status"]);
  }
});
