# Gate Workflow Supplement

This supplement is part of the Gate working regulation.

## Regulation-only PR gate

Project, product, and code PRs are allowed when ChatGPT decides they are the correct bounded development step.

Standalone regulation-only or workflow-only PRs are allowed only when Mark explicitly says `зафиксируй сейчас`, explicitly asks for a standalone regulation PR, or the regulation change is included in an already suitable substantive project PR.

When Mark says only `зафиксируй`, ChatGPT queues the rule for the next suitable substantive project PR and does not create a standalone regulation-only PR.

## ChatGPT-default Gate development mode

For Gate, the default development mode is direct ChatGPT implementation through the GitHub connector, not Codex delegation.

ChatGPT should implement directly when all of the following are true:

1. the task is a small bounded product/code/docs sprint;
2. the scope is narrow and understandable from the current repository state;
3. the expected change is roughly 1-5 files, unless a small migration/test file makes the count slightly higher;
4. GitHub CI can validate the change;
5. no local-only debugging, broad refactor or complex multi-module work is required;
6. no secrets need to be viewed, pasted or committed;
7. merge is either explicitly approved for the named PR or covered by an active standing merge authorization from Mark.

Codex is no longer the default Gate implementation path. Codex should be used only as a backup/local implementation assistant when the task is large, broad, multi-file, local-test-heavy, unclear, or better suited to a local repository environment.

When Codex is not used, ChatGPT owns the full GitHub workflow for the bounded sprint: create branch, edit files, open or update PR, inspect CI, fix small CI failures, review scope, and merge when the applicable authorization rule permits it.

## Single merge command execution rule

A single direct merge command from Mark is complete merge authorization for the named pull request.

Direct merge commands include `мердж PR #<number>`, `мержи PR #<number>`, `merge PR #<number>`, `merge if ready`, and equivalent wording that names or clearly refers to the active pull request.

After receiving a direct merge command, ChatGPT must:

1. fetch the current PR state;
2. verify the exact current head SHA;
3. verify that CI is green for that exact head SHA;
4. review that the PR scope and diff still match the intended active PR;
5. if the PR is mergeable and there are no safety blockers, merge immediately through the GitHub connector;
6. if blocked, report the specific blocker.

ChatGPT must not ask Mark to repeat the same merge command after these checks pass.

## Standing merge authorization

Mark may grant a standing merge authorization for Gate development.

While that authorization is active, ChatGPT may merge an active Gate PR without requesting another merge command only when all of the following are true:

1. the PR implements the already approved bounded sprint or task;
2. the final diff contains no unrelated scope;
3. the exact current head SHA has green CI;
4. the PR is mergeable;
5. review found no unresolved safety, data-loss, secret, or architecture blocker;
6. the merge does not authorize any separate external action beyond the repository merge.

If any condition is not met, ChatGPT must not merge and must report or fix the blocker first.

Mark's instruction on 2026-07-11 — `Мерджи сам без моей команды если все в порядке` — activates standing merge authorization for subsequent Gate PRs until Mark revokes or narrows it.

## Automatic Railway deployment

Gate's current Railway production service deploys automatically after a merge to `main`.

The operational sequence is:

```text
merge to main
→ automatic Railway deployment
→ production verification
```

A separate deploy command is not required while this automatic deployment configuration remains active. Merge authorization is still governed by the direct or standing merge authorization rules above.

## Error correction final-output gate

After any ChatGPT workflow error, the final response must include:

1. immediate correction or why correction cannot be done;
2. violated rule;
3. whether any higher-priority rule justified the action;
4. root cause or gap;
5. proposed prevention rule;
6. fixation status: written now, written in a suitable substantive PR, queued, or blocked by the fixation gate.

If any item is missing, the error explanation is incomplete.

## Codex handoff rule

In the current Gate workflow, Codex is a local implementation and handoff assistant only.

Codex tasks must request local work and a final local handoff summary. ChatGPT owns GitHub publication through the GitHub connector after reviewing that handoff.

A Codex handoff should include local implementation status, changed files, commands run, results, errors if any, and known risks.

## Codex patch output contract

When Codex is used for local implementation, the deliverable is the full cumulative patch, not a summary and not a single last-commit patch.

Every Codex implementation task must include this output contract unless explicitly irrelevant:

```text
Before making changes, run:

git rev-parse HEAD

Save the result as BASE_SHA.

After all implementation and fixes are complete, run:

git rev-parse HEAD
git status --short
git diff --binary BASE_SHA...HEAD

The final Codex response must contain:

1. BASE_SHA.
2. HEAD_SHA.
3. Working tree status.
4. Commands run and results.
5. Full cumulative patch from `git diff --binary BASE_SHA...HEAD`.

The patch is the deliverable.
Do not provide only a summary.
Do not provide only changed-file statistics.
Do not provide only `git format-patch -1` for the latest commit when the branch may contain multiple commits.
Do not modify files after producing the final patch.
If any file is modified after producing the patch, regenerate the cumulative patch for the new HEAD before responding.
```

ChatGPT must not treat a Codex summary as enough to create or review a GitHub PR. If Codex returns only a summary, ChatGPT must ask for the cumulative patch before attempting GitHub publication.
