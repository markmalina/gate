# Gate736 Watch0119 client independent review

Verdict: **ACCEPT_SOURCE_BLOCKED**. Independent reviewer: `ops_watch_review`. Reviewed 2026-11-03. This accepts the exact disabled client preparation below, not installation, S1 DONE or an S2 effect grant.

## Exact source

Watch Ops executor: `5c86c479f6581c5922519382ffcd953d63629084`, [Ops PR414](https://github.com/markmalina/ops/pull/414).
Reply745 executor remains `b0993f9fcd51db33e94a2f2e3bbec28f6b1d8c85`.
Comparison baseline for Gate client: `ea92973c579de3f802323f9811ca32fed070118f`.

Fetched the original Gate workflow/lock and applicable Gate AGENTS.md at that exact baseline. Fetched the exact Ops Watch profile and terminal entry from `c56f62e0421b51d1ff0103abc20071d78585d55b`; verified they match the locally reviewed bytes. Read the actual successor commit `5c86c479f6581c5922519382ffcd953d63629084`: its only change adds the inert imported `watch-native.mjs` to the isolated reply745 production-lifecycle fixture copy. Independently reran that lifecycle suite: **9 passed, 0 failed**. This is a test packaging correction, not a runtime change.

## Checks performed

- Parsed the complete YAML and read all appended Watch jobs and their exact pinned actions.
- Compared the complete original workflow bytes: the old workflow remains an exact prefix. Existing reply745 jobs and action pins are unchanged. The old lock differs only in its workflow digest, which correctly binds the expanded workflow.
- Both client locks match SHA-256 of the final workflow. Watch profile hash matches the fetched exact Ops profile. All four Watch Ops action references use the same full successor SHA and explicitly pass `profile: watch0119`.
- Executed **16 positive/negative cases against the actual Bash parser snippets** for owner request, bot plan, apply and observer plan transport. Covered correct inputs, exact-source drift, wrong profile, zero IDs, trailing whitespace/text/newline and a literal shell-substitution string. Values travel through environment variables rather than interpolation into shell source.
- Verified requester/apply actor and issue736 restrictions, immutable expected client SHA propagation into bot plan admission, exact plan run/artifact/digest grammar, and the apply plan download.
- The protected observer downloads both the current-run receipt and the original plan required by the Ops verifier. The observer and terminal use `always()` with SUCCEEDED/FAILED result admission so verified fallback can be checked even after the apply job fails. UNKNOWN is excluded; the typed Ops verifier rejects FAILED receipts that are not confirmed recovery.
- The terminal job has no protected environment or secret references and has only contents/actions/issues read permissions. Its exact job name matches the typed Ops entry gate. It downloads receipt/live artifacts from the current Gate run and accepts only VERIFIED or RECOVERY_VERIFIED_DRIVER_FENCED output.
- Shared `gate-production-deploy` concurrency remains non-cancelling. This change adds no production restore/downgrade/history deletion or feature-enable branch.

The Watch Ops release remains disabled and its source/fixture/S2/history gates remain pending. Merely adding these jobs does not make a valid execution grant.

## Limits and remaining blocker

These are actual source, byte-comparison and isolated parser/lifecycle checks. No workflow was dispatched, protected secret was read, live drain was toggled, or provider operation was performed. CI for the final Gate/Ops heads was still pending at this review and must be linked separately by the executor.

The inherited reply745 runtime behavior is preserved as source; this review does not certify its old deployment route as currently installed or executable. Existing protected bindings used by the new source are assumptions to be checked within a separately authorized operation, not facts established here.

The Mark production adapter remains intentionally blocked by E_SETTLED_CHECKPOINT_REQUIRED. Authoritative settled history and native provider evidence remain missing. The source-level acceptances therefore do not close S1 or authorize S2. Follow the consolidated executor report for the precise external evidence package.

## Reviewed bytes

| File | SHA-256 |
| --- | --- |
| `gate/.github/workflows/gate-snapshot-cutover.yml` | `cd75c4a53c690ed6b0c48466199ac6d19ca40d2b4e9f49e120b6e2d2fbaf7794` |
| `gate/agent-ops.watch-snapshot.lock.json` | `88756bd1a6a8af968549181387189bc80c233694560f22e8ed42f62b198be1e3` |
| `gate/agent-ops.gate-snapshot.lock.json` | `233ae8dfa90fe1934cdfc61aca8183083eaa6fcc421c99cf7ec07eeae7011df0` |
| `ops/agent-ops/actions/gate745-fixture-controller/production-lifecycle.test.js` | `c010924bc2910de13224d799aac48f0e200f6e4d01fd9fdbb4bb8de5573d04b4` |

Independent lifecycle log SHA-256: `d869f5c251fa73296251e9d5ce343d1596c5d9bf921b1f5c81164a98d68d6066`. Saved adjacent as `client-independent-lifecycle-tests.txt`.
