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
- decides whether a GitHub PR should be created or updated;
- may create or update GitHub pull requests through the GitHub connector when this is the correct development step;
- may implement small, bounded development sprints directly through GitHub when Mark has permitted this mode and Codex is not the fastest reliable path;
- must delegate to Codex or request explicit confirmation before taking on broad, multi-scope, high-risk or unclear development packages;
- may use GitHub API for emergency repair, repository hygiene and documentation updates;
- may merge a PR through the GitHub connector only after Mark gives a direct merge command;
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

## Standard workflow

```text
Mark describes intent
→ ChatGPT decides the next development step
→ ChatGPT may create/update a GitHub PR directly, implement a small bounded sprint directly, or delegate implementation to Codex
→ GitHub Actions CI runs
→ ChatGPT reviews GitHub state, not just summaries
→ ChatGPT decides: fix, accept, or reject
→ Mark gives explicit merge approval
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
- CI status;
- whether the PR is ready, blocked or needs fixes.

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

1. Mark gives a direct command such as `merge PR #2` or `мержи PR #2`;
2. CI is green;
3. ChatGPT has reviewed the PR diff and scope;
4. the PR is the intended active PR;
5. there are no unresolved safety blockers.

ChatGPT must not merge based only on:

- Codex saying the task is done;
- local Codex checks;
- PR existence;
- green CI without Mark's explicit merge command.

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
- Merge requires Mark's explicit approval, even after green CI.
- Deployment requires Mark's explicit deploy approval, even after merge.

## CI rules

CI is the production gate for code quality.

Required checks:

- `ruff check .`;
- `mypy app tests`;
- `pytest`;
- `alembic upgrade head`;
- `alembic downgrade base`;
- `docker build -t gate:ci .`.

If any check fails, the task is not done.

If a check is skipped because an earlier check failed, the PR is not ready.

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
