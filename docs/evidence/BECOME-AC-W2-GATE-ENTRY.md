# BECOME.AC W2 — Gate entry and consent-preflight evidence

Status: `simulated_verified`  
Execution: `bac-w2-transport-e4-20260801-0435`  
Frozen digest: `7afe6218f2d81c3c603541154b36f46a550db25377f6cc9a94697823ca597189`

## Bounded result

Gate now has repository-only adapters for an opaque BECOME bot binding,
profile-link entry and controlled Instagram private-reply reservation. Signed
synthetic webhook fixtures from the bot and Instagram resolve through the same
`entry_subject_ref` to one stable `intent_ref`; duplicates do not create a
second intent. The adapter never creates a Person or Quest and waits only on the
frozen downstream contract.

Voice/media work has a mandatory FakeRelations consent preflight. Missing,
denied or unknown consent cannot produce a download claim. An allowed synthetic
receipt produces only an opaque local claim—there is no downloader, URL, media
payload or provider client. Provider state remains restricted to `reserved`,
`in_flight`, `settled`, `unknown`; unknown private-reply work cannot be retried.

## Verification

- `python -m unittest discover -s tests -p 'test_become_w2_entry_adapter.py' -v`
- two signed entry fixtures → one intent and two webhook receipts;
- exact replay → no-op; re-signed mutation → fail closed;
- invalid signature and sensitive provider/media fields → fail closed;
- controlled Instagram entry → deterministic local reservation only;
- missing/denied consent → no download claim;
- allowed consent → opaque local claim with no URL or bytes;
- provider calls, downloads, replies, messages and external effects: `0`.

Gate product issues were not read or changed. TAKE was not changed. This draft
does not merge, deploy, publish, message, download or enable a provider.

