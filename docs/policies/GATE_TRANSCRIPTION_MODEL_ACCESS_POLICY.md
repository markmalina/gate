# Gate transcription model-access policy

Version: `gate-transcription-model-access-v1`  
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
`telegram_connector / telegram_business` entitlement is the hard USD 1.00
monthly ceiling (`1,000,000` micro-USD). A reservation is settled from
provider-reported token usage, released only after a definite rejection, and
marked unknown after uncertain provider work. Duplicate reservation keys fail
closed.

Official OpenAI documentation rechecked on 2026-09-18 states that:

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
