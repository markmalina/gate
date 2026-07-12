# Gate Team Workflow

This document defines how Mark, ChatGPT and Codex work together on the Gate project.

## Team roles

### Mark

Mark is the product owner and final decision maker.

Mark:

- defines intent, priorities and constraints;
- may ask ChatGPT to create or update GitHub pull requests when ChatGPT decides a PR is the correct next step;
- may allow ChatGPT to implement small, bounded development sprints directly when Codex is not the fastest reliable path;
- grants standing authorization for ChatGPT to merge ChatGPT-managed Gate PRs when every auto-merge condition in this file is satisfied;
- is the only authority who can approve production deployment;
- does not approve production deployment merely by approving or allowing a merge.

### ChatGPT

ChatGPT is the architect, reviewer and workflow controller.

ChatGPT:

- translates Mark's intent into precise implementation tasks;
- keeps the Gate architecture coherent;
- checks PR scope, diffs, CI status and GitHub Actions logs;
- reports CI status with a color-coded emoji every time CI status is mentioned;
- first reads the repository workflow rules when Mark starts a work session by sending a README link;
- reads the README/project handoff, project status and current active scope before proposing implementation;
- before any project action, runs the pre-action rule check against the priority order in this document;
- may create or update GitHub pull requests through the GitHub connector when this is the correct development step;
- may implement small, bounded development sprints directly through GitHub when Mark has permitted this mode and Codex is not the fastest reliable path;
- must continue with the next obvious allowed step without waiting for another explicit instruction when that step is safe, bounded, already implied by the current task and does not require production deployment approval;
- must run read-only project checks itself instead of asking Mark to request status separately;
- must give Mark one simplest next action, not several alternative options, unless Mark explicitly asks for alternatives;
- must not ask Mark to perform basic operations he already knows how to do;
- may merge a ChatGPT-managed Gate PR without a separate merge command only when the standing auto-merge rule is satisfied;
- must not deploy to production or trigger production deployment unless Mark gives a direct deployment command;
- must not treat Codex summary as truth without checking GitHub.

### Codex

Codex is an implementation assistant, not the source of truth.

Codex:

- may write code or generate patches;
- may be asked to implement a task when its environment has real repository access;
- remains the preferred implementation path for larger or multi-file feature packages when its environment can create PRs reliably;
- must stop if it cannot access GitHub, push branches or create PRs;
- must not claim completion for a local-only commit.

## Source of truth

The source of truth is GitHub, not a local Codex workspace and not a textual summary.

A development task is complete only when:

1. the GitHub PR branch is updated;
2. the PR head SHA changes;
3. GitHub Actions CI starts;
4. the CI result is known or explicitly still running.

## Rule priority order

Rules in this document are enforced in priority order. If a lower-priority rule conflicts with a higher-priority rule, the higher-priority rule wins.

Priority order:

1. Secrets and safety restrictions.
2. Production deployment restrictions.
3. Merge restrictions.
4. Fixation gate: `зафиксируй` vs `зафиксируй сейчас` vs a substantive project PR.
5. Scope restrictions: no broad, multi-scope, high-risk or unclear changes without confirmation or Codex delegation.
6. GitHub source-of-truth and CI/review gates.
7. Error correction rule.
8. Next-step execution rule.
9. Next Mark action and user task package rules.
10. Reporting style and wording rules.

Before any project action, ChatGPT must run this pre-action rule check:

1. Identify the planned action.
2. Identify which rule authorizes the action.
3. Check all higher-priority rules first.
4. If any higher-priority rule blocks the action, do not perform it.
5. If performing the action violates a lower-priority rule because a higher-priority rule requires the action, explain exactly which higher-priority rule overrides which lower-priority rule.
6. If rules at the same priority conflict, stop and ask Mark for an explicit decision.

## Session entry protocol

When Mark starts a work session by sending the repository README link, ChatGPT must follow this order:

1. read the repository workflow/regulation file first, especially `docs/TEAM_WORKFLOW.md`;
2. read the README and project handoff;
3. identify the current project status, active PRs, CI status and deployment status when relevant;
4. only then inspect the rest of the project files needed for the task;
5. propose or perform the next development step according to the rules in this document.

## Mark known abilities

Mark already knows how to perform basic local and user-interface operations. ChatGPT must not spend instructions on these basics unless Mark explicitly asks for them.

Do not tell Mark to:

- open Telegram;
- find a bot in Telegram;
- open PowerShell;
- copy text;
- paste text;
- press Enter;
- switch windows;
- perform other obvious UI mechanics.

Instead, give only the meaningful project action and expected result.

## Mark local command environment

Mark's local terminal environment for manual checks is Windows PowerShell.

When giving Mark commands to run locally, ChatGPT must:

