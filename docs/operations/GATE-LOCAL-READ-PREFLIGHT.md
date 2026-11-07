# Gate-local READ preflight — source candidate, not executed

Task: https://github.com/markmalina/gate/issues/839
Regulation: https://github.com/markmalina/ai/blob/main/WORK-RULES.md

## Current-main source proposal — 7 November 2026

This candidate is a sole child of main
`e7b3e32998706f7ef19053331466ea6c63105beb`, base tree
`e17b705e67747f52a937d9f2574b157600c0db9d`. It copies exactly eight
added paths from immutable PR805 head
`42b8dbf110789c545c8b2137c02aaca197a00e92`.
The action, local wrapper, reader, upstream tests and workflow are byte-identical
to PR805. Only the lock's main/window binding and proposal classification,
the local test clock binding, and this instruction are adapted.
PR805, its body, branch and expired lock are historical and remain untouched.

The single proposed window is **2026-11-07T14:00:00Z to
2026-11-07T14:45:00Z**, end exclusive, **PROPOSED_NOT_GRANTED**.
The lock's authorizationStatus is an informational proposal classification,
not a new admission protocol or grant. Only the existing exact owner body-edit
admission can authorize execution after independent source acceptance.
The proposed start was selected with more than fifteen minutes after freeze
for CI, controller review and owner admission. Expiry before owner admission
means STOP; no automatic extension, replacement head or rearming.

Source GitData confirms main railway.toml blob
`d26281d309732ade5b8a9787d97e679398dec283` and Dockerfile blob
`78e94f24fea683302c49fbe4badce55fff281023`.
The lock's expected production source
`afe0c6b8519381a0007c0dcc1c145207b5d54a3f` is the dated expected fingerprint
from the accepted source, not a new runtime observation. Its historical Git
Dockerfile blob is `22dc92c6cc82bf9b33131647d2243a408fd6e5f2`;
railway.toml is identical. Git source equality does not prove runtime overrides.

## Future one-READ packet — do not execute in this task

Before any operation, alpha must accept the exact new candidate head/tree,
its sole parent above, the unchanged current main, and this proposed window.
The final receipt in #839 supplies actual remote head/tree and CI evidence;
these cannot be inferred from a source document's future commit.
Then a separate new exact owner grant is required. The existing command format
is one owner-authored body edit on the **new** PR:
`/ops-gate-release-preflight expected-head-sha=<accepted full candidate head>`.
This document is not that command. The ordinary source PR body contains no
operational command. Do not edit PR805 or reuse completed #838 admission.

One five-minute workflow, at most ten minutes for the complete execution;
existing `gate-production` environment and `secrets.RAILWAY_TOKEN` reference
only. No login, provisioning, secret copying or credential fallback.
Source/caller admission precedes the credential-bearing step. Actual Gate
executor SHA/tree and workflow SHA are separate from upstream Ops provenance.

The unchanged core makes exactly five HTTP calls: two GitHub main-ref GETs and
three fixed Railway GraphQL calls (authentication plus bracketed state),
retry zero, existing fifteen-second deadlines and existing response caps.
Source fetch/setup/artifact operations are separate GitHub operations.
The fixed target remains project `972b6ab7-988a-421c-abfa-425451ad714f`,
environment `a148b138-44b3-4349-9d4b-292880503055`,
service `099cbf8c-1d94-43fe-8714-90725bf93998`.
The accepted query in preflight.mjs explicitly requests
`serviceInstanceAutoDeployStatus { enabled canEnable }`, scoped
`deploymentTriggers` with `pageInfo`, and
`environmentStagedChanges(decryptVariables: false)`.
No generic client, alternative API research or additional Railway reads.

Preserve original JSON and detached SHA256. The strict consumer must verify
original bytes, exact source/run/workflow/head/tree provenance; after upload,
independently obtain genuine artifact metadata/ID and original ZIP bytes/digest.
Never invent an artifact ID or substitute the run ID. Available listing/download
tools are not proof of a successful future upload.
OBSERVED with explicit facts can be useful even when releasePreflightReady is
false. Keep HISTORICAL_GATE_ALIAS as classification, sourceMatches false and
releaseReady false as observed; missing booleans, incomplete trigger pagination,
staged UNKNOWN and old UNKNOWN never become readiness by normalization.

STOP on source/main/window/owner/target/provenance drift, malformed or missing
required fields, unavailable protected credentials, incomplete evidence or a
new UNKNOWN. No retry, fallback or automatic rearming. #838 integration stays
BLOCKED until fresh authoritative facts and a new exact operational grant.
The first hosted source CI failure ends #839 without fix/rerun/dispatch loops.
No real reader, provider read, merge, runtime operation, main/config/source
binding, credentials, real ledger/ACL, DB/schema/data/audio/STT or message effect
is authorized by #839. Full Gate downtime is NOT_GRANTED; HOLD_NO_AUTORESTORE,
S1 NOT_ACCEPTED, AQUB frozen, historical UNKNOWN and consumed grants persist.

Context: [accepted STOP #838](https://github.com/markmalina/gate/issues/838#issuecomment-6707427143),
[accepted reader #804](https://github.com/markmalina/gate/issues/804#issuecomment-6694800871),
[historical run #806](https://github.com/markmalina/gate/issues/806#issuecomment-6695093250).
The last source demonstrates capability, not current readiness or permission.

## Existing unchanged reader mechanism

The reader and upstream tests are byte-identical Ops sources at
d1b47bdacf6b3bcf9d2abc46e133eada4e0832c6, copied from
agent-ops/actions/gate-release-preflight. The local action calls local.mjs;
the upstream historical caller/main are not the Gate entry point.
The wrapper retains the fixed queries, target, project-token scope check,
five-request/three-provider-query bounds, 15-second deadlines, redaction,
state bracket and strict observation schema. No private Ops action is loaded.

The lock freezes the current main, production LKG/config blobs, workflow and
four executor files. A separate provenance envelope records actual Gate
executor SHA/tree, actual PR workflow SHA, upstream pin and upstream byte digest.
The checked-out head must be a single forward child of frozen main; the actual
PR merge workflow must have parents main/head and exactly the same tree.
Source/caller admission runs before the credential-bearing local action and
again inside it. The receipt consumer verifies original bytes/digest, strict
schemas and exact provenance, without rewriting artifacts.

This PR's ordinary body and opened CI cannot activate the reader. Only an exact
owner-authored body edit on this same-repo, non-fork, open, main-based PR,
attempt1, inside the finite proposed lock window can pass admission. Source/CI
acceptance and a new exact-head owner admission are separate requirements.
The window is a proposal, not a grant. No owner command is posted by Work839.
Expired lock, main drift, a different base/head/tree/workflow, wrong target,
missing credentials or unknown state fails closed; there is no fallback/retry.

An OBSERVED receipt may have releasePreflightReady=false. In particular the
historical AlphaCenter/gate alias stays HISTORICAL_GATE_ALIAS, not identity
proof, and unknown/nonempty staged data never becomes EMPTY_VERIFIED. Raw
variables, commands, trigger identifiers, error bodies and private strings do
not enter the observation. The wrapper does not settle historical UNKNOWN.

No Railway read, merge, deployment, flags/config/credential change, DB access,
recording, STT, transport effect or other operational acceptance is performed by
this source task. PR835/PR837 remain open and unmodified; W1 is NOT_ACHIEVED.
