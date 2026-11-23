# Gate-local READ preflight — source candidate, not executed

Task: https://github.com/markmalina/gate/issues/804
Regulation: https://github.com/markmalina/ai/blob/main/WORK-RULES.md

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
The window is a proposal, not a grant. No owner command is posted by Work804.
Expired lock, main drift, a different base/head/tree/workflow, wrong target,
missing credentials or unknown state fails closed; there is no fallback/retry.

An OBSERVED receipt may have releasePreflightReady=false. In particular the
historical AlphaCenter/gate alias stays HISTORICAL_GATE_ALIAS, not identity
proof, and unknown/nonempty staged data never becomes EMPTY_VERIFIED. Raw
variables, commands, trigger identifiers, error bodies and private strings do
not enter the observation. The wrapper does not settle historical UNKNOWN.

No Railway read, merge, deployment, flags/config/credential change, DB access,
recording, STT, transport effect or other operational acceptance is performed by
this source task. Main release/S1 OFF and PR797/PR802 remain unchanged.
