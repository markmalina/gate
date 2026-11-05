"""Real RSA signature and hermetic v2 boundaries; keys/IDs are synthetic."""

import asyncio
import base64
import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from app.worker import gate745_fixture_controller_pin as trust
from app.worker import gate745_fixture_hosted as hosted
from app.worker import gate745_fixture_ledger as ledger
from app.worker.gate745_fixture_policy import FixtureDenied
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.test_gate745_fixture_bootstrap import HASH, NOW, manifest

PIN = ("7" * 40, "8" * 40)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
PUBLIC.update(kid="synthetic-key", alg="RS256", use="sig")
JWKS = {"keys": [PUBLIC]}


def bundle() -> dict[str, Any]:
    old = manifest()["binding"]
    target = {
        k: old["target"][k]
        for k in (
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    }
    target.update(
        repository="AlphaCenter/gate",
        healthUrl="https://gate745-synthetic.up.railway.app/admin/ops/reply-cutover",
    )
    m = {
        "schemaVersion": "agent-ops.gate745-fixture-runtime-manifest/v2",
        "state": "BOUND",
        "enabled": True,
        "binding": {
            "target": target,
            "source": {
                "reviewedHead": "c" * 40,
                "reviewedTree": "d" * 40,
                "deploySha": "c" * 40,
                "deployTree": "d" * 40,
                "baselineSha": "afe0c6b8519381a0007c0dcc1c145207b5d54a3f",
                "baselineTree": "9" * 40,
                "reviewReceiptDigest": HASH,
                "reviewIssue": 301,
                "reviewCommentId": "1010",
            },
            "authority": {
                "ownerId": 23397294,
                "controlIssue": 745,
                "workflowPath": hosted.WORKFLOW,
                "gateReviewIssue": 747,
                "protectedEnvironment": hosted.ENVIRONMENT,
                "ownerGrantDigest": HASH,
                "ownerGrantCommentId": "1002",
                "createdAt": "2026-10-11T14:00:00.000Z",
                "expiresAt": "2026-10-11T16:00:00.000Z",
            },
            "seed": {
                **old["seed"],
                "helperSourceSha": "a" * 40,
                "helperSourceTree": "b" * 40,
                "helperManifestDigest": HASH,
                "proofRouteDigest": HASH,
                "setupRouteDigest": HASH,
            },
            "inventory": [
                {
                    "role": role,
                    "serviceId": target[key],
                    "replicas": 1,
                    "configurationDigest": HASH,
                    "sourceDigest": HASH,
                    "imageDigest": None if role == "gate" else HASH,
                    "externalBindings": 0,
                }
                for role, key in (("gate", "serviceId"), ("pg", "postgresServiceId"))
            ],
            "caps": dict(hosted.CAPS),
            "configuration": {
                "mechanism": "NEW_SERVICE_SETTINGS",
                "serviceCreatedAt": "2026-10-11T13:00:00.000Z",
                "phaseDigests": hosted.configuration_digests(),
            },
            "cleanup": {
                "operatorIdentity": 23397294,
                "routeDigest": HASH,
                "deadline": "2026-10-11T16:00:00.000Z",
                "confirmed": True,
            },
        },
    }
    m["binding"]["seed"]["helperManifestDigest"] = hosted.source_scope_digest(m["binding"])
    raw = hosted.wire_json(m)
    return {
        "schemaVersion": "gate745.fixture-run-binding/v2",
        "state": "BOUND",
        "enabled": True,
        "run": {
            "id": "12345",
            "attempt": 1,
            "clientSha": "6" * 40,
            "controllerSha": PIN[0],
            "controllerTree": PIN[1],
            "workflowPath": hosted.WORKFLOW,
            "repositoryId": hosted.REPOSITORY_ID,
        },
        "ops": {"sha": "e" * 40, "tree": "f" * 40},
        "helper": {"sha": "a" * 40, "tree": "b" * 40, "policy": hosted.POLICY},
        "manifestRaw": raw,
        "manifestRawDigest": hosted.raw_digest(raw),
        "sourceCertificate": {"artifactId": "1111", "artifactDigest": HASH},
        "ownerGrant": {"commentId": "1002", "bodyDigest": HASH},
        "reservation": {"commentId": "1003", "bodyDigest": HASH},
        "targetDigest": hosted.raw_digest(
            hosted.wire_json(
                {
                    k: target[k]
                    for k in (
                        "projectId",
                        "environmentId",
                        "serviceId",
                        "postgresServiceId",
                        "volumeInstanceId",
                        "repository",
                    )
                }
            )
        ),
        "createdAt": m["binding"]["authority"]["createdAt"],
        "expiresAt": m["binding"]["authority"]["expiresAt"],
    }


def request_document(
    index: int = 1, *, b: dict[str, Any] | None = None, prior: str | None = None
) -> dict[str, Any]:
    raw = hosted.wire_json(b or bundle())
    binding_digest = hosted.raw_digest(raw)
    record = {
        "schemaVersion": "gate745.fixture-operation/v2",
        "state": "RESERVED",
        "runId": "12345",
        "runAttempt": 1,
        "bindingDigest": binding_digest,
        "mode": hosted.OPERATIONS[index - 1],
        "index": index,
        "priorCompletionDigest": prior if index > 1 else None,
        "retained": {"successDigest": HASH, "liveDigest": HASH, "quiescenceDigest": HASH}
        if index == 4
        else None,
    }
    return {
        "schemaVersion": "gate745.fixture-helper-request/v2",
        "runBindingDigest": binding_digest,
        "bindingRaw": raw,
        "mode": record["mode"],
        "operationIndex": index,
        "reservation": record,
        "reservationDigest": hosted.raw_digest(hosted.wire_json(record)),
    }


def claims(document: dict[str, Any]) -> dict[str, Any]:
    b = json.loads(document["bindingRaw"])
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "gate745-fixture:v2:"
        + hosted.raw_digest(
            hosted.wire_json(
                {
                    "bindingDigest": document["runBindingDigest"],
                    "mode": document["mode"],
                    "index": document["operationIndex"],
                    "reservationDigest": document["reservationDigest"],
                }
            )
        ),
        "sub": "repo:AlphaCenter/gate:environment:" + hosted.ENVIRONMENT,
        "repository": "AlphaCenter/gate",
        "repository_id": str(hosted.REPOSITORY_ID),
        "repository_owner": "AlphaCenter",
        "repository_owner_id": "310893851",
        "actor": "markmalina",
        "actor_id": "23397294",
        "run_id": "12345",
        "run_attempt": "1",
        "event_name": "issue_comment",
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "sha": b["run"]["clientSha"],
        "workflow_ref": "AlphaCenter/gate/" + hosted.WORKFLOW + "@refs/heads/main",
        "job_workflow_ref": "AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@"
        + PIN[0],
        "job_workflow_sha": PIN[0],
        "environment": hosted.ENVIRONMENT,
        "runner_environment": "github-hosted",
        "iat": int(NOW.timestamp()),
        "nbf": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 300,
        "jti": "synthetic-jti-" + str(document["operationIndex"]).zfill(4),
    }


