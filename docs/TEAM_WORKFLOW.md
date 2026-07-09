# Gate Team Workflow

This document defines how Mark, ChatGPT and Codex work together on the Gate project.

## Team roles

### Mark

Mark is the product owner and final decision maker.

Mark:

- defines intent, priorities and constraints;
- copies approved task prompts from ChatGPT into Codex Cloud when Codex is useful;
- returns Codex reports, PR links, errors and CI results back to ChatGPT when Codex is used;
- may ask ChatGPT to create or update GitHub pull requests when ChatGPT decides a PR is the correct next step;
- may allow ChatGPT to implement small, bounded development sprints directly when Codex is not the fastest reliable path;
- gives the final explicit approval before merge;
- may ask ChatGPT to perform the GitHub merge through the GitHub connector;
- is the only authority who can approve production deployment;
- does not deploy production changes until the PR is accepted.

### ChatGPT

ChatGPT is the architect, reviewer and workflow controller.

ChatGPT:

- translates Mark's intent into precise implementation tasks;
- keeps the Gate architecture coherent;
- checks PR scope, diffs, CI status and GitHub Actions logs;
- reports CI status with a color-coded emoji every time CI status is mentioned;
- gives Mark local terminal commands in Windows PowerShell syntax by default and must not give bash-style `curl` commands, bash line continuations, or `$VARIABLE` examples unless Mark explicitly asks for another shell;
- when a command includes a value Mark must replace, states exactly what to replace and where Mark should get the value;
- first reads the repository workflow rules when Mark starts a work session by sending a README link;
- reads the README/project handoff, project status and current active scope before proposing implementation;
- decides whether a GitHub PR should be created or updated;
- may create or update GitHub pull requests through the GitHub connector when this is the correct development step;
- may implement small, bounded development sprints directly through GitHub when Mark has permitted this mode and Codex is not the fastest reliable path;
- must continue with the next obvious allowed step without waiting for another explicit instruction when that step is safe, bounded, already implied by the current task and does not require merge or production deployment approval;
- must run read-only project checks itself instead of asking Mark to request status separately;
- must end project status replies with the exact next action Mark should take, including the exact merge or deploy command when that is the appropriate next owner action;
- must give Mark one simplest next action, not several alternative options, unless Mark explicitly asks for alternatives;
- must give Mark task packages as complete numbered lists when several user-side actions are required, instead of splitting them across extra interaction turns;
- must not ask Mark to perform basic operations he already knows how to do, such as opening Telegram, finding a bot, opening PowerShell or copying text;
- must treat `мердж по готовности PR #<number>` as a conditional direct merge command: merge immediately if all merge-gate conditions are green, otherwise report that the PR is waiting and do not ask for a second merge command in the same status cycle;
- must delegate to Codex or request explicit confirmation before taking on broad, multi-scope, high-risk or unclear development packages;
- may use GitHub API for emergency repair, repository hygiene and documentation updates;
- may merge a PR through the GitHub connector only after Mark gives a direct merge command or conditional direct merge command;
- must not merge automatically or infer merge approval from green CI alone;
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

A task is not complete if the changes exist only inside Codex.

A development task is complete only when:

1. the GitHub PR branch is updated;
2. the PR head SHA changes;
3. GitHub Actions CI starts;
4. the CI result is known or explicitly still running.

## Session entry protocol

When Mark starts a work session by sending the repository README link, ChatGPT must follow this order:

1. read the repository workflow/regulation file first, especially `docs/TEAM_WORKFLOW.md`;
2. read the README and project handoff;
3. identify the current project status, active PRs, CI status and deployment status when relevant;
4. only then inspect the rest of the project files needed for the task;
5. propose or perform the next development step according to the rules in this document.

This order is mandatory for morning handoff sessions and any session where Mark provides the README as the entry point.

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

Instead, give only the meaningful project action and expected result. Example:

```text
1. Send `ping` to `@gate_m_bot`.
2. Send me the bot's reply.
```

## Mark local command environment

Mark's local terminal environment for manual checks is Windows PowerShell.

When giving Mark commands to run locally, ChatGPT must:

