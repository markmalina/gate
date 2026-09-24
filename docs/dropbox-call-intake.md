# Dropbox mobile-call intake

This intake is disabled by default. It is independent of Telegram delivery and never
changes Telegram read state.

## Data flow

1. An Android sync client uploads a recording into the existing `/Alpha/Calls`
   Dropbox folder.
2. Dropbox signs and posts a change notification to
   `https://<gate-host>/dropbox/calls/webhook`.
3. Gate verifies `X-Dropbox-Signature` with the app secret and durably marks the
   configured Dropbox account for a delta scan.
4. The background worker exchanges the refresh token, advances the persisted
   `list_folder` cursor, and downloads the exact changed revision.
5. Gate stores the bounded original recording in PostgreSQL, probes its duration,
   reserves from the existing Gate transcription budget, transcribes in Russian,
   and stores the transcript beside the original.

Duration probing uses a temporary seekable file (some M4A files cannot expose
their metadata over a nonseekable pipe). When metadata is absent or the
15-second probe times out, Gate decodes audio to a null sink for at most
120 seconds and uses the final decoded timestamp. Calls over 60 minutes are
rejected before ASR. Previously rejected duration-unknown/probe-failed rows
are retried from their stored exact revision, checked against their stored
SHA-256 and prior billing reservations, and then resumed only if no previous
provider work can have occurred. Stored bytes that fail the size/SHA-256 check
produce `dropbox_call_archive_integrity_failed`; bytes that cannot be decoded
produce `dropbox_call_media_unreadable`. Both are terminal without a provider
call. A file above the 100 MiB Dropbox limit blocks
cursor advancement and exposes `dropbox_call_download_limit_exceeded` in
owner-only intake status until the owner replaces/removes that revision.

Webhook payloads are signals only. They do not contain or transport recording bytes.

## Dropbox application

Create a scoped Dropbox API application with **Full Dropbox** access and only:

- `files.metadata.read`
- `files.content.read`
- `account_info.read` (fail-closed refresh-token/account binding)

Use the OAuth authorization-code flow with offline access so Gate receives a refresh
token. Full Dropbox access is required because `/Alpha/Calls` is an existing normal
folder rather than a folder beneath `/Apps/<Dropbox app name>/`. Keep the app
read-only with the scopes above.

Dropbox webhook notifications are app/account-level signals; Dropbox does not create
a separate webhook subscription for an individual folder. Gate therefore roots its
persisted `list_folder` cursor at exactly `/Alpha/Calls` and rejects entries outside
that path before download or transcription. Sibling folders such as `/Alpha/Camera`
are never ingested.

Configure Dropbox's webhook URL as:

```text
https://gate-production-bab1.up.railway.app/dropbox/calls/webhook
```

## Runtime configuration

All values are release-controlled environment variables. Secrets must never be
committed or logged.

```text
GATE_DROPBOX_CALL_INTAKE_ENABLED=false
DROPBOX_CALL_APP_KEY=
DROPBOX_CALL_APP_SECRET=
DROPBOX_CALL_REFRESH_TOKEN=
DROPBOX_CALL_ACCOUNT_ID=
DROPBOX_CALL_INBOX_PATH=/Alpha/Calls
GATE_DROPBOX_CALL_POLL_SECONDS=30
```

Enable the feature only after the migration is deployed, `/Alpha/Calls` is empty or
reviewed, and the Dropbox account ID has been verified. The first live test must
contain only the owner's voice or a participant who knowingly consented to the
recording and test processing.

## Gate M retrieval

The existing `gate_get_reply_context` tool accepts exact read-only selectors:

- `call:latest`
- `call:latest-file`
- `call:latest-transcript`

Results remain constrained to the caller's allowed project scope. The stored Dropbox
account ID, cursor, refresh token, app secret, and source path are not returned.

## Limits and failure handling

### Read-only intake diagnostics

`gate_get_status` includes `dropbox_calls` for the configured owner workspace
with the intake project's grant. It reports configuration-presence booleans,
the persisted inbox cursor's presence, retry state and allowlisted error code,
and at most 20 recent archive records in the caller's project/source-account
scope. It never polls Dropbox, calls a model, retries a recording, or reads audio
and transcript payloads. `state_updated_at` is state activity, not proof of a
successful poll or worker heartbeat.

`source_revision_sha256` is SHA-256 of the UTF-8 string
`<Dropbox file id>\n<Dropbox revision>`, permitting exact source correlation
without returning filenames, paths, cursors or provider identifiers. Configuration
and global inbox evidence are hidden when the owner/intake-project fence fails.

- Original file size is limited to 100 MiB; each provider part is limited to 25,000,000 bytes.
- Supported extensions are `.3gp`, `.aac`, `.amr`, `.flac`, `.m4a`, `.mp3`, `.mp4`,
  `.mpeg`, `.mpga`, `.oga`, `.ogg`, `.opus`, `.wav`, and `.webm`.
- File identity is `(Dropbox account, file ID, revision)`.
- Delta cursor advancement happens only after every supported entry in the page is
  durably handled.
- OAuth, delta-list, and download failures retry with bounded exponential backoff.
- An indeterminate provider result is stored as `unknown` and is not blindly replayed.


## Closeout behavior

Gate performs a one-time bounded rescan after migration 0053 so previously skipped
recordings between 20 MiB and 25 MiB are ingested. Successful call and Telegram
voice transcripts receive a stored, deterministic summary. Owner-only feed output
exposes both transcript and summary status. Provider-unknown Telegram retries remain
fail-closed unless the exact archive was explicitly authorized for one recovery attempt.
