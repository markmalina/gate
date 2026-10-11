# Gate745 isolated synthetic bootstrap source

This is source preparation under existing Gate745, not a physical fixture,
effect grant, full-native ACCEPT, Production apply admission or runnable hosted
recipe. `app/worker/gate745_fixture_manifest.py` ships immutable `UNBOUND` bytes.
All CLI modes and even an authenticated fixture GET deny before control env,
credential/container or database access. There is no manifest/DSN/SQL/dataset
override. Ordinary Gate startup, `railway.toml`, Alembic environment, provider
adapters and existing admin operations are unchanged.

## Fixed source interface

| Mode | Fixed operation after future authenticated admission |
| --- | --- |
| `bootstrap` | Require physically bound fresh empty `gate745_synthetic` public schema; use actual Gate migrations through `0115_watch_voice_post_payment`; remove only freshly migration-generated reference data; insert the eight literal synthetic rows. |
| `seed` | Insert that same recipe only into an empty exact 0115 schema. Existing or unexpected rows deny; no user dataset argument. |
| `reset` | Require fresh distinct reset grant, whole Gate fleet quiescence and retained first-case artifacts authenticated by the executor. First prove exact synthetic-only state and journal, then reconstruct only this isolated public schema at 0115 and reseed. No Production drop, dump, restore, generic SQL or provider operation exists. |
| `unknown-proof` | Readonly transaction; prove 0115/0116 revision, registered tables/column types/nullability/primary keys, literal 0116 guard check/index shape, whole fixed-table counts, every field of the eight literal rows and canonical unknown journal. Return only fingerprints/counts. |

`python -m app.worker.gate745_fixture_bootstrap <fixed-mode>` currently returns
`DENIED/E_FIXTURE_UNBOUND` with `applyPlan=false` and
`fixtureAcceptance=NOT_PROVEN`. Even hypothetical BOUND shape cannot invent
the missing trusted route: the public CLI supplies no executor. Shape/digest
checks are policy, never authentication or an effect grant.

The only new HTTP capability is lazy
`GET /admin/ops/gate745-fixture-proof` in the existing health router. It has no
Settings dependency, selector, mutation mode or ordinary admin-secret fallback.
Every admission/authentication/state failure returns redacted 404 and no-store.
Dedicated server-side fixture admin credentials are consulted only after exact
manifest/runtime/trusted-executor admission. Ordinary app startup does not
install `TrustedFixtureExecutor`. This endpoint is usable on the unchanged
candidate fleet only after a separately reviewed trusted fixture binding exists.

After recovery stops the whole Gate fleet, this GET is unreachable. Post-stop
journal proof requires a separately reviewed fixed authenticated operator
`unknown-proof` route without redeploy or a new service. A pre-stop GET, health
count, stub result or redeploy cannot substitute for that proof. This route is
currently missing and remains a hard native acceptance blocker.

## Literal database recipe and proof

The eight rows are one inactive bot, inactive synthetic project/account,
synthetic conversation/message/task/draft and exactly one literal unknown send
attempt. They contain no real account, contact, phone, Telegram numeric ID,
Business credential, bot token, provider message ID or model reference. Account
read/send and bot/project/account activity are false. Other Gate schema tables
must have zero rows. Reserved/sending must be zero. Unexpected schemas,
functions, views, foreign relations, tables, columns, rows, status, nullable
values or changed journal deny. Other source-migration constraint equivalence
and native deploy/backup proof remain separate authenticated evidence; the
`schemaRevision` receipt is not a full structural schema hash. Historical schema imports only
describe data-free Gate migration tables; they do not initialize AQUB or a
provider. There is no Production-data inheritance or dump.

The fixture-only Alembic environment accepts only an already-admitted SQLAlchemy
connection, never Settings, env, dotenv or URL fallback. The database credential
comes only from the future trusted executor, after control admission. It must
resolve the fixed `gate745-synthetic-postgres.railway.internal:5432` service,
database and dedicated role `gate745_synthetic`; arbitrary hosts/query options
deny. Physical identity is rechecked inside the DB transaction using
`pg_control_system()` system identifier, database name, server address/port and
the pinned project/environment/PG service/volume. The required least privileges
for this exact identity/DDL route remain an operator decision; no admin credential
is obtained by this source change.

