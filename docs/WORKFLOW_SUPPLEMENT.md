# Gate Workflow Supplement

This supplement is part of the Gate working regulation.

## Regulation-only PR gate

Project, product, and code PRs are allowed when ChatGPT decides they are the correct bounded development step.

Standalone regulation-only or workflow-only PRs are allowed only when Mark explicitly says `зафиксируй сейчас`, explicitly asks for a standalone regulation PR, or the regulation change is included in an already suitable substantive project PR.

When Mark says only `зафиксируй`, ChatGPT queues the rule for the next suitable substantive project PR and does not create a standalone regulation-only PR.

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
