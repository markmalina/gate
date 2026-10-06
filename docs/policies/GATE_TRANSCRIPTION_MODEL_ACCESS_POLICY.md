# Gate transcription model-access policy

Version: `gate-transcription-model-access-v2`  
Feature: bounded server-side voice and Dropbox phone-call transcription  
Status: owner-approved implementation and exact-call Production processing policy

## Pinned access

The deterministic skill pin is `model-access.lock`. Gate may send bounded audio
only to `POST https://api.openai.com/v1/audio/transcriptions` using the
server-side `OPENAI_API_KEY` and one of these immutable models:

- default: `gpt-4o-mini-transcribe`;
- isolated recovery: `gpt-4o-transcribe`.

The provider, endpoint and model allowlist are application-owned. User input
cannot select them. No tools, Responses conversation, provider memory,
background mode, file object, search, shell, MCP or automatic retry is allowed.

## Input, output and call bounds

- Telegram download limit remains 25 MiB.
- Dropbox call source limit is 100 MiB.
- Every provider upload is at most 25,000,000 bytes.
- Audio longer than three minutes or larger than the provider upload bound is
  decoded from a seekable temporary file into mono 16 kHz MP3 at 32 kbit/s,
  split into ordered three-minute parts, and checked for decodable frames and
  complete duration coverage before any provider request.
- One recording may contain at most 20 parts.
- Each part has one attempt with a 60-second timeout.
- A failure after any completed part is an unknown outcome and is not retried
  automatically.
- Ordered part text is joined into one transcript of at most 100,000
  characters; only the previous part's final 1,000 characters may be supplied
  as continuity context.
- Raw audio, transcript text, prompts, provider bodies and private identifiers
  are forbidden in logs, GitHub evidence and receipts.

## Cost and retention

Every recording reserves budget before the first provider request. The existing
reservation includes a 5,000 micro-USD minimum per provider part for fixed
request overhead; this is a temporary reservation, not a minimum charge.
Only provider-reported actual usage is settled, and the monthly ceiling is
checked before provider I/O even when that minimum applies. The existing
`telegram_connector / telegram_business` entitlement is the hard USD 3.00
monthly ceiling (`3,000,000` micro-USD), authorized by Mark for Gate #704 on
2026-09-24. A reservation is settled from
provider-reported token usage, released only after a definite rejection, and
marked unknown after uncertain provider work. Duplicate reservation keys fail
closed.

Official OpenAI documentation rechecked on 2026-09-24 states that:

- the transcription endpoint accepts files up to 25 MB and recommends
  compression or chunks no larger than 25 MB for longer recordings;
- `/v1/audio/transcriptions` data is not used for training;
- abuse-monitoring retention is none;
- application-state retention is none;
- the endpoint is eligible for Zero Data Retention, but Gate does not claim
  that the account is provisioned for ZDR.

Sources:

- https://developers.openai.com/api/docs/guides/speech-to-text
- https://developers.openai.com/api/docs/guides/your-data

## Feature gate, failure and rollback

The Dropbox intake remains default-off through
`GATE_DROPBOX_CALL_INTAKE_ENABLED=false`. Missing credentials, malformed
configuration, unsupported media, a source or part limit breach, budget denial,
timeout, provider error, malformed response or unknown usage fails closed.
There is no outbound message or publication side effect.

Rollback disables Dropbox call intake and restores the last-known-good
Production revision recorded by the current execution. Stored source rows remain
durable for an explicitly authorized later recovery; no automatic provider
retry is introduced.

## Owner watch-note and private Mark addendum — 2026-10-05

Mark explicitly requested Galaxy Watch /Alpha/Voice → Gate transcription →
full transcript GitHub issue in private markmalina/mark, and confirmed continued
implementation at 19:42 MSK on 2026-10-05. This owner-private transport addition
reuses all v2 provider, model, timeout, part, budget and retention limits above.
It does not select a new provider/model or expand the USD 3 shared monthly ceiling.

The isolated feature is default-off (GATE_WATCH_VOICE_ENABLED=false) and requires
GATE_WATCH_VOICE_DATA_GATE_APPROVED, an exact configured owner workspace and
authenticated Dropbox account. Source is fixed /Alpha/Voice, at most 25 MiB
and 60 minutes, separate from Calls and their historical recovery.
The fixed normal model is gpt-4o-mini-transcribe. No provider retry is added;
a crashed/uncertain claim is quarantined as unknown.

The prohibition on private text/audio in shared GitHub evidence still applies.
The explicit private data destination exception is full transcripts and source
metadata in markmalina/mark issues only, authenticated and verified private before
provider I/O. Raw audio remains in Gate's protected archive, never in GitHub.
No public audio links, company payload logs, other repositories, Telegram sends,
new bot, classifier or automatic execution of incoming tasks are authorized here.