def signed(document: dict[str, Any], changes: dict[str, object] | None = None) -> str:
    return jwt.encode(
        {**claims(document), **(changes or {})},
        KEY,
        algorithm="RS256",
        headers={"kid": "synthetic-key", "typ": "JWT"},
    )


def admitted(
    index: int = 1, *, b: dict[str, Any] | None = None, prior: str | None = None
) -> hosted.HostedOperation:
    document = request_document(index, b=b, prior=prior)
    operation = hosted.admit_request(
        hosted.wire_json(document).encode(), signed(document), JWKS, pin=PIN, now=NOW
    )
    # Explicit synthetic lower-layer fixture only, never hosted/native authority.
    # Real entry tests use the fixed HTTP adapter, which alone can brand effects.
    hosted._ADMITTED.add(operation)
    return operation


def test_actual_rsa_signature_and_closed_source_sequence() -> None:
    for index in range(1, 7):
        operation = admitted(index, prior=HASH)
        hosted.assert_admitted(operation)
        assert operation.binding.manifest_digest == operation.reservation["bindingDigest"]
        assert operation.proof.source_sha == ("a" if index in (1, 4, 6) else "c") * 40
        assert operation.proof.invocation == ({2: 1, 3: 2, 5: 3, 6: 4}.get(index, 1))
        if index == 4:
            assert operation.proof.owner_grant_comment_id == "1002"
            assert operation.proof.reset_grant_comment_id is None
            assert operation.proof.retained_case_receipt_digest == HASH