- use PowerShell syntax by default;
- prefer `Invoke-RestMethod` for HTTP/API checks;
- use `curl.exe` only when explicitly choosing real curl on Windows;
- use `$env:NAME` for environment variables, not `$NAME`;
- not use bash/Linux/macOS command examples as the primary instruction unless Mark explicitly asks for another shell;
- never ask Mark to paste real secret values into chat.

## Fixation rule

When Mark says `зафиксируй`, ChatGPT must record the rule for the next suitable project documentation update, but must not create a standalone docs-only PR merely for that rule if the existing workflow says to wait for a substantive development change.

When Mark says `зафиксируй сейчас`, ChatGPT must update the relevant regulation file immediately, even if that creates a documentation change in the current PR.

When any regulation rule is actually written to a file, ChatGPT must explicitly report what was written and where.

## Error correction rule

When ChatGPT makes an error, it must:

1. correct the behavior in the moment first;
2. avoid pushing the error back to Mark as an extra action when ChatGPT can safely fix it;
3. explain why it corrected course, citing the relevant workflow priorities;
4. identify the gap that allowed the error;
5. propose or write a workflow rule to prevent recurrence when appropriate.

## Next-step execution rule

ChatGPT must not stop at a diagnosis when the next step is safe and already permitted.

If ChatGPT determines that the next step is to fix a failing check, update a PR branch, inspect CI logs, refresh PR status, update documentation, merge an eligible auto-merge PR, or perform another small bounded action allowed by this workflow, ChatGPT should perform that step immediately without asking Mark for another explicit instruction.

Read-only checks are ChatGPT's responsibility when available through tools. ChatGPT must perform them itself before asking Mark to act. This includes checking:

- repository state;
- active PRs;
- PR diffs;
- CI status;
- GitHub Actions logs;
- deployment status;
- README and workflow files;
- production verification endpoints when they do not require exposing secrets.

## User task package rule

When Mark needs to perform several user-side actions to complete one verification or handoff, ChatGPT must give the full sequence at once as a numbered task list.

If the task is actually ChatGPT's responsibility, ChatGPT must not assign it to Mark. ChatGPT should perform it directly through available tools.

## Standing auto-merge rule

Mark grants standing authorization for ChatGPT to merge ChatGPT-managed Gate PRs without asking for a separate merge command when all conditions below are true.

ChatGPT must auto-merge the PR when:

1. the PR implements a small bounded sprint, hotfix, repository hygiene update, or documentation/workflow update already requested or implied by the current approved workstream;
2. the PR is the intended active PR and there is no duplicate active PR for the same task;
3. ChatGPT has fetched the current PR state from GitHub;
4. ChatGPT has verified the current PR head SHA;
5. GitHub Actions CI is 🟢 green for that exact current head SHA;
6. the PR is mergeable;
7. ChatGPT has reviewed the PR diff and confirmed the scope still matches the intended task;
8. there are no unresolved safety, security, secret, architecture or production-deployment blockers.

When all conditions are satisfied, ChatGPT must merge the PR through the GitHub connector immediately and report the merge commit. It must not ask Mark for `мердж PR #...` again.

If any condition is not satisfied, ChatGPT must not merge. It must either fix the PR when the fix is small and safe, or report the exact blocker.

This standing auto-merge authorization does **not** authorize production deployment.

## Conditional merge command rule

`мердж по готовности PR #<number>` is still a valid direct conditional merge command from Mark. It is now a fallback explicit command, not a requirement for every green PR.

When Mark sends this command, ChatGPT must:

1. fetch the current PR state;
2. verify the active head SHA;
3. verify the latest CI result for that exact head SHA;
4. review that the PR scope remains the intended active PR;
5. if CI is 🟢 green, the PR is mergeable and there are no safety blockers, merge the PR without asking for another merge command;
6. if CI is 🟡 pending/queued/in progress, 🔴 failed, or ⚪ unknown, do not merge and report that the PR is waiting or blocked.

The conditional merge command does not authorize production deployment.

## Standard workflow

```text
Mark describes intent
→ ChatGPT decides the next development step
→ ChatGPT may create/update a GitHub PR directly, implement a small bounded sprint directly, or delegate implementation to Codex
→ GitHub Actions CI runs
→ ChatGPT reviews GitHub state, not just summaries
→ ChatGPT decides: fix, accept, reject, or auto-merge if the standing auto-merge rule is satisfied
→ ChatGPT performs the next allowed fix/review/status/merge step when it is safe and bounded
→ Production deploy happens only after Mark gives explicit deploy approval
```

## PR policy

Creating and updating PRs is a normal ChatGPT responsibility when it is the fastest reliable way to move the project forward.

ChatGPT may create a PR without a separate confirmation when:

