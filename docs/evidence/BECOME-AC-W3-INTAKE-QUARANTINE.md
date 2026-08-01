# BECOME.AC W3 — Gate voice/text intake and quarantine evidence

Status: `simulated_verified`  
Execution: `bac-w3-transport-e4-20260801-0535`  
Frozen digest: `af767a200a3dc20396814c1c36fafacd9ae871e4273bf0b04b5fe53056b502f6`

Gate accepts explicitly synthetic text and opaque Telegram file references. It
persists only text/file-reference digests. Voice intake requires the prior
opaque download claim and ConsentReceipt, then remains quarantined until an
explicit local decision. A released asset produces only an opaque TAKE request;
there is no downloader, provider client, URL, transcript or media byte path.

Verification:

- `python -m unittest discover -s tests -p 'test_become_w3_voice_text_intake.py' -v`;
- exact voice replay no-op; mutated replay rejected;
- pending/rejected quarantine cannot create a TAKE request;
- released quarantine creates a digest-only request naming TAKE;
- provider state restricted to `reserved/in_flight/settled/unknown`;
- unknown state cannot be blindly retried;
- content/media/provider/message/download effects: `0`.

Gate remains transport-only. Gate product issues were not read or changed.
This draft does not merge, deploy, download, transcribe, message or publish.

