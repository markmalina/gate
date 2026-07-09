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
