# Gate I4 private provider-disabled staging

Select `/railway.i4.toml` as the Railway service's custom Config-as-Code path.
It builds `/Dockerfile.i4`, whose image contains only the standard-library I4
HTTP module. The image neither copies nor starts `app.main`, Alembic, provider
clients, or database dependencies. Do not generate a public domain.

Required environment contract:

```text
BECOME_INTERNAL_ONLY=true
BECOME_RUNTIME_MODE=SIMULATED_TEST
BECOME_LIVE_ENABLED=false
BECOME_PROVIDER_SEND_ENABLED=false
BECOME_PAYMENT_ENABLED=false
BECOME_PUBLICATION_ENABLED=false
BECOME_MEDIA_DOWNLOAD_ENABLED=false
BECOME_EXTERNAL_EFFECTS_ENABLED=false
BECOME_I4_CONTRACT_DIGEST=91c3a210709c63738358bd0c512d11a9668acabd0997afcd68ba240bed38f573
BECOME_IC9_DIGEST=e8b30118dbd754a841e575b0bc3cb8681461381b1b138d2fa1b3a75c28e2a5e7
BECOME_SOURCE_SHA=<exact-deployed-head-sha>
HOST=0.0.0.0
PORT=<Railway-injected>
```

Start command: `python -m app.become_i4_service`.
Healthcheck: `GET /healthz`.
No `DATABASE_URL` is required or consumed.
