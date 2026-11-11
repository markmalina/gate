> 11 November 2026: historical source/design evidence. The active integrated
> contract is [DIALOG-SNAPSHOT-GUARD](DIALOG-SNAPSHOT-GUARD.md).
> The current plan is [Gate745, 8 sprints](https://github.com/markmalina/gate/issues/745#issuecomment-6789115422).
> Historical fixture workers, grants, old migration heads and operation windows
> do not authorize or block source sprint 2. Product fixture HTTP/worker code
> is excluded from this candidate; immutable source remains at PR747
> b975475c1c6c81035e38d9bd13a40d777933e83c. No release is authorized here.

# Gate745 isolated synthetic bootstrap source

## Preparation source contract — 12 October 2026

The next preparation seam is implemented as two pure, disconnected modules:
`gate745_fixture_identity` and `gate745_fixture_readiness`. They are **SOURCE
foundations**, not the authenticated collector or a runnable allocation driver.
They add no route, database connector, credential, provider adapter, deploy,
resource creation/deletion, scheduler or enabling switch. `identity.run()`
unconditionally denies with `E_FIXTURE_IDENTITY_UNBOUND` before inputs or effects.
The existing H2/native protocol and its barriers remain unchanged.

The identity recipe pins one SQL statement, one read-only connection/transaction/
row/invocation, 5-second connect and statement ceilings, 10-second total ceiling,
131072-byte request/response ceilings, and zero SQL connections from metadata GET.
An authenticated durable one-use reservation must be consumed before connection;
its adapter is absent and explicitly `null`. A future adapter requires its own
bounded source change and independent review. This source does not implement
process-local replay protection and mislabel it as durable reservation.

`candidate_observation()` accepts only the closed four-field allocation tuple and
the closed actual-query row shape, rejects known Production IDs in every tuple
slot, mismatched database/port, null/noncanonical address and invalid PostgreSQL
uint64 identity, and computes the exact existing H2 canonical physical digest.
Its `UNAUTHENTICATED_CALLER_INPUT` discriminator, false effect authority and false
launch authority prevent it being mistaken for `fixture-db-identity.json`.
This pure result does not prove a DB read, SQL permission, original artifact,
volume attachment, actor, signature or source/run/allocation provenance.

`check_source_contract()` validates only the closed UNBOUND proposal protocol
`gate745.fixture-preparation-source/v1`. It accounts for the maximum two Gate
physical instances during handoff: at 500 milli-vCPU/512MiB each, Gate remains
within 1vCPU/1024MiB; with one PG instance the project remains within
2vCPU/2048MiB. Provider setters, units, effective limits and fleet observations
are still unproven. Caller JSON cannot bind an allocation, grant, selector or
invoice cap. Its result is `SOURCE_CONTRACT_CONSISTENT`, never native readiness.

The proposal models a write deadline no later than original ARM+120minutes or
the earlier owner write expiry, and a separate read-only observation deadline no
later than ARM+72hours or the earlier owner read expiry. These are **proposed
contract ceilings**, not approved lifecycle changes, fresh clock evidence,
cleanup execution, renewed counters or a promised terminal purge time. The
actual original ARM and all earlier expiries still require authenticated evidence.
All preparation, overlap, retention, observer and egress cost categories must be
included; billing authority remains `NOT_PROVEN`. Consistency grants no spend.

### Closed order and evidence required before any physical preparation

| Stage | Required evidence | Current boundary |
|---|---|---|
| Compatible source | Exact Gate/Ops/C0/H2 heads/trees, original checks and independent source receipts; compatible new schema/producer/readsets | Pure source foundations only; old readers must reject the new protocol |
| Readiness | Supported caps-before-start, units, complete physical fleet, effective configuration, isolated variables and exact least-scope rights | NOT_PROVEN; no allocation or limits setter permitted |
| Cleanup/lifecycle | Exact default-off cleanup source exception and independently accepted finite driver/observer contract | Proposed; current Ops delete prohibition remains unchanged |
| Preparation grant | Exact closed graph, accepted pins, owner expiry/cost/cleanup authority; unknown IDs are not fabricated | Not issued by a source report |
| Durable ARM | Authenticated cleanup driver and original one-use allocation lineage before first paid dispatch | No driver or original ARM receipt exists here |
| Allocation | Original attributed resource IDs/results and proven caps, dedicated synthetic DB and first Hbootstrap | No resources allocated; no scan/name-based adoption |
| Identity read | Distinct preparation audience/JTI, owner/run/source/allocation binding, private DB binding, original durable one-use reservation | No authenticated route/adapter; public effect entry UNBOUND |
| Original identity artifact | Actual read counters/times/read-only fact, exact physical tuple/digest, source/run/owner/A0/allocation lineage; no secrets | Caller candidate is ineligible as this receipt |
| Final B/native grant | Bind the actual original allocation/identity evidence without resetting helper/call/lifetime budgets | Original native acceptance remains pending |
| Terminal reconciliation | Supported affirmative retained exact-ID authority; requested/queued deletion is not DELETED; storage cost accounted for | Separate proof and owner decision still needed |

Production additionally requires accepted native evidence, actual protected
autoDeploy=false, exact target/source/fleet/DB/locked-backup/cutover authority and
connected client/live product acceptance. Passing this source checker, local
tests, PostgreSQL CI, merge or deploy substitutes for none of those facts.

The source unit tests use only synthetic rows and proposals. They prove digest
compatibility, strict scope, bounded overlap/time/read/cost invariants and the
absence of an enabling/effect path. They claim no new real SQL collection or
physical fixture. The existing unchanged hosted CI PostgreSQL drill remains
separate evidence for the old H2/guard contract.

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

The original v1 HTTP capability is lazy
`GET /admin/ops/gate745-fixture-proof` in the existing health router. It has no
Settings dependency, selector, mutation mode or ordinary admin-secret fallback.
Every admission/authentication/state failure returns redacted 404 and no-store.
Dedicated server-side fixture admin credentials are consulted only after exact
manifest/runtime/trusted-executor admission. Ordinary app startup does not
install `TrustedFixtureExecutor`. This endpoint is usable on the unchanged
candidate fleet only after a separately reviewed trusted fixture binding exists.

After recovery stops the whole Gate fleet, this GET is unreachable. The v2
source below provides a separately authenticated proof-only helper process on
the same isolated service. A pre-stop GET, health count or stub result cannot
substitute for actual post-stop journal proof. Authentic source/runtime/cleanup
acceptance remains required; this document does not claim that it ran.

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
with repository `AlphaCenter/gate`. The v1 helper manifest digest hashes exact embedded
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

## Authenticated hosted helper v2

The v1 checked-in manifest, pure policy, CLI with no executor, and GET semantics
remain unchanged. `gate745_fixture_controller_pin.py` is a separate fixed C0
trust anchor. Its fixed pins bind independently accepted C0
`7ec459467b606ac67ec2110289e9018fa322b8fa`, tree
`83b2a20bbbf467bf89fb8ff28e9a8aa42ca7c2cb`.
[Delta #305 SOURCE ACCEPT](https://github.com/AlphaCenter/delta/issues/305#issuecomment-6118271130)
is the original released review; its raw terminal body digest is
`sha256:3676e3bad47a59065f1896149dbb16e82c8d200f73136ac041164133fe938f93`.
Missing or malformed pins deny before request/environment/network or credential
access; tests retain an explicit isolated null-pin case. Caller/environment pins
and self-referential helper commits are denied. This source pin supplies no
effect grant or physical target. The v1 manifest and ordinary public entry remain
UNBOUND/default-off; H2, physical resources, cleanup and native/product acceptance
need their own exact evidence.

The fixed v2 entry accepts GitHub RS256 OIDC from the reviewed reusable C0, with
GitHub's fixed HTTPS JWKS endpoint, RSA >=2048, exact issuer/header/key/audience,
immutable Gate repository and owner IDs, Mark actor, original run/attempt1,
issue-comment/main/P source, protected fixture environment, hosted runner and
the exact C0 workflow SHA. Legacy and immutable GitHub subject strings are both
literal allowlisted with independently checked repository/owner IDs. Token
lifetime is <=10 minutes. JWTs, keys and credentials never enter logs/artifacts.
The JOSE header requires `alg`, `kid`, `typ` and permits only optional `x5t`:
if present, its canonical base64url 20-byte thumbprint must equal the selected
fixed GitHub JWK's valid `x5t`. A different key's thumbprint cannot satisfy it;
missing/malformed selected thumbprints and all other header extensions deny.
Signed integer `iat` and expiry enforce the <=10-minute freshness/lifetime cap;
`nbf` must be no later than current time plus five seconds and strictly before
expiry. GitHub's documented `nbf` preceding `iat` by 600 seconds is accepted.
Official claim definitions: https://docs.github.com/en/actions/reference/security/oidc
and https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows.

C0 authenticates the private original B/source certificate, immutable owner
comment, independent source review, checks and permanent one-use grant reservation
using its ephemeral GitHub token. H receives no GitHub/App token. The signed
audience is `gate745-fixture:v2:` plus SHA256 of insertion-order JSON
`{bindingDigest,mode,index,reservationDigest}`. H hashes the original exact
`bindingRaw` bytes; it never rebuilds that digest from a parsed/normalized object.
This reviewed C0 relay and GitHub signature authenticate the evidence. Asserted
`BOUND`/`ACCEPT` fields and digest shape alone do not.

The v2 helper manifest digest equals SHA256 of Ops' canonical owner-scope
binding. Its output-only `authority.ownerGrantDigest`,
`authority.ownerGrantCommentId` and `seed.helperManifestDigest` fields are removed
before sorting object keys and emitting compact ASCII-escaped JSON. This binds
all seven configuration digests and the exact control-ledger caps without a
self-referential digest. The immutable v1 manifest bytes remain unchanged.

The exact request schema is `gate745.fixture-helper-request/v2`, with
`runBindingDigest,bindingRaw,mode,operationIndex,reservation,reservationDigest`.
No SQL/DSN/dataset/source/URL/callback selectors or role field is accepted.
`POST /admin/ops/gate745-fixture-operation` admits fixed bootstrap/reset;
`POST /admin/ops/gate745-fixture-proof` admits readonly unknown-proof only.
Admission and exact runtime precede the dedicated server-side
`GATE745_FIXTURE_ADMIN_TOKEN` and then `GATE745_FIXTURE_DATABASE_URL`.
Ordinary admin/DB/Settings fallbacks are absent. Failure returns no-store404.
The public pure signature/contract verifier returns unbranded data. Only the
fixed hosted pin/JWKS, path/role/runtime and dedicated-admin adapter may grant
effect authority; caller-supplied verification keys/pins cannot reach DB access.

One immutable B binds all phases, physical IDs and source pairs. Closed operation
order is bootstrap H, success frozen proof candidate, success live proof
candidate, conditional reset H, recovery frozen proof candidate, post-stop
proof-only H. Reset has the same original v2 owner grant and requires retained
success/live and fresh authenticated whole-fleet quiescence from C0; it never
fabricates a distinct owner comment. H also requires its first three operations
to have completed in the same durable ledger. v1 distinct-reset-grant checks are
unchanged. Limits are explicitly v2: helper deployments3, challenge GET6,
proof4, total helper invocations7, configuration transitions7, one control ledger
creation and <=7 rows. All other physical/native caps retain their fixed values.
These limits and the additional synthetic control DDL must appear in the exact
owner effect scope and original source certificate; source code grants none.

The sole additional database namespace is
`gate745_fixture_control.operation_ledger`. Only a typed cryptographically
admitted v2 operation may allow this exact schema. The physical PG identity is
verified first, then exact table/columns/types/nullability/PK/unique constraints
and absence of extra objects/triggers/functions/defaults. The table must be
permanent and logged, with no row-security policy, rewrite rule, inheritance or
unexpected namespace dependency. RLS could hide consumed rows; UNLOGGED could
lose them after a crash. v1 still rejects this
schema as foreign. A session advisory lock spans reservation commit, mode effect
and completion. RESERVED is committed before the synthetic mode effect, with
unique ordinal/JTI/reservation digests and one immutable B/run. Failure, rollback,
HTTP loss or unavailable completion retains consumed RESERVED/UNKNOWN and denies
replay; it never resets counters. Public-schema reset preserves this ledger and
the literal unknown journal. No extra service, volume, secret or operator port is
introduced. Setup stops by T+100; proof and all execution stop by grant expiry.

The standalone control process is `python -m app.worker.gate745_fixture_hosted`.
The third helper deployment is instead the literal command
`python -m app.worker.gate745_fixture_hosted proof-only`, using a separate app with
no mutation route. Validly signed bootstrap/reset requests are denied there
before credentials. `executionRole` in the v2 receipt derives from the actual
entry/app: CONTROL at setup/reset, GUARDED at the ordinary candidate proof route,
PROOF_ONLY at post-stop proof. It is never copied from the request or expected
phase. Source/runtime admission enforces the closed source/role combination.

Only the standalone helper serves aggregate GET metadata with exactly
`schemaVersion,helperPolicy,sourceSha`; source SHA comes from actual Railway
runtime, and C0/source certificates independently establish the canonical tree.
No B/run/tree/identity/DB/secret claim or mutation authority comes from metadata.
Ordinary candidate/Production metadata stays404. Challenge reads occur only at
the three helper phases and remain bounded by6.

The v2 TOML is reference-only. Railway's new-service rules require the reviewed
Ops `serviceInstanceUpdate` route and actual source-bound effective configuration
proof, with seven fixed phase digests, new-service creation evidence and fixed
port8000. Null config or requested settings alone are not runtime proof. Ordinary
`railway.toml`, predeploy and startup are untouched. No native deployment,
credential entry, control DDL, seed/reset, paid provisioning or cleanup ran here.

Hermetic tests use ephemeral synthetic RSA keys, exact positive HTTP role
handoffs and claim/signature/body/cap/order/schema/expiry/role negatives. The SAME
existing CI PG drill additionally checks real cross-session lock exclusion,
committed reservation visibility, six fixed operations, ledger preservation on
reset, v1 foreign-schema denial, altered-control-schema denial, replay and
UNKNOWN retained after effect rollback. Locally absent PG remains SKIP; only the
original changed-head CI may prove that SQL drill. It is not authentic native
fixture, whole-fleet, backup, cleanup or product acceptance.


## Fixed migration artifact packaging

The helper authenticates the exact 119 canonical migration files through 0115
with a checked-in filename/SHA256 inventory before migration DDL. Source and
Docker artifacts locate these files relative to the authenticated helper module
and repository `pyproject.toml`; the existing Dockerfile copies that layout to
`/app`. Installed wheels carry the same canonical files, without source copies,
under the fixed `sys.prefix/share/gate745-fixture/alembic/versions` data directory.
Installed Gate distribution metadata must identify the executing helper module.
Discovery is restricted to that module's own installation root and requires
exactly one matching distribution. Checkout/cwd egg-info cannot shadow it;
missing, ambiguous or foreign-module metadata denies before migration.
Missing, extra, substituted or symlinked migration files deny; no cwd, environment
variable, caller path or arbitrary migration-root fallback is accepted.

A hermetic packaging regression builds and installs the real wheel in a fresh
local venv away from the repository, verifies installed-module/resource discovery
and the 119-revision baseline chain, exposes real checkout egg-info after loading
the installed module, and proves missing/duplicate installed metadata denies.
It then reconstructs the existing Docker COPY
artifact and checks its bytes/discovery and three corruption denials. This checks
the Docker source layout; it does not claim a local Docker container build or
native fixture run. The original rejected AAA CI failed before seed/reset because
its installed wheel lacked migration resources. The corrected head needs its own
actual disposable PG17 drill and full original CI, then a new independent Delta
attempt; simulated bindings/local PG skips remain NOT_PROVEN.

The dev extra installs the same setuptools/wheel prerequisites declared by the
build system so the regression's no-index/no-build-isolation wheel command has
its backend without a network fallback. Its fixed subprocesses inherit only
allowlisted environment fields; failures expose bounded stderr for diagnosis.
The wheel install ignores inherited same-version distribution metadata inside
its disposable venv; an explicit inherited-metadata fixture proves the reviewed
wheel, not a parent's installed package, supplies the executing helper.

Schema admission compares the physical PostgreSQL spelling of canonical
unqualified Float (DOUBLE PRECISION); every other type/precision and nullability
remains exact. Hermetic schema probes and the real disposable PG drill reject
REAL and changed nullability. Canonical migrations themselves are unchanged.
