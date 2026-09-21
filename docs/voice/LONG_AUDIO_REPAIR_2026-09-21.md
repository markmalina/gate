# Complete bounded long-audio transcription

Issue: [#685](https://github.com/AlphaCenter/gate/issues/685).
Execution: `gate-long-audio-repair-20260921T091200Z`.
Owner: Gate Alpha. Baseline/rollback: `efe67ca816c1ca86268a60171cf3b8d3816bdaec`.

## Confirmed defect

The archive bound was repaired in #688. The remaining provider HTTP 400 was
reproduced locally without a provider request. The existing ffmpeg invocation
read M4A through `pipe:0`; recordings with end-of-file container metadata need
seekable input. Both retained large sources yielded an invalid 400-byte MP3,
despite ffmpeg returning zero. ffprobe rejected that output.

| Source bytes | Original duration | Broken output | Repaired output |
| --- | --- | --- | --- |
| 29,982,127 | 1850.723083 s | one invalid 400-byte file | 11 validated MP3 parts |
| 37,585,783 | 2320.091750 s | one invalid 400-byte file | 13 validated MP3 parts |

Original Dropbox content hashes were independently verified. No source filename,
Dropbox identifier, audio, transcript, provider body or credential is committed.

## Implementation

- Read audio from a seekable temporary file; finalize converted WAV headers.
- Split audio longer than three minutes, including compressed files below the
  upload size ceiling. Preserve the 100 MiB source ceiling, 20-part maximum,
  25,000,000-byte upload ceiling, 60-second provider timeout and zero retries.
- Verify every generated part with ffprobe and compare aggregate duration to
  the source. Fail before provider work on empty, corrupt, incomplete or
  out-of-bound audio. Temporary files are removed after normalization.
- Preserve ordered transcript assembly and previous-part continuity context;
  treat oversized aggregate output as unknown paid work, without retry.
- Advance Dropbox discovery to version 2 once. Rediscovery skips existing
  file/revision identities and finds supported files previously size-filtered.
- Grant `recovery_v14` only to two exact owner-authorized, transcript-free,
  definitively HTTP-400-rejected archives, with expected sizes and intact bytes.
  Successful, foreign and unknown rows cannot receive a grant. The claim uses a
  distinct reservation key, current-month admission and a durable generation
  marker across restart. Old workers ignore the new status during deployment.
- Expose scoped whole-archive completion counts and bounded missing-record
  metadata, so a historical failure cannot disappear behind 20 newer successes.

## Recovery authority and bounds

Mark's September 21 instruction explicitly requests working transcription of
all retained long recordings, superseding the earlier decision to leave the
July definite rejection as an accepted exception. Exact recovery archives:

- `a1a82247-434e-4f43-8974-35a972ba9dc2`, 25,505,021 bytes;
- `050409d7-4e44-4df6-896f-c5023ced70ae`, 37,585,783 bytes.

The 29,982,127-byte retained source is first-time ingestion through idempotent
rediscovery. This execution admits at most these three currently missing long
sources and at most 20 provider requests per recording, subject to the existing
USD 1 monthly hard budget. Ordinary future intake retains the same policy.
No Telegram send, unknown-outcome replay, successful transcript replacement,
credential action or budget increase is authorized by this repair.

Model policy: `gate-transcription-model-access-v1`; unchanged skill digest
`6cc0efbcb916df3974c196513884ed895f3e7ae828ec1a4225fa3517fead2709`.
Official endpoint schema, pricing and retention were rechecked on September 21:
[transcription](https://developers.openai.com/api/docs/guides/speech-to-text),
[pricing](https://developers.openai.com/api/docs/pricing),
[data controls](https://developers.openai.com/api/docs/guides/your-data).
No training or endpoint retention is claimed beyond that accepted policy;
account-level Zero Data Retention provisioning is not asserted.

## Verification and acceptance

Local real-media regression generates a tail-metadata M4A with distinct tones
at the beginning and end, proves both survive in ordered parts, and verifies
duration coverage. Other tests cover compressed long audio, invalid headers,
bounded output, recovery reservation continuity, PostgreSQL exact grant guards,
lossless downgrade, scope isolation and incomplete-record inventory.

Require exact-head full CI, migrations and synthetic-merge checks before the
registered current-main Ops deployment. Runtime acceptance is separate: all
three source records must expose complete stored text and summaries, the
missing-call count must be checked across the whole archive, and inbox errors
must remain zero. Store the deployed revision, receipt digest, counts, bounded
cost outcome and any remaining limitations in #685; CI alone is not completion.

Rollback restores the baseline code. Withdraw only unconsumed v14 grants;
preserve completed/unknown provider work, source rows and the wider 100 MiB
archive schema. Unknown outcomes always require a separately recorded decision.