Recipe digest is SHA256 of JSON with sorted keys/compact separators, source
literal UUID strings and UTC timestamps at microsecond precision. Journal digest
uses the full fixed `send_attempts` recipe projection; every actual journal
column is checked before hashing. `seedDigest=recipeDigest` is the fixed recipe
fingerprint after actual full row/count verification, not an arbitrary DB hash.
Database identity digest hashes sorted canonical keys
`projectId,environmentId,postgresServiceId,volumeInstanceId,databaseName,serverAddress,serverPort,systemIdentifier`.
Target digest instead preserves the Ops fixed insertion order
`projectId,environmentId,serviceId,postgresServiceId,volumeInstanceId,repository`
with repository `AlphaCenter/gate`. Helper manifest digest hashes exact embedded
`MANIFEST_JSON` bytes. None of these fingerprints authenticates evidence by itself.

The bounded receipt is `gate745.fixture-bootstrap-receipt/v1` and contains:
mode/result, helper and current process SHA/tree, manifest/recipe/seed/database/
target/setup-route/proof-route fingerprints, immutable owner grant comment/body
digest, current run/attempt/time, schema revision, reserved/sending/unknown
counts, journal before/after counts/digests, and authenticated reset/quiescence/
retained-case references (null outside reset). No row payload, credential, SQL,
URL with credentials or raw exception leaves the helper. Bootstrap/seed before
journal is empty; reset and unknown-proof before/after are one exact unknown.
Successful source test/backend receipt still says `fixtureAcceptance=NOT_PROVEN`
and `applyPlan=false`; it cannot claim native Ops cutover acceptance.

## Hosted bootstrap and source binding blockers

Baseline 0115 source does not contain this helper. Deploying the final guard via
ordinary predeploy to seed would advance to 0116 before the backup. The two
standalone fixture TOMLs are proposed fixed helper deployments, never selected
by ordinary Railway configuration. They disable ordinary predeploy/start and
automatic retries. They are not installable authority: native configuration
semantics and the trusted helper invocation route must be independently reviewed
first. Helper bootstrap/reset must finish and canonical baseline source/config,
fleet quiescence and 0115 schema must be freshly re-proven before native backup.

Embedding the deployed commit's own SHA/tree in that same commit's manifest is
self-referential. **Do not manually make a BOUND commit and claim it runnable.**
The current scaffold deliberately has no such binding. A future exact source
handoff must choose and independently review a separate immutable controller
artifact/admission contract: Ops binds another repository's accepted Gate code
source SHA/tree and exact helper manifest/recipe/target; a trusted fixed executor
authenticates that artifact, owner/source/CI/protected-current-run evidence and
physical binding. Any split earlier code/later policy commits must explicitly
prove the exact code/config blob equivalence and list permitted policy-only
differences; changed whole trees never become equal by assertion. Current closed
gates are not bypassed by digest shape, an env JSON object, public callback or
boolean ACCEPT. `TrustedFixtureExecutor` has no shipped implementation, and a
future admission route is separate reviewed source work before physical effects.

Admitted execution has fixed connection timeout <=5s, lock timeout <=2s,
statement timeout <=15s and whole helper invocation <=120s (readonly proof
<=10s), shortened to the remaining grant lifetime. These are backend bounds,
not permission for a hosted operation or a provider billing enforcement claim.

Helper deployment source and final candidate source are distinct. Standalone
bootstrap/reset/operator proof must authenticate the helper process SHA/tree;
GET authenticates the final deployed candidate process SHA/tree plus helper
code/manifest compatibility. Historical accepted guard747 head599 is static
source history only. Final fixture must bind the freshly integrated/reviewed
747 candidate, exact CI and actual deployed merge SHA with proven tree equality;
it cannot inherit 599 acceptance after main/client/696 changes.

## Operator effect scope still required

