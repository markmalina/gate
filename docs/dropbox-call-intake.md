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

- Original file size is limited to 25 MiB.
- Supported extensions are `.3gp`, `.aac`, `.amr`, `.flac`, `.m4a`, `.mp3`, `.mp4`,
  `.mpeg`, `.mpga`, `.oga`, `.ogg`, `.opus`, `.wav`, and `.webm`.
- File identity is `(Dropbox account, file ID, revision)`.
- Delta cursor advancement happens only after every supported entry in the page is
  durably handled.
- OAuth, delta-list, and download failures retry with bounded exponential backoff.
- An indeterminate provider result is stored as `unknown` and is not blindly replayed.


## Closeout behavior

Gate performs a one-time bounded rescan after migration 0052 so previously skipped
recordings between 20 MiB and 25 MiB are ingested. Successful call and Telegram
voice transcripts receive a stored, deterministic summary. Owner-only feed output
exposes both transcript and summary status. Provider-unknown Telegram retries remain
fail-closed unless the exact archive was explicitly authorized for one recovery attempt.
