# Sprint 2 integration record — Gate745 / PR747

Owner scope: source/test integration only. No merge, production/config/credentials, model/STT or real messages.

Parents: original747 `b975475c1c6c81035e38d9bd13a40d777933e83c` and media main `8d17d0b40a86c856bec1072633c721212a34a9dc`.
Regulation: <https://github.com/markmalina/ai/blob/main/WORK-RULES.md>.
Current plan: <https://github.com/markmalina/gate/issues/745#issuecomment-6789115422>.

## Implemented source

- Reconciled nine text conflicts. Current media sender, normalization, budgets, hashes, receipt and startup freeze boundaries retained.
- Mandatory immutable context snapshot across text/document/media preparation, package identity and preview; shared scoped refusal contract.
- Current unprojected raw readset must be complete. Raw keyset/text continuation is read-only and never grants a send token; consumers recheck completeness. Explicit known-gap markers fail closed; real producer wiring remains sprint 3.
- Preserve completed legacy sent/failed/unknown media replay before freshness checks, without snapshot backfill or budget reservation.
- Preserve both authored 0116 revisions, add no-DDL 0120 join, align bounded helper/readiness. Historical fixture product workers/HTTP endpoints/packaging excluded.
- Preserve R1 pending-task fixes and legacy direct-send denial.

## Terminal local checks before publication

Python 3.12, existing dependencies, isolated PostgreSQL 17.11 on loopback TCP. Provider transports are synthetic.
External proxy variables removed for final SQL regression slices; task-local Python DNS/connect guard denies non-loopback, including child Python processes.

| Suite | Terminal result |
|---|---|
| New actual MCP/readset/media + legacy receipt replay + no-read/failed-lookup error contracts | 17 PASS |
| Actual authored-branch convergence + populated media/unknown cutover helper | 4 PASS |
| Media/digest/context/catalogue/unit/bridge/helper/readiness | 147 PASS |
| Actual MCP account/project isolation (downstream health transport mocked) | 1 PASS |
| Reviewed pending tasks, including 50-item sequences | 19 PASS |
| Media storage/cleanup/schema drift | 12 PASS, 2 existing cyclic-FK SQLAlchemy warnings |
| Selected current media wire/MCP/replay/legacy-deny regression | 23 PASS, 35 unrelated transport cases deselected in this slice |
| Plain/markdown/Rich/document/sequence formatting regression | 118 PASS |
| Explicit joined-graph downgrade guard and clean round trip | 1 PASS |

Total: 342 distinct terminal passing cases in these disjoint suites, no PG skips in them. Ruff repository and strict Mypy 320 source files PASS. One Alembic source head: `0120_reply_media_dialog_join`.

The first hosted Quest run on integration commit `9291ab1dcc6f2e3cfb8a50f199d421c9bf12162d` exposed a relative `downgrade -1` ambiguity at the joined graph (191 PASS / 1 FAIL). The migration regression now exercises the same explicit `0083_become_quest_corrective_m` target for both the active-grant refusal and the clean round trip. Local actual PostgreSQL verification passes; original run results are retained rather than rerun unchanged.

A broad local pytest attempt was not completed and is NOT PASS. Automatic approval review rejected its continuation because an existing MCP status test could reach downstream Railway health through the inherited proxy. The test now injects a synthetic downstream transport; the actual SQL/read/draft/send paths remain real. Scoped offline terminal slices above supersede incomplete exploratory runs. Several combined 25-second slices timed out and were split; no timeout is counted as PASS.

The broad exploratory run also lacked the venv bin directory in PATH for child `alembic`; final SQL slices set that PATH. Hosted exact-head checks and their full logs belong to the issue receipt and are not inferred here.

## Remaining release gates

Sprint 3 actual ingress-gap producers and all-path race acceptance; sprint 4 exact own-echo/instant tail-stop/status; sprint 5 independent whole-candidate verdict; sprint 6 accepted current Gate-owned cutover/rehearsal/recovery and protected autoDeploy=false before merge; sprint 7 physical target/DB/fleet/locked backup/exact guarded deployment; sprint 8 connected catalogue and bounded live product acceptance.
Old media-only binary is not assumed compatible rollback for the joined schema. Current snapshot workflow stays quarantined; no old fixture/grant/token is reactivated.

## Accounting for all 94 original PR747 paths

Every original changed path is accounted for below. Historical evidence retained in the tree is explicitly not an active dependency. Full before/main/conflict/decision evidence is in sprint 1 comments 6789537390 and 6789537697.