@pytest.mark.parametrize(
    "field,value",
    [
        ("iss", "https://untrusted.invalid"),
        ("aud", "gate745-fixture:v2:wrong"),
        ("sub", "repo:AlphaCenter/gate:ref:refs/heads/main"),
        ("repository", "other/gate"),
        ("repository_id", "1"),
        ("repository_owner", "other"),
        ("repository_owner_id", "1"),
        ("actor", "other"),
        ("actor_id", "1"),
        ("run_id", "12346"),
        ("run_attempt", "2"),
        ("event_name", "workflow_dispatch"),
        ("ref", "refs/heads/other"),
        ("ref_type", "tag"),
        ("sha", "1" * 40),
        ("job_workflow_sha", "1" * 40),
        (
            "job_workflow_ref",
            "AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@main",
        ),
        ("workflow_ref", "wrong"),
        ("environment", "production"),
        ("runner_environment", "self-hosted"),
        ("iat", int(NOW.timestamp()) + 60),
        ("nbf", int(NOW.timestamp()) + 60),
        ("exp", int(NOW.timestamp())),
        ("exp", int(NOW.timestamp()) + 601),
        ("iat", True),
        ("nbf", None),
        ("exp", "1"),
        ("jti", "short"),
    ],
)
def test_signed_wrong_claims_deny(field: str, value: object) -> None:
    document = request_document()
    with pytest.raises(FixtureDenied):
        hosted.admit_request(
            hosted.wire_json(document).encode(),
            signed(document, {field: value}),
            JWKS,
            pin=PIN,
            now=NOW,
        )


def test_wrong_signature_algorithm_key_and_body_are_denied() -> None:
    document = request_document()
    tokens = [
        signed(document)[:-10] + "AAAAAAAAAA",
        jwt.encode(
            claims(document),
            "synthetic-only-HMAC-key-for-algorithm-denial",
            algorithm="HS256",
            headers={"kid": "synthetic-key"},
        ),
    ]
    for token in tokens:
        with pytest.raises(FixtureDenied):
            hosted.admit_request(hosted.wire_json(document).encode(), token, JWKS, pin=PIN, now=NOW)
    for keys in ({"keys": []}, {"keys": [PUBLIC, PUBLIC]}, {"keys": [{**PUBLIC, "use": "enc"}]}):
        with pytest.raises(FixtureDenied):
            hosted.admit_request(
                hosted.wire_json(document).encode(), signed(document), keys, pin=PIN, now=NOW
            )
    token = signed(document)
    document["bindingRaw"] += " "
    with pytest.raises(FixtureDenied):
        hosted.admit_request(hosted.wire_json(document).encode(), token, JWKS, pin=PIN, now=NOW)


def test_optional_github_thumbprint_is_bound_to_selected_key() -> None:
    document = request_document()
    thumbprint = base64.urlsafe_b64encode(b"\x11" * 20).decode().rstrip("=")
    other_thumbprint = base64.urlsafe_b64encode(b"\x22" * 20).decode().rstrip("=")
    selected = {**PUBLIC, "x5t": thumbprint}

    def token(header: dict[str, object]) -> str:
        return jwt.encode(
            claims(document),
            KEY,
            algorithm="RS256",
            headers={"kid": "synthetic-key", "typ": "JWT", **header},
        )

    for header, keys in (
        ({}, JWKS),
        ({}, {"keys": [selected]}),
        ({"x5t": thumbprint}, {"keys": [selected]}),
    ):
        hosted.admit_request(
            hosted.wire_json(document).encode(), token(header), keys, pin=PIN, now=NOW
        )
    denied = [
        ({"x5t": thumbprint}, JWKS),
        ({"x5t": other_thumbprint}, {"keys": [selected]}),
        (
            {"x5t": other_thumbprint},
            {"keys": [selected, {**PUBLIC, "kid": "other-key", "x5t": other_thumbprint}]},
        ),
    ]
    for malformed in ("", "invalid!", "AA", thumbprint + "=", 1, None):
        denied.append(({"x5t": malformed}, {"keys": [selected]}))
        denied.append(({"x5t": thumbprint}, {"keys": [{**PUBLIC, "x5t": malformed}]}))
    for field, value in (
        ("jku", "https://untrusted.invalid/keys"),
        ("jwk", PUBLIC),
        ("crit", ["x5t"]),
        ("x5u", "https://untrusted.invalid/certificate"),
        ("x5c", ["untrusted-certificate"]),
    ):
        denied.append(({field: value, "x5t": thumbprint}, {"keys": [selected]}))
    for header, keys in denied:
        with pytest.raises(FixtureDenied):
            hosted.admit_request(
                hosted.wire_json(document).encode(), token(header), keys, pin=PIN, now=NOW
            )


