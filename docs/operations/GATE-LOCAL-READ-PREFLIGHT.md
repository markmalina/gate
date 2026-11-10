# Protected release preflight for Gate #742

Product candidate: [Gate PR #744](https://github.com/markmalina/gate/pull/744)
at `176487760c873b9a40b7a29c518343dc68a75c50`.
Execution and owner approval: [Gate #742](https://github.com/markmalina/gate/issues/742#issuecomment-6768283097).
Regulation: https://github.com/markmalina/ai/blob/main/WORK-RULES.md.

This companion PR stages the accepted reader for a single production metadata
observation before merging the product PR. Current main and the independently
observed application fingerprint are both
`e7b3e32998706f7ef19053331466ea6c63105beb`. The lock binds that SHA as
the sole parent, current main and expected production source. The production
and candidate source use the same Railway configuration and Dockerfile.

The four executor files, workflow and local tests are byte-identical to the
accepted [PR #842](https://github.com/markmalina/gate/pull/842) at
`b5bcc065b2c0948bf63dee8cce5a45babfdca57d`. The lock records the new
window **10 November 2026, 16:24–17:09 MSK**, end exclusive. This lock
classifies the source proposal. The actual read requires source acceptance and
an owner-authored PR body edit naming this companion PR's exact full head:

`/ops-gate-release-preflight expected-head-sha=<accepted full helper SHA>`

Ordinary PR publication and CI do not admit the read. The protected job checks
owner identity, exact parent and workflow tree, time, file hashes and target
before exposing the existing Railway project token to the fixed read-only
action. The action makes at most two GitHub GETs and three Railway GraphQL
reads, with zero retries. Only the fixed Gate production target is in scope.

Verify the original JSON and detached SHA-256 from the exact GitHub Actions
artifact. For this product release, require explicit auto-deploy off, zero
deployment triggers, empty staged changes, current source identity, matching
configuration, one healthy active deployment and no drift across the
observation. An expired window, STOP, UNKNOWN, historical source alias, or
incomplete evidence blocks the product merge. This preflight carries no
authority to migrate, deploy, change configuration, or send Telegram messages.
