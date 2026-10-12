"""Fixed C0-authenticated synthetic helper, without Settings or provider ports.

B is relayed by the exact reviewed C0 after original-artifact admission. Its
hash is inside GitHub's signed audience. Data shape never substitutes for that
signature. No GitHub/App token is copied into this service.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Any, cast
from urllib.parse import urlsplit
from weakref import WeakSet

from app.worker.gate745_fixture_controller_pin import controller_pin
from app.worker.gate745_fixture_policy import (
    DIGEST,
    ENVIRONMENT,
    POSITIVE_ID,
    PRODUCTION_IDS,
    SHA,
    WORKFLOW,
    Binding,
    ExecutionProof,
    FixtureDenied,
    admit_manifest,
    check_runtime,
    exact,
    instant,
    match,
)
from app.worker.gate745_fixture_recipe import JOURNAL_DIGEST, RECIPE_DIGEST

JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks"
POLICY = "gate745.fixture-hosted-helper/v2"
REPOSITORY_ID = 1293746267
OPERATIONS = (
    "bootstrap",
    "unknown-proof",
    "unknown-proof",
    "reset",
    "unknown-proof",
    "unknown-proof",
)
CAPS = {
    "projects": 1,
    "services": 2,
    "volumes": 1,
    "replicasPerService": 1,
    "vCpuPerService": 1,
    "memoryMiBPerService": 1024,
    "volumeMiB": 1024,
    "baselineDeploys": 2,
    "candidateDeploys": 4,
    "backups": 2,
    "helperDeploys": 3,
    "pgSetupDeploys": 1,
    "injectedFailures": 1,
    "lifetimeMinutes": 120,
    "stopNewTestsMinute": 100,
    "additionalUSD": 5,
    "bootstrapInvocations": 1,
    "resetInvocations": 1,
    "seedInvocations": 1,
    "unknownProofInvocations": 4,
    "maxHelperInvocations": 7,
    "metadataChallengeReads": 6,
    "configurationTransitions": 7,
    "controlLedgerCreates": 1,
    "controlLedgerRows": 7,
}


def raw_digest(raw: str | bytes) -> str:
    return "sha256:" + hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def wire_json(value: object) -> str:
    """The fixed ASCII control records use JSON.stringify insertion order."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def source_scope_digest(binding: dict[str, Any]) -> str:
    """Ops v2 owner scope, with its three output-only cycle fields removed."""
    scope = json.loads(wire_json(binding))
    del scope["authority"]["ownerGrantDigest"]
    del scope["authority"]["ownerGrantCommentId"]
    del scope["seed"]["helperManifestDigest"]
    return raw_digest(
        json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    )


def configuration_digests() -> list[str]:
    gate = "/bin/sh -c 'exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"
    helper = "python -m app.worker.gate745_fixture_hosted"
    result = []
    for phase in ("Hbootstrap", "baseline", "guard", "Hreset", "baseline", "guard", "Hproof"):
        is_helper = phase.startswith("H")
        settings = {
            "startCommand": helper + (" proof-only" if phase == "Hproof" else "")
            if is_helper
            else gate,
            "preDeployCommand": ["python -m app.worker.reply_cutover"] if phase == "guard" else [],
            "rootDirectory": "/",
            "dockerfilePath": "Dockerfile",
            "railwayConfigFile": None,
            "healthcheckPath": "/admin/ops/gate745-fixture-metadata" if is_helper else "/ready",
            "healthcheckTimeout": 30,
            "restartPolicyType": "NEVER",
            "restartPolicyMaxRetries": 0,
        }
        result.append(raw_digest(json.dumps(settings, sort_keys=True, separators=(",", ":"))))
    return result


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureDenied("E_FIXTURE_V2_JSON")
        result[key] = value
    return result


def _nonfinite(_: str) -> object:
    raise FixtureDenied("E_FIXTURE_V2_JSON")


def parse(raw: str | bytes, limit: int = 65536) -> dict[str, Any]:
    if len(raw.encode() if isinstance(raw, str) else raw) > limit:
        raise FixtureDenied("E_FIXTURE_V2_JSON")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (ValueError, UnicodeDecodeError):
        raise FixtureDenied("E_FIXTURE_V2_JSON") from None
    if type(value) is not dict:
        raise FixtureDenied("E_FIXTURE_V2_JSON")
    return value