def test_signed_documented_not_before_leeway_preserves_freshness() -> None:
    document = request_document()
    seconds = int(NOW.timestamp())
    hosted.admit_request(
        hosted.wire_json(document).encode(),
        signed(document, {"nbf": seconds - 600}),
        JWKS,
        pin=PIN,
        now=NOW,
    )
    for changes in (
        {"nbf": seconds + 6},
        {"nbf": seconds + 4, "exp": seconds + 4},
        {"nbf": seconds + 5, "exp": seconds + 4},
        {"nbf": True},
        {"nbf": "1"},
        {"iat": seconds - 601, "nbf": seconds - 1201},
    ):
        with pytest.raises(FixtureDenied):
            hosted.admit_request(
                hosted.wire_json(document).encode(),
                signed(document, changes),
                JWKS,
                pin=PIN,
                now=NOW,
            )


@pytest.mark.parametrize(
    "key,value",
    [
        ("helperDeploys", 2),
        ("helperDeploys", 4),
        ("unknownProofInvocations", 5),
        ("maxHelperInvocations", 8),
        ("configurationTransitions", 8),
        ("metadataChallengeReads", 7),
        ("controlLedgerCreates", 2),
        ("controlLedgerRows", 8),
        ("projects", True),
    ],
)
def test_typed_v2_caps_cannot_expand_or_impersonate_v1(key: str, value: object) -> None:
    b = bundle()
    m = json.loads(b["manifestRaw"])
    m["binding"]["caps"][key] = value
    b["manifestRaw"] = hosted.wire_json(m)
    b["manifestRawDigest"] = hosted.raw_digest(b["manifestRaw"])
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_CAPS"):
        admitted(b=b)


def test_reset_lineage_order_extra_selectors_and_expiry() -> None:
    for index in range(1, 7):
        document = request_document(index, prior=HASH)
        document["reservation"]["retained"] = None if index == 4 else {"successDigest": HASH}
        document["reservationDigest"] = hosted.raw_digest(hosted.wire_json(document["reservation"]))
        with pytest.raises(FixtureDenied):
            hosted.admit_request(
                hosted.wire_json(document).encode(), signed(document), JWKS, pin=PIN, now=NOW
            )
    document = request_document()
    document["sql"] = "untrusted"
    with pytest.raises(FixtureDenied):
        hosted.admit_request(
            hosted.wire_json(document).encode(), signed(document), JWKS, pin=PIN, now=NOW
        )
    document = request_document(4, prior=HASH)
    with pytest.raises(FixtureDenied):
        hosted.admit_request(
            hosted.wire_json(document).encode(),
            signed(document),
            JWKS,
            pin=PIN,
            now=NOW + timedelta(minutes=100),
        )


def test_public_unbound_and_ordinary_metadata_do_not_read_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(trust, "CONTROLLER_SHA", None)
    monkeypatch.setattr(trust, "CONTROLLER_TREE", None)

    class Poison:
        def get(self, _: str, default: object = None) -> object:
            raise AssertionError("Environment read before static admission")

    monkeypatch.setattr(hosted, "os", SimpleNamespace(environ=Poison()))
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_UNBOUND"):
        hosted.metadata(standalone=True)

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=hosted.app), base_url="http://fixture.test"
        ) as client:
            for path in ("operation", "proof"):
                response = await client.post("/admin/ops/gate745-fixture-" + path, json={})
                assert response.status_code == 404
                assert response.json() == {"detail": "Not Found"}
                assert response.headers["cache-control"] == "no-store"

    asyncio.run(exercise())
    monkeypatch.setattr(trust, "CONTROLLER_SHA", PIN[0])
    monkeypatch.setattr(trust, "CONTROLLER_TREE", PIN[1])
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_METADATA"):
        hosted.metadata(standalone=False)