| Original path | Final source decision |
|---|---|
| `.github/workflows/become-quest-exact-head.yml` | Original guard source reused unchanged |
| `.github/workflows/ci.yml` | Current main preserved; old PR hunk not reintroduced |
| `Dockerfile.contact-sync` | Current main preserved; old PR hunk not reintroduced |
| `alembic/versions/0116_reply_dialog_versions.py` | Original guard source reused unchanged |
| `app/api/health_routes.py` | Adapted to current media/readset/join contract |
| `app/api/mcp_routes.py` | Adapted to current media/readset/join contract |
| `app/api/mcp_schemas.py` | Adapted to current media/readset/join contract |
| `app/api/sprint10_routes.py` | Adapted to current media/readset/join contract |
| `app/api/sprint17_send_draft_routes.py` | Original guard source reused unchanged |
| `app/api/sprint9_routes.py` | Original guard source reused unchanged |
| `app/application/dialog_snapshots.py` | Original guard source reused unchanged |
| `app/application/mcp_inbox.py` | Adapted to current media/readset/join contract |
| `app/application/reply_documents.py` | Original guard source reused unchanged |
| `app/application/reply_drafts.py` | Original guard source reused unchanged |
| `app/infrastructure/dialog_snapshots.py` | Adapted to current media/readset/join contract |
| `app/infrastructure/reply_cutover.py` | Adapted to current media/readset/join contract |
| `app/infrastructure/reply_documents.py` | Original guard source reused unchanged |
| `app/infrastructure/reply_drafts.py` | Original guard source reused unchanged |
| `app/infrastructure/reply_sends.py` | Adapted to current media/readset/join contract |
| `app/infrastructure/reply_sequences.py` | Adapted to current media/readset/join contract |
| `app/infrastructure/telegram_processing.py` | Original guard source reused unchanged |
| `app/models/domain.py` | Original guard source reused unchanged |
| `app/worker/gate745_fixture_alembic/__init__.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_alembic/env.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_bootstrap.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_controller_pin.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_hosted.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_identity.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_ledger.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_manifest.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_policy.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_readiness.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/gate745_fixture_recipe.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `app/worker/reply_cutover.py` | Original guard source reused unchanged |
| `app/worker/reply_sequences.py` | Original guard source reused unchanged |
| `docs/TELEGRAM_BUSINESS_DOCUMENT_SEND.md` | Original guard source reused unchanged |
| `docs/operations/DIALOG-SNAPSHOT-GUARD.md` | Adapted to current media/readset/join contract |
| `docs/operations/GATE745-FIXTURE-BOOTSTRAP.md` | Adapted to current media/readset/join contract |
| `docs/operations/GATE745-OPS-CONTROL.md` | Adapted to current media/readset/join contract |
| `docs/operations/gate745-incident-20261026/R2-CONTRACT.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-incident-20261026/R3-MATRIX.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-incident-20261026/SPRINT-PLAN.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-incident-20261026/diagnosis-2026-10-26.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-incident-20261026/reproduction-results.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/READ-TRANSPORT.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/README.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/ops-proposal.patch` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/read-transport-evidence.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/route-inventory.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/s1-source-tests.mjs` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/source-manifest.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s1/test-results.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s2/FEASIBILITY.md` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s2/analysis-results.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s2/analyze_feasibility.py` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s2/schema-excerpt.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/gate745-s2/sources.json` | Historical evidence retained; current plan/contract takes precedence |
| `docs/operations/railway-gate745-fixture-bootstrap.toml` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `docs/operations/railway-gate745-fixture-helper-v2.toml` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `docs/operations/railway-gate745-fixture-reset.toml` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `docs/specs/ISSUE-737-RICH-OPERATOR-EXAMPLE.json` | Original guard source reused unchanged |
| `docs/specs/ISSUE-745-SPRINT-SPEC.md` | Adapted to current media/readset/join contract |
| `pyproject.toml` | Adapted to current media/readset/join contract |
| `railway.toml` | Original guard source reused unchanged |
| `scripts/gate_document_upload.py` | Original guard source reused unchanged |
| `tests/test_become_i4_service.py` | Original guard source reused unchanged |
| `tests/test_become_learner_invitation_postgres.py` | Adapted to current media/readset/join contract |
| `tests/test_become_quest_migration.py` | Adapted to current media/readset/join contract |
| `tests/test_become_transcript_review_postgres.py` | Adapted to current media/readset/join contract |
| `tests/test_ci_audio_tools.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `tests/test_dialog_snapshot_processes_postgres.py` | Original guard source reused unchanged |
| `tests/test_dialog_snapshots.py` | Original guard source reused unchanged |
| `tests/test_dialog_snapshots_postgres.py` | Original guard source reused unchanged |
| `tests/test_document_upload_bridge.py` | Original guard source reused unchanged |
| `tests/test_gate745_fixture_bootstrap.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `tests/test_gate745_fixture_bootstrap_postgres.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `tests/test_gate745_fixture_hosted.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `tests/test_gate745_fixture_identity.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `tests/test_gate745_fixture_readiness.py` | Excluded from active product candidate; original source/history retained at old747 SHA |
| `tests/test_mcp_contacts.py` | Adapted to current media/readset/join contract |
| `tests/test_mcp_postgres_isolation.py` | Adapted to current media/readset/join contract |
| `tests/test_mcp_reply_context.py` | Adapted to current media/readset/join contract |
| `tests/test_mcp_routes.py` | Adapted to current media/readset/join contract |
| `tests/test_mcp_voice_reply_context.py` | Adapted to current media/readset/join contract |
| `tests/test_railway_readiness.py` | Original guard source reused unchanged |
| `tests/test_reply_cutover.py` | Adapted to current media/readset/join contract |
| `tests/test_reply_cutover_postgres.py` | Adapted to current media/readset/join contract |
| `tests/test_reply_formatting_mcp_transport.py` | Original guard source reused unchanged |
| `tests/test_reply_formatting_send.py` | Original guard source reused unchanged |
| `tests/test_reply_formatting_sequences_documents.py` | Original guard source reused unchanged |
| `tests/test_reply_rich_mcp_transport.py` | Original guard source reused unchanged |
| `tests/test_reply_sends_postgres.py` | Adapted to current media/readset/join contract |
| `tests/test_reply_sequence_context_mcp.py` | Adapted to current media/readset/join contract |
| `tests/test_reviewed_pending_tasks_postgres.py` | Original guard source reused unchanged |