def _unbase64(part: str, limit: int = 16384) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", part) or len(part) > limit * 2:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    try:
        raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except ValueError:
        raise FixtureDenied("E_FIXTURE_V2_OIDC") from None
    if len(raw) > limit or base64.urlsafe_b64encode(raw).decode().rstrip("=") != part:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    return raw


def verify_oidc(
    token: str, jwks: dict[str, Any], *, expected: dict[str, Any], now: datetime
) -> dict[str, Any]:
    import jwt
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

    if not isinstance(token, str) or len(token) > 32768 or token.count(".") != 2:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    parts = token.split(".")
    header = parse(_unbase64(parts[0]))
    exact(header, {"alg", "kid", "typ"} | ({"x5t"} if "x5t" in header else set()))
    claims = parse(_unbase64(parts[1]))
    if header["alg"] != "RS256" or header["typ"] != "JWT" or not isinstance(header["kid"], str):
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", header["kid"]):
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    keys = jwks.get("keys")
    if type(keys) is not list or len(keys) > 10:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    selected = [key for key in keys if type(key) is dict and key.get("kid") == header["kid"]]
    if len(selected) != 1:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    key = selected[0]
    if key.get("kty") != "RSA" or key.get("use") != "sig" or key.get("alg") != "RS256":
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    if "x5t" in header:
        thumbprint = header["x5t"]
        selected_thumbprint = key.get("x5t")
        if not isinstance(thumbprint, str) or not isinstance(selected_thumbprint, str):
            raise FixtureDenied("E_FIXTURE_V2_OIDC")
        if len(_unbase64(thumbprint, 20)) != 20 or len(_unbase64(selected_thumbprint, 20)) != 20:
            raise FixtureDenied("E_FIXTURE_V2_OIDC")
        if thumbprint != selected_thumbprint:
            raise FixtureDenied("E_FIXTURE_V2_OIDC")
    for field in ("n", "e"):
        if not isinstance(key.get(field), str):
            raise FixtureDenied("E_FIXTURE_V2_OIDC")
        _unbase64(key[field])
    try:
        public = jwt.PyJWK.from_dict(key).key
        if not isinstance(public, RSAPublicKey) or public.key_size < 2048:
            raise FixtureDenied("E_FIXTURE_V2_OIDC")
        _unbase64(parts[2], 1024)
        verified = jwt.decode(
            token,
            public,
            algorithms=["RS256"],
            options={
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.PyJWTError:
        raise FixtureDenied("E_FIXTURE_V2_OIDC") from None
    except (ValueError, TypeError):
        raise FixtureDenied("E_FIXTURE_V2_OIDC") from None
    if verified != claims:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    audience = "gate745-fixture:v2:" + raw_digest(
        wire_json(
            {
                "bindingDigest": expected["bindingDigest"],
                "mode": expected["mode"],
                "index": expected["index"],
                "reservationDigest": expected["reservationDigest"],
            }
        )
    )
    required = {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": audience,
        "repository": "AlphaCenter/gate",
        "repository_id": str(REPOSITORY_ID),
        "repository_owner": "AlphaCenter",
        "repository_owner_id": "310893851",
        "actor": "markmalina",
        "actor_id": "23397294",
        "run_id": expected["runId"],
        "run_attempt": "1",
        "event_name": "issue_comment",
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "sha": expected["clientSha"],
        "workflow_ref": "AlphaCenter/gate/" + WORKFLOW + "@refs/heads/main",
        "job_workflow_ref": "AlphaCenter/gate/.github/workflows/gate745-fixture-controller.yml@"
        + expected["controllerSha"],
        "job_workflow_sha": expected["controllerSha"],
        "environment": ENVIRONMENT,
        "runner_environment": "github-hosted",
    }
    if any(claims.get(k) != v for k, v in required.items()):
        raise FixtureDenied("E_FIXTURE_V2_OIDC_CLAIMS")
    if claims.get("sub") not in (
        "repo:AlphaCenter/gate:environment:" + ENVIRONMENT,
        "repo:AlphaCenter@310893851/gate@1293746267:environment:" + ENVIRONMENT,
    ):
        raise FixtureDenied("E_FIXTURE_V2_OIDC_CLAIMS")
    seconds = int(now.timestamp())
    iat, nbf, exp = (claims.get(k) for k in ("iat", "nbf", "exp"))
    if any(type(v) is not int for v in (iat, nbf, exp)):
        raise FixtureDenied("E_FIXTURE_V2_OIDC_CLAIMS")
    iat, nbf, exp = cast(tuple[int, int, int], (iat, nbf, exp))
    if (
        iat > seconds + 5
        or nbf > seconds + 5
        or exp <= seconds
        or nbf >= exp
        or not 0 < exp - iat <= 600
        or seconds - iat > 600
    ):
        raise FixtureDenied("E_FIXTURE_V2_OIDC_CLAIMS")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", jti):
        raise FixtureDenied("E_FIXTURE_V2_OIDC_CLAIMS")
    return {"jtiDigest": raw_digest(jti), "expiresAt": exp}


@dataclass(frozen=True, eq=False)
class HostedOperation:
    binding: Binding
    proof: ExecutionProof
    bundle: dict[str, Any]
    reservation: dict[str, Any]
    reservation_digest: str
    jti_digest: str
    jwt_expires: int


_ADMITTED: WeakSet[HostedOperation] = WeakSet()


def assert_admitted(operation: HostedOperation) -> None:
    if not isinstance(operation, HostedOperation) or operation not in _ADMITTED:
        raise FixtureDenied("E_FIXTURE_V2_AUTHENTICATED_BINDING")


def admit_request(
    raw: bytes, token: str, jwks: dict[str, Any], *, pin: tuple[str, str], now: datetime
) -> HostedOperation:
    """Pure contract/signature verification; data carries no effect authority."""
    request = exact(
        parse(raw),
        {
            "schemaVersion",
            "runBindingDigest",
            "bindingRaw",
            "mode",
            "operationIndex",
            "reservation",
            "reservationDigest",
        },
    )
    if (
        request["schemaVersion"] != "gate745.fixture-helper-request/v2"
        or type(request["operationIndex"]) is not int
    ):
        raise FixtureDenied("E_FIXTURE_V2_REQUEST")
    index = request["operationIndex"]
    if not 1 <= index <= len(OPERATIONS) or request["mode"] != OPERATIONS[index - 1]:
        raise FixtureDenied("E_FIXTURE_V2_ORDER")
    mode = request["mode"]
    if (
        not isinstance(request["bindingRaw"], str)
        or raw_digest(request["bindingRaw"]) != request["runBindingDigest"]
    ):
        raise FixtureDenied("E_FIXTURE_V2_BINDING_DIGEST")
    b = exact(
        parse(request["bindingRaw"]),
        {
            "schemaVersion",
            "state",
            "enabled",
            "run",
            "ops",
            "helper",
            "manifestRaw",
            "manifestRawDigest",
            "sourceCertificate",
            "ownerGrant",
            "reservation",
            "targetDigest",
            "createdAt",
            "expiresAt",
        },
    )
    run = exact(
        b["run"],
        {
            "id",
            "attempt",
            "clientSha",
            "controllerSha",
            "controllerTree",
            "workflowPath",
            "repositoryId",
        },
    )
    if (
        b["schemaVersion"] != "gate745.fixture-run-binding/v2"
        or b["state"] != "BOUND"
        or b["enabled"] is not True
        or type(run["attempt"]) is not int
        or run["attempt"] != 1
        or type(run["repositoryId"]) is not int
        or run["repositoryId"] != REPOSITORY_ID
        or run["workflowPath"] != WORKFLOW
        or (run["controllerSha"], run["controllerTree"]) != pin
    ):
        raise FixtureDenied("E_FIXTURE_V2_BINDING")
    for key in ("clientSha", "controllerSha", "controllerTree"):
        match(run[key], SHA)
    match(run["id"], POSITIVE_ID)
    for item, keys in ((b["ops"], {"sha", "tree"}), (b["helper"], {"sha", "tree", "policy"})):
        exact(item, keys)
        match(item["sha"], SHA)
        match(item["tree"], SHA)
    if b["helper"]["policy"] != POLICY:
        raise FixtureDenied("E_FIXTURE_V2_POLICY")
    for ref in (b["ownerGrant"], b["reservation"]):
        exact(ref, {"commentId", "bodyDigest"})
        match(ref["commentId"], POSITIVE_ID)
        match(ref["bodyDigest"], DIGEST)
    exact(b["sourceCertificate"], {"artifactId", "artifactDigest"})
    match(b["sourceCertificate"]["artifactId"], POSITIVE_ID)
    match(b["sourceCertificate"]["artifactDigest"], DIGEST)
    record = exact(
        request["reservation"],
        {
            "schemaVersion",
            "state",
            "runId",
            "runAttempt",
            "bindingDigest",
            "mode",
            "index",
            "priorCompletionDigest",
            "retained",
        },
    )
    if (
        raw_digest(wire_json(record)) != request["reservationDigest"]
        or record["schemaVersion"] != "gate745.fixture-operation/v2"
        or record["state"] != "RESERVED"
        or record["runId"] != run["id"]
        or type(record["runAttempt"]) is not int
        or record["runAttempt"] != 1
        or record["bindingDigest"] != request["runBindingDigest"]
        or record["mode"] != mode
        or type(record["index"]) is not int
        or record["index"] != index
    ):
        raise FixtureDenied("E_FIXTURE_V2_RESERVATION")
    if index == 1:
        if record["priorCompletionDigest"] is not None:
            raise FixtureDenied("E_FIXTURE_V2_RESERVATION")
    else:
        match(record["priorCompletionDigest"], DIGEST)
    if mode == "reset":
        retained = exact(record["retained"], {"successDigest", "liveDigest", "quiescenceDigest"})
        for value in retained.values():
            match(value, DIGEST)
    elif record["retained"] is not None:
        raise FixtureDenied("E_FIXTURE_V2_RESERVATION")
    verified = verify_oidc(
        token,
        jwks,
        expected={
            "bindingDigest": request["runBindingDigest"],
            "mode": mode,
            "index": index,
            "reservationDigest": request["reservationDigest"],
            "runId": run["id"],
            "clientSha": run["clientSha"],
            "controllerSha": pin[0],
        },
        now=now,
    )
    binding = replace(_backend_binding(b, index, now), manifest_digest=request["runBindingDigest"])
    retained = record["retained"] or {}
    proof = ExecutionProof(
        binding.manifest_digest,
        binding.source["currentSourceSha"],
        binding.source["currentSourceTree"],
        binding.authority["ownerGrantCommentId"],
        binding.authority["ownerGrantBodyDigest"],
        WORKFLOW,
        ENVIRONMENT,
        run["id"],
        1,
        mode,
        1 if mode != "unknown-proof" else index - (1 if index < 4 else 2),
        index,
        retained.get("quiescenceDigest"),
        retained.get("successDigest"),
    )
    operation = HostedOperation(
        binding,
        proof,
        b,
        record,
        request["reservationDigest"],
        verified["jtiDigest"],
        verified["expiresAt"],
    )
    return operation


def _backend_binding(b: dict[str, Any], index: int, now: datetime) -> Binding:
    if (
        not isinstance(b["manifestRaw"], str)
        or raw_digest(b["manifestRaw"]) != b["manifestRawDigest"]
    ):
        raise FixtureDenied("E_FIXTURE_V2_MANIFEST")
    m = exact(parse(b["manifestRaw"], 16384), {"schemaVersion", "state", "enabled", "binding"})
    if (
        m["schemaVersion"] != "agent-ops.gate745-fixture-runtime-manifest/v2"
        or m["state"] != "BOUND"
        or m["enabled"] is not True
    ):
        raise FixtureDenied("E_FIXTURE_V2_MANIFEST")
    v = exact(
        m["binding"],
        {"target", "source", "authority", "seed", "inventory", "caps", "cleanup", "configuration"},
    )
    t = exact(
        v["target"],
        {
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
            "repository",
            "healthUrl",
        },
    )
    s = exact(
        v["source"],
        {
            "reviewedHead",
            "reviewedTree",
            "deploySha",
            "deployTree",
            "baselineSha",
            "baselineTree",
            "reviewReceiptDigest",
            "reviewIssue",
            "reviewCommentId",
        },
    )
    a = exact(
        v["authority"],
        {
            "ownerId",
            "controlIssue",
            "workflowPath",
            "gateReviewIssue",
            "protectedEnvironment",
            "ownerGrantDigest",
            "ownerGrantCommentId",
            "createdAt",
            "expiresAt",
        },
    )
    seed = exact(
        v["seed"],
        {
            "baselineRevision",
            "recipeDigest",
            "databaseIdentityDigest",
            "unknownJournalDigest",
            "expectedUnknown",
            "helperSourceSha",
            "helperSourceTree",
            "helperManifestDigest",
            "proofRouteDigest",
            "setupRouteDigest",
        },
    )
    exact(v["caps"], set(CAPS))
    if any(type(v["caps"][k]) is not int or v["caps"][k] != value for k, value in CAPS.items()):
        raise FixtureDenied("E_FIXTURE_V2_CAPS")
    config = exact(v["configuration"], {"mechanism", "serviceCreatedAt", "phaseDigests"})
    if (
        config["mechanism"] != "NEW_SERVICE_SETTINGS"
        or instant(config["serviceCreatedAt"]) < datetime(2026, 10, 11, tzinfo=UTC)
        or instant(config["serviceCreatedAt"]) >= instant(b["expiresAt"])
        or config["phaseDigests"] != configuration_digests()
    ):
        raise FixtureDenied("E_FIXTURE_V2_CONFIGURATION")
    if (
        t["repository"] != "AlphaCenter/gate"
        or a["gateReviewIssue"] != 747
        or a["ownerGrantCommentId"] != b["ownerGrant"]["commentId"]
        or a["ownerGrantDigest"] != b["ownerGrant"]["bodyDigest"]
        or a["createdAt"] != b["createdAt"]
        or a["expiresAt"] != b["expiresAt"]
        or s["reviewedHead"] != s["deploySha"]
        or s["reviewedTree"] != s["deployTree"]
        or (seed["helperSourceSha"], seed["helperSourceTree"])
        != (b["helper"]["sha"], b["helper"]["tree"])
    ):
        raise FixtureDenied("E_FIXTURE_V2_SOURCE")
    for key in (
        "reviewedHead",
        "reviewedTree",
        "deploySha",
        "deployTree",
        "baselineSha",
        "baselineTree",
    ):
        match(s[key], SHA)
    if (
        s["baselineSha"] != "afe0c6b8519381a0007c0dcc1c145207b5d54a3f"
        or s["baselineTree"] == s["deployTree"]
        or seed["recipeDigest"] != RECIPE_DIGEST
        or seed["unknownJournalDigest"] != JOURNAL_DIGEST
        or seed["helperManifestDigest"] != source_scope_digest(v)
    ):
        raise FixtureDenied("E_FIXTURE_V2_SOURCE")
    cleanup = exact(v["cleanup"], {"operatorIdentity", "routeDigest", "deadline", "confirmed"})
    if (
        type(cleanup["operatorIdentity"]) is not int
        or cleanup["operatorIdentity"] != 23397294
        or cleanup["confirmed"] is not True
        or cleanup["deadline"] != b["expiresAt"]
    ):
        raise FixtureDenied("E_FIXTURE_V2_CLEANUP")
    match(cleanup["routeDigest"], DIGEST)
    inventory = v["inventory"]
    if type(inventory) is not list or len(inventory) != 2:
        raise FixtureDenied("E_FIXTURE_V2_INVENTORY")
    for i, row in enumerate(inventory):
        exact(
            row,
            {
                "role",
                "serviceId",
                "replicas",
                "configurationDigest",
                "sourceDigest",
                "imageDigest",
                "externalBindings",
            },
        )
        if (
            row["role"] != ("gate" if i == 0 else "pg")
            or row["serviceId"] != t["serviceId" if i == 0 else "postgresServiceId"]
            or type(row["replicas"]) is not int
            or row["replicas"] != 1
            or type(row["externalBindings"]) is not int
            or row["externalBindings"] != 0
        ):
            raise FixtureDenied("E_FIXTURE_V2_INVENTORY")
        for key in ("configurationDigest", "sourceDigest"):
            match(row[key], DIGEST)
        if i == 0:
            if row["imageDigest"] is not None:
                raise FixtureDenied("E_FIXTURE_V2_INVENTORY")
        else:
            match(row["imageDigest"], DIGEST)
    url = urlsplit(t["healthUrl"])
    if (
        url.scheme != "https"
        or url.path != "/admin/ops/reply-cutover"
        or url.query
        or url.fragment
        or url.username
        or url.password
        or url.port
        or not re.fullmatch(r"[a-z0-9-]+\.up\.railway\.app", url.hostname or "")
    ):
        raise FixtureDenied("E_FIXTURE_V2_TARGET")
    target = {
        key: t[key]
        for key in (
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    }
    if raw_digest(wire_json({**target, "repository": "AlphaCenter/gate"})) != b["targetDigest"]:
        raise FixtureDenied("E_FIXTURE_V2_TARGET")
    target.update(
        databaseHost="gate745-synthetic-postgres.railway.internal",
        databaseName="gate745_synthetic",
        databaseUser="gate745_synthetic",
        healthUrl="https://" + (url.hostname or "") + "/admin/ops/gate745-fixture-proof",
    )
    source_sha, source_tree = (
        (b["helper"]["sha"], b["helper"]["tree"])
        if index in (1, 4, 6)
        else (s["deploySha"], s["deployTree"])
    )
    projected = {
        "schemaVersion": "gate745.fixture-bootstrap-manifest/v1",
        "state": "BOUND",
        "enabled": True,
        "binding": {
            "target": target,
            "source": {
                "helperSourceSha": b["helper"]["sha"],
                "helperSourceTree": b["helper"]["tree"],
                "currentSourceSha": source_sha,
                "currentSourceTree": source_tree,
                "reviewIssue": s["reviewIssue"],
                "reviewCommentId": s["reviewCommentId"],
                "reviewBodyDigest": s["reviewReceiptDigest"],
                "setupRouteDigest": seed["setupRouteDigest"],
                "proofRouteDigest": seed["proofRouteDigest"],
            },
            "authority": {
                "ownerId": a["ownerId"],
                "controlIssue": a["controlIssue"],
                "workflowPath": a["workflowPath"],
                "protectedEnvironment": a["protectedEnvironment"],
                "ownerGrantCommentId": a["ownerGrantCommentId"],
                "ownerGrantBodyDigest": a["ownerGrantDigest"],
                "createdAt": a["createdAt"],
                "expiresAt": a["expiresAt"],
            },
            "seed": {
                k: seed[k]
                for k in (
                    "baselineRevision",
                    "recipeDigest",
                    "databaseIdentityDigest",
                    "unknownJournalDigest",
                    "expectedUnknown",
                )
            },
            "caps": {
                "helperDeploys": 2,
                "pgSetupDeploys": 1,
                "modeInvocations": {"bootstrap": 1, "seed": 1, "reset": 1, "unknown-proof": 4},
                "totalInvocations": 7,
            },
        },
    }
    # v1 pure structural checks are reused, never v1 effect authority or cap3.
    binding = admit_manifest(wire_json(projected).encode(), now=now)
    if index in (1, 4) and now >= instant(b["createdAt"]) + timedelta(minutes=100):
        raise FixtureDenied("E_FIXTURE_NEW_TEST_DEADLINE")
    return replace(binding, manifest_digest=raw_digest(wire_json(b)))


async def _load_jwks() -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", JWKS_URL) as response:
            if response.status_code != 200:
                raise FixtureDenied("E_FIXTURE_V2_OIDC_JWKS")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 32768:
                    raise FixtureDenied("E_FIXTURE_V2_OIDC_JWKS")
    return parse(bytes(chunks), 32768)


async def hosted_request(request: Any, *, execution_role: str = "GUARDED") -> dict[str, object]:
    async with asyncio.timeout(120):
        return await _hosted_request(request, execution_role=execution_role)


async def _hosted_request(request: Any, *, execution_role: str) -> dict[str, object]:
    pin = controller_pin()  # UNBOUND precedes env, body, network, credentials.
    if (
        request.url.query
        or request.method != "POST"
        or request.headers.get("content-type") != "application/json"
    ):
        raise FixtureDenied("E_FIXTURE_V2_REQUEST")
    oidc = request.headers.get("x-gate745-oidc", "")
    if not isinstance(oidc, str) or len(oidc) > 32768 or oidc.count(".") != 2:
        raise FixtureDenied("E_FIXTURE_V2_OIDC")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 65536:
            raise FixtureDenied("E_FIXTURE_V2_REQUEST")
    operation = admit_request(
        bytes(chunks),
        oidc,
        await _load_jwks(),
        pin=pin,
        now=datetime.now(UTC),
    )
    if (request.url.path == "/admin/ops/gate745-fixture-proof") != (
        operation.proof.mode == "unknown-proof"
    ) or request.url.path not in (
        "/admin/ops/gate745-fixture-proof",
        "/admin/ops/gate745-fixture-operation",
    ):
        raise FixtureDenied("E_FIXTURE_V2_REQUEST")
    expected_role = (
        "CONTROL"
        if operation.reservation["index"] in (1, 4)
        else "PROOF_ONLY"
        if operation.reservation["index"] == 6
        else "GUARDED"
    )
    if execution_role != expected_role:
        raise FixtureDenied("E_FIXTURE_V2_EXECUTION_ROLE")
    check_runtime(operation.binding, os.environ.get)
    token = os.environ.get("GATE745_FIXTURE_ADMIN_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    if (
        len(token) < 32
        or re.search(r"\s", token)
        or not authorization.startswith("Bearer ")
        or not compare_digest(authorization[7:], token)
    ):
        raise FixtureDenied("E_FIXTURE_ADMIN_AUTH")
    # Only this fixed pin/JWKS + path/role/runtime/admin adapter grants effects.
    # The public pure verifier accepts test keys for verification, never DB access.
    _ADMITTED.add(operation)
    from app.worker.gate745_fixture_ledger import execute_hosted

    return await execute_hosted(operation, execution_role=execution_role)


def metadata(*, standalone: bool) -> dict[str, str]:
    controller_pin()
    if not standalone:
        raise FixtureDenied("E_FIXTURE_V2_METADATA")
    for variable in ("RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID"):
        value = os.environ.get(variable)
        if value is None or value in PRODUCTION_IDS:
            raise FixtureDenied("E_FIXTURE_PRODUCTION")
    source = match(os.environ.get("RAILWAY_GIT_COMMIT_SHA"), SHA)
    return {
        "schemaVersion": "gate745.fixture-helper-metadata/v2",
        "helperPolicy": POLICY,
        "sourceSha": source,
    }


def create_app(*, proof_only: bool = False) -> Any:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "alive", "service": "gate745-fixture-helper"}

    @app.get("/admin/ops/gate745-fixture-metadata")
    async def read_metadata() -> JSONResponse:
        try:
            result = metadata(standalone=True)
        except Exception:
            return JSONResponse({"detail": "Not Found"}, 404, headers={"Cache-Control": "no-store"})
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def operation(request: Request) -> JSONResponse:
        try:
            result = await hosted_request(
                request, execution_role="PROOF_ONLY" if proof_only else "CONTROL"
            )
        except Exception:
            return JSONResponse({"detail": "Not Found"}, 404, headers={"Cache-Control": "no-store"})
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    if not proof_only:
        app.add_api_route("/admin/ops/gate745-fixture-operation", operation, methods=["POST"])
    app.add_api_route("/admin/ops/gate745-fixture-proof", operation, methods=["POST"])
    return app


def create_proof_app() -> Any:
    """A separate post-stop app has no bootstrap/seed/reset mutation route."""
    return create_app(proof_only=True)


app = create_app()


def main() -> None:
    import uvicorn

    # Fixed port matches reviewed serviceInstanceUpdate configuration. No CLI
    # mode/URL/config/provider/backend selectors exist.
    if sys.argv[1:] == ["proof-only"]:
        main_proof()
    elif sys.argv[1:]:
        raise SystemExit("Only the fixed proof-only startup choice is supported")
    else:
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning", access_log=False)


def main_proof() -> None:
    import uvicorn

    uvicorn.run(
        create_proof_app(), host="0.0.0.0", port=8000, log_level="warning", access_log=False
    )


if __name__ == "__main__":
    main()