def test_constructed_or_copied_admission_cannot_reach_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = copy.copy(admitted())
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_AUTHENTICATED_BINDING"):
        asyncio.run(ledger.execute_hosted(operation))
    app = FastAPI()
    assert not any("gate745" in route.path for route in app.routes)


def test_public_verification_with_caller_keys_and_pin_cannot_grant_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = request_document()
    operation = hosted.admit_request(
        hosted.wire_json(document).encode(), signed(document), JWKS, pin=PIN, now=NOW
    )
    accesses: list[str] = []

    class Poison:
        def get(self, key: str, default: str | None = None) -> str:
            accesses.append(key)
            raise AssertionError("pure verification must not read environment or credentials")

    monkeypatch.setattr(ledger, "os", SimpleNamespace(environ=Poison()))
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_AUTHENTICATED_BINDING"):
        asyncio.run(ledger.execute_hosted(operation, execution_role="CONTROL"))
    assert accesses == []


def test_immutable_subject_is_exact_and_owner_claims_remain_pinned() -> None:
    document = request_document()
    token = signed(
        document,
        {"sub": "repo:AlphaCenter@310893851/gate@1293746267:environment:" + hosted.ENVIRONMENT},
    )
    operation = hosted.admit_request(
        hosted.wire_json(document).encode(), token, JWKS, pin=PIN, now=NOW
    )
    assert operation.proof.source_sha == "a" * 40
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_AUTHENTICATED_BINDING"):
        hosted.assert_admitted(operation)


def test_proof_only_app_rejects_valid_signed_mutations_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accesses: list[str] = []

    class Poison:
        def get(self, key: str, default: object = None) -> object:
            accesses.append(key)
            raise AssertionError("Credential/runtime read by proof-only mutation")

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW

    async def jwks() -> dict[str, Any]:
        return JWKS

    monkeypatch.setattr(trust, "CONTROLLER_SHA", PIN[0])
    monkeypatch.setattr(trust, "CONTROLLER_TREE", PIN[1])
    monkeypatch.setattr(hosted, "datetime", Clock)
    monkeypatch.setattr(hosted, "os", SimpleNamespace(environ=Poison()))
    monkeypatch.setattr(hosted, "_load_jwks", jwks)
    proof_app = hosted.create_proof_app()
    assert not any(
        route.path == "/admin/ops/gate745-fixture-operation" for route in proof_app.routes
    )

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=proof_app), base_url="http://fixture.test"
        ) as client:
            for index in (1, 4):
                document = request_document(index, prior=HASH)
                for path in ("operation", "proof"):
                    response = await client.post(
                        "/admin/ops/gate745-fixture-" + path,
                        content=hosted.wire_json(document),
                        headers={
                            "Content-Type": "application/json",
                            "X-Gate745-OIDC": signed(document),
                            "Authorization": "Bearer synthetic-admin-token-32-bytes-long",
                        },
                    )
                    assert response.status_code == 404
                    assert response.json() == {"detail": "Not Found"}
            assert accesses == []

    asyncio.run(exercise())


def test_original_raw_binding_bytes_are_not_rebuilt_or_normalized() -> None:
    document = request_document()
    document["bindingRaw"] = "  " + document["bindingRaw"] + "\n"
    document["runBindingDigest"] = hosted.raw_digest(document["bindingRaw"])
    document["reservation"]["bindingDigest"] = document["runBindingDigest"]
    document["reservationDigest"] = hosted.raw_digest(hosted.wire_json(document["reservation"]))
    operation = hosted.admit_request(
        hosted.wire_json(document).encode(), signed(document), JWKS, pin=PIN, now=NOW
    )
    assert operation.binding.manifest_digest == hosted.raw_digest(document["bindingRaw"])


def test_duplicate_keys_nonfinite_and_configuration_drift_are_denied() -> None:
    for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
        with pytest.raises(FixtureDenied):
            hosted.parse(value)
    b = bundle()
    m = json.loads(b["manifestRaw"])
    m["binding"]["configuration"]["phaseDigests"][6] = HASH
    b["manifestRaw"] = hosted.wire_json(m)
    b["manifestRawDigest"] = hosted.raw_digest(b["manifestRaw"])
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_CONFIGURATION"):
        admitted(b=b)