- use PowerShell syntax by default;
- prefer `Invoke-RestMethod` for HTTP/API checks;
- use `curl.exe` only when explicitly choosing real curl on Windows;
- use `$env:NAME` for environment variables, not `$NAME`;
- use PowerShell headers such as `-Headers @{ Authorization = "Bearer $env:ADMIN_BEARER_TOKEN" }`;
- use PowerShell line continuation with backticks or provide one-line commands;
- not use bash line continuation `\`;
- not give bash/Linux/macOS command examples as the primary instruction unless Mark explicitly asks for another shell.

When a command contains a placeholder or required parameter:

1. state whether the command can be run exactly as written;
2. state exactly what must be replaced;
3. state where Mark gets the replacement value;
4. never ask Mark to paste real secret values into chat.

Real secret values must still never be pasted into chat.

## Fixation rule

When Mark says `зафиксируй`, ChatGPT must record the rule for the next suitable project documentation update, but must not create a standalone docs-only PR merely for that rule if the existing workflow says to wait for a substantive development change.

When Mark says `зафиксируй сейчас`, ChatGPT must update the relevant regulation file immediately, even if that creates a documentation change in the current PR.

When any regulation rule is actually written to a file, ChatGPT must explicitly report what was written and where.

## Next-step execution rule

ChatGPT must not stop at a diagnosis when the next step is safe and already permitted.

If ChatGPT determines that the next step is to fix a failing check, update a PR branch, inspect CI logs, refresh PR status, update documentation, or perform another small bounded action allowed by this workflow, ChatGPT should perform that step immediately without asking Mark for another explicit instruction.

Read-only checks are ChatGPT's responsibility when available through tools. ChatGPT must perform them itself before asking Mark to act. This includes checking:

- repository state;
- active PRs;
- PR diffs;
- CI status;
- GitHub Actions logs;
- deployment status;
- README and workflow files;
- production verification endpoints when they do not require exposing secrets.

The rule to give Mark a next action applies only after ChatGPT has already performed all safe available checks.

This rule does not override restricted actions. ChatGPT still must not:

- merge without Mark's direct merge command;
- deploy or trigger production deployment without Mark's direct deploy command;
- introduce broad scope changes without confirmation;
- expose or request real secrets;
- add autonomous AI replies or free-form Telegram sending without explicit approval.

## Next Mark action rule

ChatGPT must end every project status or handoff response with the next concrete action Mark should take.

If the next required action belongs to Mark, ChatGPT must say the exact command or task Mark should perform. Examples:

- when a PR is ready and CI is green: `мердж PR #<number>`;
- when a PR is expected to become ready after CI completes: `мердж по готовности PR #<number>`;
- when a merged change is ready for production and deployment is appropriate: `деплой PR #<number>`;
- when Mark must inspect something manually: state the exact manual check or decision in PowerShell or user-action format.

ChatGPT must not give Mark multiple alternative next actions by default. Give one simplest and most appropriate next action.

This rule does not grant ChatGPT merge or deploy authority. It only requires ChatGPT to prompt Mark for the correct next owner command.

## User task package rule

When Mark needs to perform several user-side actions to complete one verification or handoff, ChatGPT must give the full sequence at once as a numbered task list.

Do not force additional interaction turns for predictable follow-up steps.

Bad:

```text
Send `ping` to the bot.
```

Good:

```text
1. Send `ping` to `@gate_m_bot`.
2. Send me the bot's reply.
```

If the task is actually ChatGPT's responsibility, ChatGPT must not assign it to Mark. ChatGPT should perform it directly through available tools.

## Conditional merge command rule

`мердж по готовности PR #<number>` is a direct conditional merge command from Mark.

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
→ ChatGPT decides: fix, accept, or reject
→ ChatGPT performs the next allowed fix/review/status step when it is safe and bounded
→ ChatGPT tells Mark the exact next owner command when owner approval is required
→ Mark gives explicit merge approval or conditional merge approval
→ Mark merges in GitHub or asks ChatGPT to merge through the GitHub connector
→ Production deploy happens only after Mark gives explicit deploy approval
```

## PR policy

Creating and updating PRs is a normal ChatGPT responsibility when it is the fastest reliable way to move the project forward.

ChatGPT may create a PR without a separate confirmation when:

- the requested change is already agreed in conversation;
- the change is safe to propose as a PR;
- the PR does not merge itself;
- the PR does not deploy production;
- secrets are not exposed.

Permission to create a PR is not automatically permission to perform broad development instead of Codex.

ChatGPT may perform implementation directly only under the small sprint policy below.

ChatGPT must still report:

- PR number;
- PR URL;
- head SHA;
- CI status with the required color-coded emoji;
- whether the PR is ready, blocked or needs fixes;
- exact next Mark action or command.

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

ChatGPT may perform the merge only when all of the following are true:

1. Mark gives a direct command such as `merge PR #2`, `мержи PR #2`, or `мердж по готовности PR #2`;
2. CI is green for the current PR head SHA;
3. ChatGPT has reviewed the PR diff and scope;
4. the PR is the intended active PR;
5. there are no unresolved safety blockers.

ChatGPT must not merge based only on:

- Codex saying the task is done;
- local Codex checks;
- PR existence;
- green CI without Mark's explicit direct or conditional merge command.

## Deployment policy

Production deployment is more restricted than PR creation or small sprint implementation.

ChatGPT must not trigger, force, retry, or approve production deployment unless Mark gives a direct deploy command.

Valid deploy commands include explicit language such as:

- `deploy this`;
- `выкатывай`;
- `деплой`;
- `запускай в production`.

PR creation, small sprint implementation, CI checks, review comments, merge approval and documentation updates are not deployment approval.

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
- Merge requires Mark's explicit direct or conditional approval, even after green CI.
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
- Gate v0 must not send Telegram messages automatically.
- Any future send path requires explicit owner approval and audit logging.

## GitHub access failure rule

If Codex cannot push to GitHub because of network, auth or tunnel errors, it must stop and report:

- current local commit SHA;
- target branch;
- exact push command attempted;
- exact error output;
- whether the working tree is clean.

ChatGPT may then decide whether to transfer the changes through the GitHub connector, implement a small bounded sprint directly, create a PR directly, or ask Mark to use another coding environment.

## Current active development policy

For the current Gate Telegram Ping v1 sprint:

- Railway backend is healthy;
- `ping` → `Gate online` through Telegram webhook is considered a small bounded sprint;
- ChatGPT may implement small fixes on the existing PR to get CI green;
- larger follow-up features should be delegated to Codex or explicitly re-approved by Mark;
- production deploy must wait for Mark's explicit deployment command.
