# Independent PostgreSQL review of reply sequences

## Why this lane exists

[Delta #210](https://github.com/AlphaCenter/delta/issues/210#issuecomment-5866863275)
ended **BLOCKED**, not REJECT: its Work could inspect source and CI, but had no
PostgreSQL service and skipped all 15 new SQL regressions. No new product defect
was reproduced. Repeating the same local command in the same environment cannot
remove this blocker.

The `Reply sequence review` workflow provides an ephemeral PostgreSQL 17 service
and a single `reply-sequence-postgres-review` job. An independent reviewer can
start a fresh execution of that job through the GitHub connector. Their test
runner is GitHub-hosted, not their local Work filesystem. This is a supported
independent execution when the reviewer initiates it, verifies the exact source,
reads the fresh result and records their own verdict. An author's green run alone
does not satisfy that requirement.

## Scope and controls

- Python 3.12; project dependencies constrained by `constraints/ci.txt`.
- Exact PR head checkout, no persisted checkout credentials, read-only repository
  permission, no repository secrets or protected environments.
- One fresh database `gate_reply_sequence_review` in a PostgreSQL 17 service,
  discarded with the runner. No production connection or production data.
- Runner requires a literal 40-character candidate SHA and clean tracked files.
- Database preflight requires the designated loopback database, PostgreSQL 17
  and no application tables. It rejects missing DB configuration before pytest.
- Nine explicitly selected test files: **190 cases**, including **73 PostgreSQL
  cases** (15 review regressions + 23 dispatch + 35 acceptance).
- A successful pytest process is insufficient: JUnit must contain the exact
  per-file inventory with no skipped, failed, errored, duplicate or missing cases.
- Inventory changes require an explicit update and review of the runner manifest.
- Caller `PYTEST_ADDOPTS`/`PYTEST_PLUGINS` cannot silently narrow this suite.
- Existing tests use synthetic data and mocked Telegram transport; no live
  provider sends. Runtime feature flags and production configuration are unchanged.
- Job timeout 12 minutes; pytest timeout 6 minutes. No retry loop.

The runner's own guard tests exercise empty/misconfigured databases, wrong PG
version, source drift, missing/duplicate cases, skips, failures, stale evidence,
credential redaction and agreement between process status and JUnit results.

## Procedure for the independent Work

1. Accept the new Delta attempt, read its candidate/base and predecessor, and
   record a unique review execution. Do not reuse a terminal attempt.
2. Read current Gate PR #720 and compare its head with the attempt's candidate.
   Also assess base drift. Review the source diff, test assertions and this runner;
   a successful runner is evidence, not a substitute for code review.
3. Fetch the workflow runs for that exact head. Select `Reply sequence review`
   and its completed `reply-sequence-postgres-review` job, verifying the workflow
   path and source SHA. There must be no competing active review of this attempt.
4. Record the current run ID, attempt and job ID. Start **one** fresh execution:

   ```text
   github_rerun_workflow_job(
       repo_full_name="AlphaCenter/gate",
       job_id=<verified reply-sequence-postgres-review job ID>
   )
   ```

   This bounded rerun is specifically justified by the missing independent DB
   execution in Delta #210. Do not rerun the whole backend/Windows/Docker suite.
   API equivalent: `POST /repos/AlphaCenter/gate/actions/jobs/{job_id}/rerun`.
   [GitHub API contract](https://docs.github.com/en/rest/actions/workflow-runs#re-run-a-job-from-a-workflow-run).

5. Poll the run and its **new attempt/jobs**. A queued rerun response is not a
   completed test. Do not mistake an earlier author's successful job for the new
   execution. Confirm unchanged source SHA, fresh timestamps and successful steps.
6. Read the new job log and artifact
   `reply-sequence-review-<run_id>-<run_attempt>`. It contains `receipt.json`,
   `junit.xml` and `pytest.log`, retained for 14 days. The receipt records candidate,
   verified head, PG version, Python, command, counts, timestamps and GitHub
   run/attempt/actor metadata. The same receipt is printed in the job log.
7. Require `execution_status=succeeded`, `passed=190`, `skipped=0`, matching
   per-file counts and `acceptance=not_issued`. Link the new run attempt and job in
   the Delta verdict. Distinct Work identity/independence is established by its
   accepted task and execution receipt; a shared GitHub login alone proves neither.
8. Finish the R1/R2/R3 and regression matrix; record ACCEPT, REJECT or BLOCKED with
   precise limitations. Close that attempt only after its verdict is saved.

If the connector lacks Actions write permission, rerun is rejected, source has
changed, or the new execution fails, report the exact blocker. Do not substitute
the previous CI run or edit the old Delta verdict. A later attempt is a new Issue.

## Optional local path

For a reviewer with Docker and a clean checkout of the exact candidate:

```sh
docker run --rm -d --name gate-reply-review \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=gate_reply_sequence_review \
  -p 127.0.0.1:55432:5432 postgres:17
docker exec gate-reply-review pg_isready -U postgres
python -m pip install -c constraints/ci.txt '.[dev,contact-sync]'
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/gate_reply_sequence_review \
  python scripts/reply_sequence_review.py \
  --candidate-sha "$(git rev-parse HEAD)" --disposable-database \
  --output /tmp/gate-reply-review-evidence
docker stop gate-reply-review
```

Use a fresh output directory. Wait until `pg_isready` succeeds before starting.
Stop the container even if the checks fail. This path is optional; a Work without
Docker or an unprivileged PostgreSQL runtime should use the hosted job above.

## Acceptance boundary

The implementation Work's rehearsal and the independent reviewer's execution
are separate. Neither this workflow nor its receipt emits ACCEPT, merges a PR,
deploys code, enables features or proves live product readiness. Owner notification
route, deployment fingerprint and bounded live canary remain release-stage checks
under `REPLY-SEQUENCE-CUTOVER.md`.
