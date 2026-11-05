import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { validateRuntime, fastDeploy, FastDeployError } from "../../.github/actions/gate-deploy/gate-deploy.mjs";

const sha = "a".repeat(40);
const trusted = {
 AGENT_OPS_DEPLOY_TARGET: "gate", AGENT_OPS_EXPECTED_SOURCE_SHA: "b".repeat(40),
 AGENT_OPS_CALLER_REPOSITORY: "markmalina/gate", AGENT_OPS_CALLER_REPOSITORY_ID: "1293746267",
 AGENT_OPS_EVENT_NAME: "issue_comment", AGENT_OPS_REF: "refs/heads/main",
 AGENT_OPS_WORKFLOW_REF: "markmalina/gate/.github/workflows/ops-deploy.yml@refs/heads/main",
 AGENT_OPS_RUN_ID: "33000000000", AGENT_OPS_RUN_ATTEMPT: "1",
 AGENT_OPS_ACTOR: "markmalina", AGENT_OPS_ACTOR_ID: "23397294",
 AGENT_OPS_EXECUTOR_REPOSITORY: "markmalina/gate", AGENT_OPS_EXECUTOR_SOURCE_SHA: sha,
 AGENT_OPS_WORKFLOW_SHA: sha, AGENT_OPS_CHECKED_OUT_SHA: sha,
};
test("Gate local executor binds repository ID, workflow bytes and checked-out bytes", () => {
 assert.equal(validateRuntime(trusted).executorSha, sha);
 for (const patch of [
  {AGENT_OPS_CALLER_REPOSITORY_ID:"1"}, {AGENT_OPS_EXECUTOR_REPOSITORY:"markmalina/ops"},
  {AGENT_OPS_WORKFLOW_SHA:"b".repeat(40)}, {AGENT_OPS_CHECKED_OUT_SHA:"b".repeat(40)},
  {AGENT_OPS_WORKFLOW_SHA:undefined}, {AGENT_OPS_CHECKED_OUT_SHA:undefined},
  {AGENT_OPS_CALLER_REPOSITORY:"attacker/gate"}, {AGENT_OPS_ACTOR_ID:"123"},
  {AGENT_OPS_EVENT_NAME:"pull_request_target"}, {AGENT_OPS_RUN_ATTEMPT:"2"},
 ]) assert.throws(()=>validateRuntime({...trusted,...patch}));
});
test("receipt distinguishes actual Gate executor from upstream Ops provenance", async () => {
 const result=await fastDeploy({
  railway:{async authenticate(){throw new FastDeployError("E_AUTH","authenticate");}},
  github:{async resolveMain(){throw new Error("must not be called");}}
 },validateRuntime(trusted));
 assert.equal(result.schemaVersion,"gate.local-deploy/v1");
 assert.notEqual(result.schemaVersion,"agent-ops.fast-deploy/v1");
 assert.equal(result.executorSha,sha);
 assert.equal(result.executorRepository,"markmalina/gate");
 assert.equal(result.upstreamSha,"02ef5f1c6f4273ca2a3194643383d646dbf37648");
 assert.notEqual(result.executorSha,result.upstreamSha);
});
test("future deployment entry stays literal default OFF with unchanged owner command and protected environment", async () => {
 const workflow=await readFile(".github/workflows/ops-deploy.yml","utf8");
 assert.match(workflow,/if: >-\s*\$\{\{\s*false &&/u);
 assert.match(workflow,/github\.repository_id == '1293746267'/u);
 assert.match(workflow,/github\.event\.issue\.number == 751/u);
 assert.match(workflow,/github\.run_attempt == 1/u);
 assert.match(workflow,/environment: gate-production/u);
 assert.match(workflow,/group: gate-production-deploy/u);
 assert.match(workflow,/cancel-in-progress: false/u);
 assert.match(workflow,/persist-credentials: false/u);
 assert.match(workflow,/uses: \.\/\.github\/actions\/gate-deploy/u);
 assert.ok(!workflow.includes("uses: markmalina/ops/"));
 assert.ok(workflow.includes("elif [[ \"$COMMENT_BODY\" =~ ^/ops-deploy\\ production\\ target=gate\\ expected-source-sha=([0-9a-f]{40})$ ]]"));
});
test("local action derives identity from Gate contexts and verifies checkout before entry", async () => {
 const action=await readFile(".github/actions/gate-deploy/action.yml","utf8");
 assert.ok(!action.includes("github.action_ref"));
 assert.ok(!action.includes("github.action_repository"));
 assert.ok(action.includes("AGENT_OPS_EXECUTOR_SOURCE_SHA: ${{ github.sha }}"));
 assert.ok(action.includes("AGENT_OPS_WORKFLOW_SHA: ${{ github.workflow_sha }}"));
 assert.ok(action.indexOf("git -C \"$GITHUB_WORKSPACE\" rev-parse HEAD")<action.indexOf('node "${{ github.action_path }}'));
});
test("all four historical private Ops calls are quarantined, not migrated", async () => {
 const workflow=await readFile(".github/workflows/gate-snapshot-cutover.yml","utf8");
 assert.equal((workflow.match(/uses: AlphaCenter\/ops\//gu)||[]).length,4);
 assert.equal((workflow.match(/\$\{\{ false &&/gu)||[]).length,4);
 assert.match(workflow,/NOT_EXECUTABLE/u);
});