def test_v2_helper_manifest_digest_binds_the_exact_owner_source_scope() -> None:
    b = bundle()
    m = json.loads(b["manifestRaw"])
    m["binding"]["seed"]["proofRouteDigest"] = "sha256:" + "f" * 64
    b["manifestRaw"] = hosted.wire_json(m)
    b["manifestRawDigest"] = hosted.raw_digest(b["manifestRaw"])
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_SOURCE"):
        admitted(b=b)
    m["binding"]["seed"]["helperManifestDigest"] = hosted.source_scope_digest(m["binding"])
    b["manifestRaw"] = hosted.wire_json(m)
    b["manifestRawDigest"] = hosted.raw_digest(b["manifestRaw"])
    operation = admitted(b=b)
    receipt = ledger.receipt_for(operation, {}, execution_role="CONTROL")
    assert receipt["helperManifestDigest"] == m["binding"]["seed"]["helperManifestDigest"]


def test_candidate_deploy_cannot_substitute_a_same_tree_unreviewed_commit() -> None:
    b = bundle()
    m = json.loads(b["manifestRaw"])
    m["binding"]["source"]["deploySha"] = "f" * 40
    m["binding"]["seed"]["helperManifestDigest"] = hosted.source_scope_digest(m["binding"])
    b["manifestRaw"] = hosted.wire_json(m)
    b["manifestRawDigest"] = hosted.raw_digest(b["manifestRaw"])
    with pytest.raises(FixtureDenied, match="E_FIXTURE_V2_SOURCE"):
        admitted(2, b=b, prior=HASH)


@pytest.mark.parametrize(
    "index,role", [(1, "CONTROL"), (2, "GUARDED"), (4, "CONTROL"), (6, "PROOF_ONLY")]
)
def test_authenticated_http_handoff_reports_actual_entry_role(
    monkeypatch: pytest.MonkeyPatch,
    index: int,
    role: str,
) -> None:
    from app.api.health_routes import router

    document = request_document(index, prior=HASH)
    op = admitted(index, prior=HASH)
    expected = dict(op.binding.target)
    env = {
        "RAILWAY_PROJECT_ID": expected["projectId"],
        "RAILWAY_ENVIRONMENT_ID": expected["environmentId"],
        "RAILWAY_SERVICE_ID": expected["serviceId"],
        "RAILWAY_GIT_COMMIT_SHA": op.proof.source_sha,
        "GATE745_FIXTURE_ADMIN_TOKEN": "synthetic-admin-token-32-bytes-long",
    }

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW

    async def jwks() -> dict[str, Any]:
        return JWKS

    calls: list[str] = []

    async def backend(
        operation: hosted.HostedOperation, *, execution_role: str
    ) -> dict[str, object]:
        hosted.assert_admitted(operation)
        calls.append(execution_role)
        return ledger.receipt_for(
            operation, {"unknownJournalDigest": HASH}, execution_role=execution_role
        )

    monkeypatch.setattr(trust, "CONTROLLER_SHA", PIN[0])
    monkeypatch.setattr(trust, "CONTROLLER_TREE", PIN[1])
    monkeypatch.setattr(hosted, "datetime", Clock)
    monkeypatch.setattr(hosted, "os", SimpleNamespace(environ=env))
    monkeypatch.setattr(hosted, "_load_jwks", jwks)
    monkeypatch.setattr(ledger, "execute_hosted", backend)
    if role == "GUARDED":
        application = FastAPI()
        application.include_router(router)
    else:
        application = hosted.create_proof_app() if role == "PROOF_ONLY" else hosted.create_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://fixture.test"
        ) as client:
            response = await client.post(
                "/admin/ops/gate745-fixture-"
                + ("proof" if op.proof.mode == "unknown-proof" else "operation"),
                content=hosted.wire_json(document),
                headers={
                    "Content-Type": "application/json",
                    "X-Gate745-OIDC": signed(document),
                    "Authorization": "Bearer " + env["GATE745_FIXTURE_ADMIN_TOKEN"],
                },
            )
            assert response.status_code == 200
            assert response.json()["executionRole"] == role
            assert response.json()["fixtureAcceptance"] == "NOT_PROVEN"
            assert calls == [role]

    asyncio.run(exercise())
