# Gate release checks (unmerged owner-gated activation candidate)

Task: https://github.com/markmalina/gate/issues/798
Assembly/activation source: https://github.com/markmalina/gate/issues/799
Base: PR797 head b0ac7825d529e3c4c82c5820f7be198d8105526c.

This unmerged PR797 candidate removes only the deploy job's literal `false &&`.
Main still has literal OFF; no live grant or deployment is implied. W1 defaults
OFF and all four S1 OFF gates remain. Owner/repository IDs, Gate751 command/manual
input, main/attempt1, environment, concurrency and token scopes are unchanged.
No transport, provider request, retry or wait is altered. Exact source/CI review,
protected facts, quiescence/autoDeploy boundary and recovery precede any live grant.

Before the credentials-bearing local deploy action, the existing Bash source
step validates full lowercase SHA40 values and requires approved source = actual
`git rev-parse HEAD` = executor (`github.sha`) = `github.workflow_sha`. The action
retains its own provenance/clean-checkout and current-main checks. No additional
Node installation is needed in the pre-action Bash step.

After the action and artifact upload, the terminal step invokes the small
read-only `release-checks.mjs` module. A successful output string alone cannot
accept the release. The verifier checks the actual executor's existing contracts
`gate.local-deploy/v1` and `gate.public-local-executor.v1`, all source/run/target/
upstream bindings, exactly one fixed Gate deployment and diagnostic=null.
Started/finished timestamps must be canonical ISO and ordered.

It hashes exact receipt bytes and compares both the output `sha256:<hex>` and
the exact existing artifact line `sha256:<hex>  ops-fast-deploy-receipt.json\n`.
It accepts only the producer's existing JSON.stringify(receipt,null,2)+LF
serialization, rejecting duplicate keys, invalid UTF-8/BOM, truncation or
unexpected fields rather than silently normalizing them. These are checks of
the unchanged producer, not a new receipt/schema version. A future producer
format change requires review; it does not silently bypass this verifier.

CLI on the fixed Ubuntu release runner reads only the two fixed artifacts in
GITHUB_WORKSPACE, as bounded regular
files with no symlink traversal. It does not rewrite/delete receipts or log
payloads, parse errors, paths or credentials; failure emits one safe constant.
FAILED/UNKNOWN, mismatched result, missing files or inconsistent digests fail.
The receipt is provenance/consistency evidence, not independent proof of fleet
health, W1 delivery, recovery availability or resolution of historical UNKNOWN.

Run offline checks from the repository root:

```sh
node --test tests/ops/*.test.mjs
node --check .github/actions/gate-deploy/release-checks.mjs
node --check tests/ops/release-checks.test.mjs
git diff --check
```

Tests execute the actual workflow source/terminal Bash scripts with synthetic
environment and a harmless next-action sentinel, plus the pure verifier and
CLI against receipts from the real unchanged fastDeploy/persistReceipt using
fake dependencies. Digests are recomputed after semantic field mutation so a
negative case must reject the bad binding, not only a stale checksum. No live
GitHub/Railway/provider client, production secret, or deploy is used. GitHub
workflow hosting/environment behavior is not proven by these offline tests.