Activation requires the exact released revision, applied additive schema,
server-side GitHub credential confined to Mark Issues, verified configured writer,
and recorded rollback. Initial live acceptance cap: one exact owner-selected
watch note, at most one transcription attempt per bounded part and one initial
issue plus required transcript continuation parts, zero Telegram sends.
No initial personal data/backfill is processed in the repository test stage.

## Private Mark Actions delivery addendum — 2026-10-06

The owner requested completion of Gate #731/resources #4 and delegated this
bounded successor. This alternative to a persistent Mark token is default-off
through `GATE_WATCH_VOICE_MARK_OIDC_ENABLED=false`. It preserves every v2 model,
endpoint, media, duration, part, timeout, retention and shared USD 3 budget bound.

One authenticated private `markmalina/mark` main workflow provides its automatic
job token in a request header. Gate verifies exact GitHub OIDC issuer/audience,
RS256 signature, immutable repository and owner IDs, private visibility, main,
exact pinned workflow bytes, current main revision and one current run attempt.
The repository-scoped job token remains request-local; it is never persisted,
logged or transported elsewhere. Private repository metadata and exact approved
private binding are verified before Dropbox audio or any transcription I/O.

The fixed private binding selects one exact owner note for initial acceptance.
A later owner-approved `future-only` binding requires its delivered initial
receipt and existing Dropbox cursor; a durable cutover excludes every preexisting
archive/queued note. There is no historical import, classifier or arbitrary
client-selected repository/workspace/source/URL. One fresh run consumes a durable
hash-only replay claim before effects and processes at most one note plus one
publication part. Unknown provider work is not retried; unknown GitHub work is
read-reconciled and never blindly posted again. Zero Telegram sends.

The ordinary persistent-token worker remains off in this mode. The job token
may create full-transcript issues/comments only in private `markmalina/mark`,
validated with exact `github-actions[bot]` writer/body receipts. Runner responses
contain only bounded counts/enums; original audio and full receipt remain in the
protected Gate archive. First-note live acceptance and a verified deployed
revision/schema remain mandatory; synthetic tests are not product acceptance.

The pinned skill SHA was verified exactly on 2026-10-06. Official OpenAI speech,
data-controls and pricing documentation were rechecked: supported model and
25 MB upload/chunk limits persist; estimated mini-transcribe price remains
USD 0.003/minute. This does not alter the hard shared monthly budget or claim
account-level ZDR provisioning.

## One owner-authorized same-note quota probe — 2026-10-06

At 19:14 MSK on 2026-10-06 Mark explicitly requested checking current provider
quota by transcribing the existing archived note and completing its private
Mark task. This supersedes the earlier prerequisite for a factual restoration
confirmation. The authorization is to observe readiness, not to assert that
quota is restored. Historical `insufficient_quota` was proven by the registered
typed read-only Ops diagnostic. No payment, credential/provider change, billing
mutation, automatic retry or different-source substitution is included.

The new protected private binding `scope=initial-quota-probe` requires
`owner_quota_probe=true`, a valid `authorized_at` after the original attempt,
and the same exact source/event/hash/error with historical
`provider_cause=insufficient_quota`. Existing initial/future bindings do not
activate this probe. The unchanged private Mark OIDC/current-main/workflow/token
preflight precedes one permanent per-note claim. Only an archived note with a
definitely rejected first attempt and matched released original reservation is
eligible. Unknown work, existing transcript/publication, foreign scope and
archive mismatch fail closed. The original error, model, attempt, reservation
and immutable source remain preserved; fresh workflow/config revisions never
extend the one-shot cap. The journal records authorization kind and time, not
an unverified restoration time.

The isolated attempt uses only already-pinned `gpt-4o-transcribe`, the same
endpoint/server credential, one normalized part, one 60-second request, 25 MB
upload, 100,000-character output and zero automatic retries. A new suffixed
reservation uses the same hard shared USD3 current-month entitlement and actual
usage settlement. Unknown work stays quarantined. Readiness is derived solely
from this attempt: success, typed quota rejection, rate limitation, unknown or
not established; success is not a global account/budget guarantee.

Successful text uses only the existing private Mark outbox, one publication
part per fresh run, including read-only reconciliation of unknown publication.
Raw audio stays protected in Gate; zero Telegram messages. Default/future watch
transcription remains `gpt-4o-mini-transcribe`. Official OpenAI speech, pricing
and retention documentation was rechecked on 2026-10-06: model/endpoint/media
bounds and retention rules persist; isolated estimated price is USD0.006/minute
(input USD2.50/output USD10 per million tokens). Typed429 mapping uses only safe
error enums, never provider messages/bodies. Policy v2 and skill/lock pins remain.
The reviewed private caller may also run from the exact newly created owner
comment `/gate-watch-voice drain` on private Mark control issue #47, excluding
pull requests. Gate allows `issue_comment` only with the immutable owner actor
ID; current-main/run/attempt/private-repository/OIDC/workflow-byte verification
and the empty request body remain mandatory. The comment trigger adds no
provider calls beyond the permanent same-note cap, no arbitrary selector,
no credentials, no new scheduled automation and no automatic retry.
