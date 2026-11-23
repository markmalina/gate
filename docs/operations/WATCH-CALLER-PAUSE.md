# MARK_CALLER_PAUSE — one source component; NOT WATCH_QUIESCE

Task: https://github.com/markmalina/gate/issues/810
Regulation: https://github.com/markmalina/ai/blob/main/WORK-RULES.md

This Gate-hosted action is not installed or executed. No Mark file is changed.
The future caller and its exact reviewed Mark commit are **UNCREATED**.
Real token permissions, repository action policy and launchability remain unverified.
No permanent PAT/App key/cross-repository secret is introduced: a Mark-owned job
supplies its own ephemeral `github.token` with actions:write and contents:read.
An action invoked in Gate, a fork, PR, push, non-owner context or rerun rejects
before all HTTP. Importing the module and ordinary Gate CI never activates it.

## Fixed authority and admission

The fixed profile names only Mark repo1336157811/owner23397294/workflow376111707,
and the reviewed target workflow path/Git blob3103cf2855bbfca5a62b19ea646e3f20494674f8.
The existing workflow is not changed. Its known SHA256 is an audit reference in
the profile; live source identity uses authenticated GitHub contents metadata
for that exact Git blob/path at the reviewed current caller commit. Private
workflow bytes are not copied into Gate fixtures or public receipts.

Admission requires the exact owner workflow_dispatch on reviewed Mark main,
actual caller HEAD=GITHUB_SHA=GITHUB_WORKFLOW_SHA=grant expected_caller_sha,
the exact caller workflow ref/byte digest, Gate action repository/ref/full SHA,
and SHA256 of all three executor files in a future Mark-owned lock. Operator
inputs have exactly five bounded fields; no URL/workflow/command selector exists.
not_before/expires_at are fresh canonical UTC timestamps, at most ten minutes
apart. No absolute development-day grant is baked in; no automatic extension.
The request deadline is the earlier of expiry and three minutes from execution.

## Fixed effect and full queue algorithm

Before a write, verify private/nonfork Mark repository identity, current main,
target source blob and workflow ID/path/state; recheck main immediately before
the optional write. Only active or disabled_manually is admitted. For active,
perform at most one fixed PUT disable (204); already disabled_manually is read-only.
Other disabled states stop conservatively. Permission failures stop; a lost ACK
is recorded as uncertain, never retried. There is no enable/cancel/rerun route.

After disabled readback, bracket a full unfiltered workflow-runs pagination
with disabled observations. Use only per_page=100 and page; status/created/actor/
branch filters are forbidden, avoiding GitHub's 1000-search-result limitation.
Require stable total_count on every page, exactly expected page sizes, unique
IDs, exact run workflow/repository, bounded next links and cardinality=total.
Default cap:20pages/2000rows per pass,2MiB/response,10s/request,64requests total.
Any overflow, missing/truncated page, duplicate, target mismatch or incomplete
pagination stops without an empty verdict. Unknown statuses or completed rows
without a known terminal conclusion also stop, never becoming zero.

A full pass with queued/in_progress/waiting/pending/requested stops immediately
as DISABLED_QUEUE_NOT_EMPTY (one complete nonempty pass is sufficient to deny).
Otherwise perform a second full pass; totals, pages, counts and canonical
ID/status/conclusion/attempt digest must agree. Only then PAUSED_QUEUE_EMPTY.
This proves two bounded metadata observations while disabled, not an eternal
absence of new runs, Gate HTTP in-flight=0, two runtime flagsOFF or full fleet
quiescence. Those remain separate WATCH_QUIESCE components.

## Receipts / stop

Original canonical JSON plus detached SHA256 are created once with mode0600/wx.
The strict consumer validates exact keys/types/target/source/caller/run/attempt/
grant/timestamps/coverage/counts and raw-byte digest/provenance; malformed UTF-8,
duplicate JSON keys, extra/private fields or tampering reject. Only
PAUSED_QUEUE_EMPTY lets the component succeed. STOP/INCOMPLETE/NOT_EMPTY keep
the caller disabled if it was disabled; no automatic compensation re-enables it.
Safe diagnostics retain only code/stage/HTTPstatus; token/provider payloads,
private commit messages, workflow/run filenames and raw run IDs are not exported.
The two explicit fields gateInFlightVerified/watchDriversOffVerified stay false.

## Future Mark-owned caller proposal — NOT INSTALLED / NOT A GRANT

Place a future caller at the profile's fixed callerPath. Proposed minimal YAML:

```yaml
name: Owner-only fixed Mark caller pause
on:
  workflow_dispatch:
    inputs:
      operation:
        type: choice
        options: [MARK_CALLER_PAUSE]
        required: true
      expected_caller_sha:
        type: string
        required: true
      expected_executor_sha:
        type: string
        required: true
      not_before:
        type: string
        required: true
      expires_at:
        type: string
        required: true
permissions:
  contents: read
jobs:
  pause:
    if: >-
      github.repository_id == '1336157811' &&
      github.repository_owner_id == '23397294' &&
      github.actor_id == '23397294' && github.ref == 'refs/heads/main' &&
      github.run_attempt == 1
    permissions:
      actions: write
      contents: read
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    concurrency:
      group: mark-caller-pause
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
        with:
          ref: ${{ github.sha }}
          persist-credentials: false
          fetch-depth: 1
      - uses: markmalina/gate/.github/actions/watch-caller-pause@UNCREATED_REVIEWED_GATE_SHA
      - if: ${{ always() }}
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
        with:
          name: mark-caller-pause-${{ github.run_id }}
          path: |
            mark-caller-pause.json
            mark-caller-pause.sha256
          if-no-files-found: error
          retention-days: 7
```

Use a separate concurrency group: sharing the drain group could replace its
pending runs. Do not install this placeholder YAML or launch it from Gate.
Future Mark `.gate/mark-caller-pause.lock.json` proposal:

```json
{
  "schemaVersion": "mark.caller-pause-lock/v1",
  "executorRepository": "markmalina/gate",
  "executorSha": "UNCREATED_REVIEWED_GATE_SHA",
  "callerWorkflowSha256": "UNCREATED_EXACT_CALLER_WORKFLOW_BYTE_SHA256",
  "executorFilesSha256": {
    "action.yml": "UNCREATED_REVIEWED_BYTE_SHA256",
    "pause.mjs": "UNCREATED_REVIEWED_BYTE_SHA256",
    "profile.json": "UNCREATED_REVIEWED_BYTE_SHA256"
  }
}
```

All placeholders fail closed. At future integration, fill reviewed Gate commit
and file hashes; compute the actual Mark caller workflow byte hash; then review
the Mark commit and bind its full SHA through the separate exact owner grant.
The lock does not self-reference its containing commit. No expiry/token goes
in this static lock. A dispatch on another Mark revision rejects before PUT.

Official contracts reviewed for this component:
- https://docs.github.com/en/rest/actions/workflows#disable-a-workflow
- https://docs.github.com/en/rest/actions/workflows#get-a-workflow
- https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs
- https://docs.github.com/en/actions/tutorials/authenticate-with-github_token

This source task performs no pause, Mark mutation, runtime change, Railway,
deployment, recording download or STT. EffectiveOFF/startup/fleet/inflight and
the historical UNKNOWN boundary remain outside the component; release HOLD.
