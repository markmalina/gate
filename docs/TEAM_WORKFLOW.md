# Gate Team Workflow

This document defines how Mark, ChatGPT and Codex work together on the Gate project.

## Team roles

### Mark

Mark is the product owner and final decision maker.

Mark:

- defines intent, priorities and constraints;
- copies approved task prompts from ChatGPT into Codex Cloud;
- returns Codex reports, PR links, errors and CI results back to ChatGPT;
- gives the final explicit approval before merge;
- may ask ChatGPT to perform the GitHub merge through the GitHub connector;
- does not merge PRs while CI is red;
- does not deploy production changes until the PR is accepted.

### ChatGPT

ChatGPT is the architect, reviewer and workflow controller.

ChatGPT:

- translates Mark's intent into precise Codex tasks;
- keeps the Gate architecture coherent;
- checks PR scope, diffs, CI status and GitHub Actions logs;
- decides whether a PR is ready, needs fixes or must be rejected;
- may use GitHub API for emergency repair or repository hygiene;
- may merge a PR through the GitHub connector only after Mark gives a direct merge command;
- must not merge automatically or infer merge approval from green CI alone;
- must not treat Codex summary as truth without checking GitHub.

### Codex

Codex is the implementation agent.

Codex:

- writes code;
- updates the existing PR branch unless explicitly told to create a new PR;
- runs local checks when possible;
- pushes changes to GitHub;
- ensures the PR head SHA changes after work is done;
- waits for or reports GitHub Actions CI status when possible;
- must not claim completion for a local-only commit.

## Source of truth

The source of truth is GitHub, not a local Codex workspace and not a textual summary.

A task is not complete if the changes exist only inside Codex.

A task is complete only when:

1. the GitHub PR branch is updated;
2. the PR head SHA changes;
3. GitHub Actions CI starts;
4. the CI result is known or explicitly still running.

## Standard workflow

```text
Mark describes intent
→ ChatGPT writes a precise Codex task
→ Mark copies the task into Codex Cloud
→ Codex implements and pushes to GitHub PR
→ GitHub Actions CI runs
→ Mark sends Codex report / PR status back to ChatGPT
→ ChatGPT reviews GitHub state, not just the report
→ ChatGPT decides: fix, accept, or reject
→ Mark gives explicit merge approval
→ Mark merges in GitHub or asks ChatGPT to merge through the GitHub connector
```

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

## Codex task rules

Every Codex task must include these rules unless explicitly irrelevant:

```text
Work in the existing PR branch unless told otherwise.
Do not create duplicate PRs.
Do not merge.
Do not mark ready for review unless explicitly instructed.
Do not deploy.
Done means: GitHub PR updated, PR head SHA changed, and CI started.
A local commit without push is not done.
If git push or GitHub access fails, stop and report the exact error.
```

## PR rules

- One task should have one active PR.
- Duplicate PRs must be closed or clearly superseded.
- Draft PRs remain draft until ChatGPT explicitly says they are ready.
- Red CI means the PR is not accepted.
- Green CI is required but not sufficient: ChatGPT still reviews scope and architecture.
- Merge requires Mark's explicit approval, even after green CI.

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

ChatGPT may then decide whether to transfer the changes through the GitHub connector or ask Mark to rerun Codex with proper repository access.

## Current active development policy

For the current bootstrap work:

- primary PR: `#2`;
- duplicate PR `#1` is superseded;
- runtime source root is `app/`;
- old `src/gate` skeleton must not be reintroduced;
- Railway is the primary deployment target;
- PostgreSQL migrations run before backend start.
