# Alfa transcription — production runbook

## Owner flow

1. Mark attaches an audio/video recording in Alfa and says `расшифруй`.
2. Alfa places the private source in Dropbox under `/Alpha/Transcriptions/Inbox/...` and calls `gate_alpha_create_transcription` with that path.
3. Gate returns a durable `job_id`. Alfa polls `gate_alpha_get_transcription` or lists recent jobs.
4. When state is `completed`, Alfa reads `verbatim.md` and `clean.md` through `gate_alpha_get_transcription_artifact`. `analysis.md` is created only when requested.
5. Long artifacts are read page by page with `offset` and `limit`; no new provider call is made.

A direct binary integration may instead POST the media body to `/alpha/transcriptions/upload` with Bearer authorization and headers `X-Filename`, optional `X-Title`, and optional `X-Analysis: true`.

## Durable states

- `queued` — accepted, no provider call.
- `preparing` — source is being copied, hashed and split.
- `transcribing` — exact parts are processed one at a time.
- `assembling` — provider work is complete; only private artifacts are being assembled.
- `completed` — source and required artifacts are durable.
- `failed` — known failure; an owner may explicitly call `gate_alpha_retry_transcription`.
- `unknown` — a provider request may have completed; automatic and owner retries are blocked until reconciliation.
- `duplicate` — the source SHA already has a canonical job; no provider call is made.

## Safety invariants

- Source bytes and transcript text stay in private Dropbox storage, never PostgreSQL, GitHub, issues, or logs.
- PostgreSQL stores only paths, SHA-256 digests, bounded metadata, states, provider request references, usage and cost.
- The part row is committed as `running` with `attempts=1` before the OpenAI request.
- A process interruption with a `running` part becomes `unknown` on startup. It is never retried automatically.
- Known failures may be retried only through an explicit tool call, which creates a new immutable `attempt_version`.
- Duplicate source SHA-256 values reuse the canonical job.
- Artifact-storage failures after successful ASR retry only assembly, not transcription.

## Chunk and completeness contract

- Every recording is decoded by ffmpeg, including supported video containers.
- Duration is measured by ffprobe.
- Parts are 230 seconds with a 225-second stride, giving a five-second overlap.
- The final part must end at the measured source duration.
- Every part must have an exact source digest, one provider attempt, a private transcript artifact and a provider receipt before assembly.
- Confirmed repeated word sequences at adjacent overlaps are removed conservatively.

## Artifacts

Each completed job has:

- permanent private source;
- `verbatim.md`;
- `clean.md`;
- optional `analysis.md`;
- `manifest.json` with source digest, duration, model, language, parts, usage, cost and artifact digests.

## Configuration

The product reuses existing Gate production secrets:

- `OPENAI_API_KEY`;
- `OPENAI_API_BASE_URL`;
- Dropbox app key, secret, refresh token and account ID already used by the call-intake worker;
- existing Gate MCP OAuth configuration.

No new Railway secret is required. Deployment remains GitHub merge → Railway automatic deployment.

## Recovery

- `failed`: inspect `error_code`; retry explicitly only when the failure is known and the source/part contract is unchanged.
- `unknown`: do not retry. Reconcile the provider request reference or cost receipt first.
- `assembling`: the worker automatically retries private artifact assembly without a provider call.
- `duplicate`: use the returned `canonical_job_id`.

## Acceptance

Use source SHA-256 `c400f62b24429ae38d755632f81fa945624749b5cfaf8adb2744d431747ca5a9` as the Chernigovskaya fixture. Import the already completed artifact and receipt from Gate issue #461; do not make another paid OpenAI call.

A separate independent Work must verify exact-head CI, deployment, MCP tool discovery, fixture reuse without a provider attempt, and one new owner-supplied end-to-end recording.
