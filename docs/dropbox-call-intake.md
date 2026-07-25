# Dropbox mobile-call intake

This intake is disabled by default. It is independent of Telegram delivery and never
changes Telegram read state.

## Data flow

1. An Android sync client uploads a recording into a dedicated Dropbox App Folder.
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

Create a scoped Dropbox API application with **App Folder** access and only:

- `files.metadata.read`
- `files.content.read`
- `account_info.read` (fail-closed refresh-token/account binding)

Use the OAuth authorization-code flow with offline access so Gate receives a refresh
token. The Android sync target shown to the owner is `/Apps/<Dropbox app name>/`.
Inside the App Folder API, Gate sees that directory as the empty root path (`""`).

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
DROPBOX_CALL_INBOX_PATH=
GATE_DROPBOX_CALL_POLL_SECONDS=30
```

Enable the feature only after the migration is deployed, the dedicated App Folder is
empty or reviewed, and the Dropbox account ID has been verified. The first live test
must contain only the owner's voice or a participant who knowingly consented to the
recording and test processing.

## Gate M retrieval

The existing `gate_get_reply_context` tool accepts exact read-only selectors:

- `call:latest`
- `call:latest-file`
- `call:latest-transcript`

Results remain constrained to the caller's allowed project scope. The stored Dropbox
account ID, cursor, refresh token, app secret, and source path are not returned.

## Limits and failure handling

- Original file size is limited to 20 MiB.
- Supported extensions are `.3gp`, `.aac`, `.amr`, `.flac`, `.m4a`, `.mp3`, `.mp4`,
  `.mpeg`, `.mpga`, `.oga`, `.ogg`, `.opus`, `.wav`, and `.webm`.
- File identity is `(Dropbox account, file ID, revision)`.
- Delta cursor advancement happens only after every supported entry in the page is
  durably handled.
- OAuth, delta-list, and download failures retry with bounded exponential backoff.
- An indeterminate provider result is stored as `unknown` and is not blindly replayed.
