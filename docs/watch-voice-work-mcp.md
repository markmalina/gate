# Protected Watch Work MCP adapter — #770 / PR766

This default-off source stage wraps the accepted internal journal. It does not
provide speech recognition, authorize a provider call, enable a worker, apply
schema or establish live availability in a connected Work. A deployed revision,
current connector discovery and separately admitted product checks are required.
The current connector lacks these operations and a standalone Work STT tool.

## Containment

`GATE_WATCH_VOICE_WORK_MCP_ENABLED` defaults to false. Discovery and calls require
OAuth mode, the internal reserve flag, early Inbox flag, data gate, exact owner
workspace/Dropbox account configuration and a nonempty server-configured
`GATE_WATCH_VOICE_WORK_OWNER_SUBJECT`. That value is the exact validated OAuth
subject, not a caller parameter. Only that subject with `gate:agents:manage` and
the configured workspace/Unclassified project grant may operate this adapter.
Temporary read bearer credentials cannot write. OAuth issuer/audience/signature,
expiry and scope use the existing validation path.

The journal actor derives from issuer/resource/auth method/workspace/subject.
Caller-supplied actors, repositories, accounts, URLs, providers and extra fields
are rejected. Source is one canonical UUID, exact revision and audio SHA-256.
There is no `latest` fallback or historical import. The existing journal owns
source admission, fencing, Gate/Work exclusion, expiry, immutable text and issue
binding. Driver/default-off changes do not remove durable barriers.

## Operations

| Tool | Required arguments | Result |
| --- | --- | --- |
| `gate_watch_voice_claim` | `event_id`, `token`, `revision`, `audio_sha256` | Reserves that archived source before any Gate attempt |
| `gate_watch_voice_start` | `event_id`, `token` | First transition reports `start_recorded_now=true`; replay reports false |
| `gate_watch_voice_submit` | claim arguments plus `text`, `transcript_sha256` | Freezes complete exact UTF-8 text; returns its journal phase |

The caller creates a fresh random 32–128 character URL-safe nonce and retains it
for replay after a lost response. Gate stores only its hash. Reuse the identical
nonce, source and text on retry. A changed candidate is rejected. Strict input
schemas bound text to 100,000 characters; internal validation also enforces
400,000 UTF-8 bytes, valid encoding and exact digest. Validation errors expose
only fixed codes, never transcript/nonce. Replies contain neither text nor nonce.

`prepared` means stored in Gate. `saved` requires complete durable publication
receipts. `accepted` additionally requires all original comment IDs/bodies/writers
and ordering to pass the existing readback and final locked checks. Submit itself
does not post to GitHub, call a provider, run a worker or claim live Mark acceptance.
The existing outbox later advances the same issue; replay can observe its phase.
`provider_authority` is always false, including the first start response. A caller
must not use start/replay as a provider grant or repeat an uncertain started STT.
Source/current status remains available through `watch-voice-status:<UUID>`.

Journal writes use independent sessions on the process AsyncEngine, preserving
the caller session's pending/dirty objects and transaction. A connection-bound or
unavailable session fails closed. No new migrations, dependencies, model/policy
changes, external URLs, provider calls or live configuration accompany this stage.

## Verification and release

Synthetic tests cover the actual `/mcp` HTTP route and discovery/metadata,
claim/start/submit/replay through verified same-issue outbox acceptance,
owner/workspace/project/scope containment, disabled paths, strict spoofing rejection,
source/hash mismatch, OAuth challenge and caller-session isolation. Existing Watch
race/readback/expiry tests remain applicable. These fixtures contain no real audio.

An exact new commit/CI and a fresh independent review must cover this adapter;
the prior journal SOURCE_ACCEPT does not transfer to new source. Production and
connector activation, real Work STT and early-card/full-text/latency/restart product
acceptance remain separate. Rollback turns off the MCP admission flag and preserves
all journal rows; never remove already-persisted fences or discard a submitted text.