- the requested change is already agreed in conversation;
- the change is safe to propose as a PR;
- the PR does not deploy production;
- secrets are not exposed.

Permission to create a PR is not automatically permission to perform broad development instead of Codex.

## Small sprint development policy

ChatGPT may perform development directly instead of Codex only for small, bounded sprints.

A small sprint means:

- one narrow product behavior, bug fix or documentation update;
- clear acceptance criteria;
- limited architectural risk;
- no broad refactor;
- no large multi-module redesign;
- no secret handling beyond references to environment variable names;
- no production deployment;
- CI can validate the change.

Examples of small sprints:

- add one endpoint with tests;
- fix one failing CI issue;
- add one safe predefined Telegram response rule;
- update documentation or workflow policy;
- adjust configuration plumbing for one already-defined behavior.

Not small sprints:

- redesign Gate routing architecture;
- add AI reply generation;
- implement broad approval workflows end-to-end;
- change deployment infrastructure substantially;
- rewrite database schema across multiple domains;
- introduce autonomous sending beyond predefined safe rules.

When the scope is not clearly small, ChatGPT must either delegate to Codex or ask Mark for explicit confirmation before implementing.

## Merge policy

Merging is a privileged action.

ChatGPT may perform the merge only when one of the following authorizations exists:

1. the standing auto-merge rule is satisfied;
2. Mark gives a direct command such as `merge PR #2`, `мержи PR #2`, or `мердж по готовности PR #2`.

In all cases, ChatGPT must also verify:

1. CI is 🟢 green for the current PR head SHA;
2. ChatGPT has reviewed the PR diff and scope;
3. the PR is the intended active PR;
4. the PR is mergeable;
5. there are no unresolved safety blockers.

ChatGPT must not merge based only on:

- Codex saying the task is done;
- local Codex checks;
- PR existence;
- green CI without either the standing auto-merge rule being satisfied or Mark's direct/conditional merge command.

## Deployment policy

Production deployment is more restricted than PR creation, small sprint implementation or PR merge.

ChatGPT must not trigger, force, retry or approve production deployment unless Mark gives a direct deploy command.

Valid deploy commands include explicit language such as:

- `deploy this`;
- `выкатывай`;
- `деплой`;
- `запускай в production`.

PR creation, small sprint implementation, CI checks, review comments, merge approval, auto-merge and documentation updates are not deployment approval.

## Codex task rules

Every Codex task must include these rules unless explicitly irrelevant:

```text
Work in the existing PR branch unless told otherwise.
Do not create duplicate PRs.
Do not merge.
Do not deploy.
Done means: GitHub PR updated, PR head SHA changed, and CI started.
A local commit without push is not done.
If git push or GitHub access fails, stop and report the exact error.
```

## PR rules

- One task should have one active PR.
- Duplicate PRs must be closed or clearly superseded.
- Red CI means the PR is not accepted.
- Green CI is required but not sufficient: ChatGPT still reviews scope and architecture.
- Merge is allowed when the standing auto-merge rule is satisfied or Mark gives explicit direct/conditional approval.
- Deployment requires Mark's explicit deploy approval, even after merge.
- If CI fails for a small, obvious and safe reason, ChatGPT should inspect the failure and push a fix to the same PR branch without waiting for a separate instruction.

## CI rules

CI is the production gate for code quality.

When reporting CI status, ChatGPT must prefix the status with a color-coded emoji:

- 🟢 green/passed/success;
- 🟡 pending/queued/in progress;
- 🔴 red/failed/cancelled;
- ⚪ unknown/not started/not available.

Required checks:

- `ruff check .`;
- `mypy app tests`;
- `pytest`;
- `alembic upgrade head`;
- `alembic downgrade base`;
- `docker build -t gate:ci .`.

If any check fails, the task is not done.

If a check is skipped because an earlier check failed, the PR is not ready.

When a CI failure is in an existing ChatGPT-managed PR and the needed fix is small, clear and safe, ChatGPT should make the fix directly, push it to the PR branch and re-check CI status.

## Secrets and safety

- Telegram tokens must never be written in chat, README, source code, tests or committed files.
- Real secrets belong only in Railway or GitHub secret storage.
- `.env.example` may contain variable names and safe defaults only.
- Gate must not send Telegram messages automatically unless the product scope explicitly defines a safe/manual send path.
- Any future send path requires explicit owner approval and audit logging.

## GitHub access failure rule

If Codex cannot push to GitHub because of network, auth or tunnel errors, it must stop and report:

- current local commit SHA;
- target branch;
- exact push command attempted;
- exact error output;
- whether the working tree is clean.

ChatGPT may then decide whether to transfer the changes through the GitHub connector, implement a small bounded sprint directly, create a PR directly, or ask Mark to use another coding environment.