No provision, paid resource, credential entry, hosted seed/reset, workflow run,
Production observation, deletion or cleanup is authorized by this source PR.
Current Gate credential is fixed-Production-project scoped and cannot provision
a new fixture. No workspace token, provider UI fallback or third service exists.
An explicitly authorized operator must supply one separate project, Gate plus
PG, one volume, one replica each, fixed physical IDs, source/config/inventory,
dedicated DB/admin bindings with human credential entry, exact synthetic-only
bootstrap/reset/proof/cleanup grants and expiry. Source never grants these itself.

Proposed resource caps remain conditional owner choices: at most 1 vCPU and
1024 MiB per service, 1024 MiB volume, two hours total; stop new setup/tests at
T+100 minutes, cleanup by T+120. Proposed additional US$5 is neither a verified
price nor an enforceable provider billing cap. No new subscription or shared
workspace compute cap is allowed by this proposal. Authenticated future executor
must enforce lifetime and aggregate invocation budgets; this server source does
not pretend process-local counters enforce cross-deployment caps.

Budgets: helper deployments <=2, PG setup deployment <=1; bootstrap/reset/seed
invocations <=1 each, readonly unknown-proof <=4 **aggregate across GET/operator
routes**, total helper invocations <=7. Gate baseline deployments <=2, candidate
deployments <=4, backups <=2 and one injected recovery failure belong to the
separate reviewed Ops fixture contract. Setup/reset receipts are retained before
reconfiguring to canonical baseline; the live first-case snapshot is saved before
reset. Future fixed workflow must retain authenticated success, reset/recovery
and cleanup evidence from the same exact current run. This source does not add
that workflow or new resources.

Backup eligibility needs actual occupied PG/WAL/storage <=50% of volume, not a
small literal seed estimate. Railway documents manual backup creation/deletion
and incremental exclusive size; locked-backup deletion authority/latency,
cleanup billing and feasibility remain UNKNOWN until exact operator proof.
Deletion of volume/project, retained locked backup handling and a fail-safe stop
owner require separate exact authority. Official references:
https://docs.railway.com/volumes/backups,
https://railway.com/pricing,
https://docs.railway.com/pricing/cost-control.

## Validation

Hermetic tests prove pre-credential denial, all known Production IDs in every
physical slot, fixed runtime/source/owner/expiry/caps and fresh reset evidence,
redacted GET and the literal recipe. The real PostgreSQL drill runs only on the
exact existing disposable CI localhost service with `GATE_ENV=ci`, creates a new
dedicated test DB, uses actual 0115 migrations, verifies literal state/journal,
denies changed sending state, exercises readonly historical 0116 schema shape
and reset, then drops only the test DB it created. It skips before reading any
DB binding outside CI. A local skip or simulated bindings never prove native
fixture ACCEPT. Necessary exact-head CI will run only after Gate owner publication
GO; remote publication/merge and physical operations are separate gates.


## Fixed migration artifact packaging

The helper authenticates the exact 119 canonical migration files through 0115
with a checked-in filename/SHA256 inventory before migration DDL. Source and
Docker artifacts locate these files relative to the authenticated helper module
and repository `pyproject.toml`; the existing Dockerfile copies that layout to
`/app`. Installed wheels carry the same canonical files, without source copies,
under the fixed `sys.prefix/share/gate745-fixture/alembic/versions` data directory.
Installed Gate distribution metadata must identify the executing helper module.
Missing, extra, substituted or symlinked migration files deny; no cwd, environment
variable, caller path or arbitrary migration-root fallback is accepted.

A hermetic packaging regression builds and installs the real wheel in a fresh
local venv away from the repository, verifies installed-module/resource discovery
and the 119-revision baseline chain, then reconstructs the existing Docker COPY
artifact and checks its bytes/discovery and three corruption denials. This checks
the Docker source layout; it does not claim a local Docker container build or
native fixture run. The original rejected AAA CI failed before seed/reset because
its installed wheel lacked migration resources. The corrected head needs its own
actual disposable PG17 drill and full original CI, then a new independent Delta
attempt; simulated bindings/local PG skips remain NOT_PROVEN.
