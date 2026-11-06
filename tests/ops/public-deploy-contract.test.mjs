import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
test("activation entry retains exact owner/main/event guards and protected environment", async () => {
 const workflow=await readFile(".github/workflows/ops-deploy.yml","utf8");
 const condition=workflow.match(/    if: >-\n      \$\{\{([\s\S]*?)\n      \}\}/u)?.[1];
 assert.ok(condition);
 const expected=`github.repository == 'markmalina/gate' &&
 github.repository_id == '1293746267' && github.run_attempt == 1 &&
 github.ref == 'refs/heads/main' && github.actor == 'markmalina' &&
 github.actor_id == '23397294' && (github.event_name == 'workflow_dispatch' ||
 (github.event_name == 'issue_comment' && github.event.issue.number == 751 &&
 !github.event.issue.pull_request &&
 startsWith(github.event.comment.body, '/ops-deploy production target=')))`;
 assert.equal(condition.replace(/\s+/gu,""),expected.replace(/\s+/gu,""));
 // Execute the actual boolean expression on synthetic contexts; no action runs.
 const allowed=new Function("github","startsWith",`return (${condition});`);
 const evaluate=(g)=>allowed(g,(value,prefix)=>value.startsWith(prefix));
 const github={repository:"markmalina/gate",repository_id:"1293746267",run_attempt:1,
  ref:"refs/heads/main",actor:"markmalina",actor_id:"23397294",event_name:"issue_comment",
  event:{issue:{number:751},comment:{body:`/ops-deploy production target=gate expected-source-sha=${sha}`}}};
 assert.equal(evaluate(github),true);
 assert.equal(evaluate({...github,event_name:"workflow_dispatch",event:{}}),true);
 for(const patch of [
  {repository:"attacker/gate"},{repository_id:"1"},{run_attempt:2},
  {ref:"refs/heads/agent/796-w1-guard"},{actor:"attacker"},{actor_id:"1"},
  {event_name:"push"},{event_name:"pull_request"},{event_name:"pull_request_target"},
  {event:{...github.event,issue:{number:752}}},
  {event:{...github.event,issue:{number:751,pull_request:{}}}},
  {event:{...github.event,comment:{body:"not an owner command"}}},
 ]) assert.equal(evaluate({...github,...patch}),false,JSON.stringify(patch));
 assert.match(workflow,/github\.repository_id == '1293746267'/u);
 assert.match(workflow,/github\.event\.issue\.number == 751/u);
 assert.match(workflow,/github\.run_attempt == 1/u);
 assert.match(workflow,/environment: gate-production/u);
 assert.match(workflow,/group: gate-production-deploy/u);
 assert.match(workflow,/cancel-in-progress: false/u);
 assert.match(workflow,/persist-credentials: false/u);
 assert.match(workflow,/uses: \.\/\.github\/actions\/gate-deploy/u);
 assert.match(workflow,/permissions:\s+contents: read\s+issues: read/u);
 assert.ok(workflow.includes("target: gate"));
 assert.ok(workflow.includes("test \"$APPROVED_SOURCE_SHA\" = \"$EXPECTED_SHA\""));
 assert.ok(workflow.includes("node .github/actions/gate-deploy/release-checks.mjs"));
 assert.ok(!workflow.includes("uses: markmalina/ops/"));
 assert.ok(workflow.includes("elif [[ \"$COMMENT_BODY\" =~ ^/ops-deploy\\ production\\ target=gate\\ expected-source-sha=([0-9a-f]{40})$ ]]"));
});
test("actual owner-command script rejects wrong owner, target, grammar and manual SHA", async () => {
 const workflow=await readFile(".github/workflows/ops-deploy.yml","utf8");
 const block=workflow.split("      - name: Resolve exact owner command\n")[1]
  .split("\n      - name: Checkout exact trusted Gate executor")[0];
 const script=block.split("        run: |\n")[1].split("\n")
  .map(line=>line.startsWith("          ")?line.slice(10):line).join("\n");
 const directory=await mkdtemp(join(tmpdir(),"gate-owner-command-"));
 const valid={ACTOR:"markmalina",ACTOR_ID:"23397294",EVENT_NAME:"issue_comment",
  COMMENT_BODY:`/ops-deploy production target=gate expected-source-sha=${sha}`,INPUT_SOURCE_SHA:sha};
 try {
  const cases=[{}, {EVENT_NAME:"workflow_dispatch"},
   // Event admission is enforced by the actual job-if cases above.
   {ACTOR:"attacker"},{ACTOR_ID:"1"},
   {COMMENT_BODY:valid.COMMENT_BODY.replace("target=gate","target=aqub")},
   {COMMENT_BODY:valid.COMMENT_BODY+" extra"},{COMMENT_BODY:valid.COMMENT_BODY+"\n"},
   {COMMENT_BODY:valid.COMMENT_BODY.replace(sha,"main")},
   {EVENT_NAME:"workflow_dispatch",INPUT_SOURCE_SHA:""},
   {EVENT_NAME:"workflow_dispatch",INPUT_SOURCE_SHA:"A".repeat(40)}];
  for(let i=0;i<cases.length;i++) {
   const output=join(directory,`output-${i}`);
   const child=spawnSync("bash",["-c",script],{encoding:"utf8",
    env:{PATH:process.env.PATH,...valid,...cases[i],GITHUB_OUTPUT:output}});
   assert.equal(child.status===0,i<2,JSON.stringify(cases[i]));
   if(i<2) assert.equal(await readFile(output,"utf8"),`source_sha=${sha}\n`);
  }
 } finally {await rm(directory,{recursive:true,force:true});}
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

