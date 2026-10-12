"""Pure, default-off synthetic fixture admission; shape checks are not authority."""

import hashlib
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from app.worker.gate745_fixture_manifest import MANIFEST_JSON

Mode = Literal["bootstrap", "seed", "reset", "unknown-proof"]
BASELINE = "0115_watch_voice_post_payment"
GUARDED = "0116_reply_dialog_versions"
WORKFLOW = ".github/workflows/gate745-native-fixture.yml"
ENVIRONMENT = "gate745-isolated-fixture"
PROOF_PATH = "/admin/ops/gate745-fixture-proof"
PRODUCTION_HOST = "gate-production-bab1.up.railway.app"
PRODUCTION_IDS = frozenset(
    {
        "972b6ab7-988a-421c-abfa-425451ad714f",
        "a148b138-44b3-4349-9d4b-292880503055",
        "099cbf8c-1d94-43fe-8714-90725bf93998",
        "561d3da3-ddb5-4d3d-8fa2-c9454b0996bf",
        "7d2de9ed-c63e-454a-811e-4f206f3dfef3",
        "0e16e0f3-ec16-4e47-992a-4c7e14b77d51",
        "b9867b43-80de-40da-b8cd-66c8fc73e523",
        "124e1f44-8c5b-46c0-b382-55becb9e6ddb",
        "35df1378-ec07-49c4-8fd4-8213fd921eb7",
        "fc623faa-455e-4703-897f-4cc215307abc",
    }
)
MODE_CAPS = {"bootstrap": 1, "seed": 1, "reset": 1, "unknown-proof": 4}
RUNTIME_KEYS = {
    "projectId": "RAILWAY_PROJECT_ID",
    "environmentId": "RAILWAY_ENVIRONMENT_ID",
    "serviceId": "RAILWAY_SERVICE_ID",
    "currentSourceSha": "RAILWAY_GIT_COMMIT_SHA",
}
SHA = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z")
POSITIVE_ID = re.compile(r"[1-9][0-9]{0,19}\Z")


class FixtureDenied(Exception):
    """Only a public bounded error code may leave the helper."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def digest(value: object) -> str:
    """Canonical JSON fingerprint; never hashes credential values."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def exact(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise FixtureDenied("E_FIXTURE_SHAPE")
    return value


def match(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise FixtureDenied("E_FIXTURE_BINDING")
    return value


def instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise FixtureDenied("E_FIXTURE_TIME")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise FixtureDenied("E_FIXTURE_TIME") from None
    if parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != value:
        raise FixtureDenied("E_FIXTURE_TIME")
    return parsed


@dataclass(frozen=True)
class Binding:
    """Validated static policy only, not an authenticated effect grant."""

    target: Mapping[str, Any]
    source: Mapping[str, Any]
    authority: Mapping[str, Any]
    seed: Mapping[str, Any]
    manifest_digest: str


def admit_manifest(raw: bytes, *, now: datetime) -> Binding:
    if len(raw) > 16384:
        raise FixtureDenied("E_FIXTURE_MANIFEST")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise FixtureDenied("E_FIXTURE_MANIFEST") from None
    m = exact(document, {"schemaVersion", "state", "enabled", "binding"})
    if m["schemaVersion"] != "gate745.fixture-bootstrap-manifest/v1":
        raise FixtureDenied("E_FIXTURE_MANIFEST")
    if m["state"] == "UNBOUND":
        raise FixtureDenied("E_FIXTURE_UNBOUND")
    if m["state"] != "BOUND" or m["enabled"] is not True:
        raise FixtureDenied("E_FIXTURE_DISABLED")
    b = exact(m["binding"], {"target", "source", "authority", "seed", "caps"})
    t = exact(
        b["target"],
        {
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
            "databaseHost",
            "databaseName",
            "databaseUser",
            "healthUrl",
        },
    )
    ids = []
    for key in ("projectId", "environmentId", "serviceId", "postgresServiceId", "volumeInstanceId"):
        value = match(t[key], UUID)
        if value in PRODUCTION_IDS:
            raise FixtureDenied("E_FIXTURE_PRODUCTION")
        ids.append(value)
    if len(set(ids)) != 5:
        raise FixtureDenied("E_FIXTURE_TARGET")
    for key in ("databaseName", "databaseUser"):
        if t[key] != "gate745_synthetic":
            raise FixtureDenied("E_FIXTURE_DATABASE")
    if t["databaseHost"] != "gate745-synthetic-postgres.railway.internal":
        raise FixtureDenied("E_FIXTURE_DATABASE")
    if not isinstance(t["healthUrl"], str):
        raise FixtureDenied("E_FIXTURE_TARGET")
    try:
        url = urlsplit(t["healthUrl"])
        valid_url = (
            url.scheme == "https"
            and url.path == PROOF_PATH
            and not url.query
            and not url.fragment
            and not url.username
            and not url.password
            and url.port is None
            and url.hostname != PRODUCTION_HOST
            and bool(re.fullmatch(r"[a-z0-9-]+\.up\.railway\.app", url.hostname or ""))
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise FixtureDenied("E_FIXTURE_PRODUCTION_OR_URL")
    s = exact(
        b["source"],
        {
            "helperSourceSha",
            "helperSourceTree",
            "currentSourceSha",
            "currentSourceTree",
            "reviewIssue",
            "reviewCommentId",
            "reviewBodyDigest",
            "setupRouteDigest",
            "proofRouteDigest",
        },
    )
    for key in ("helperSourceSha", "helperSourceTree", "currentSourceSha", "currentSourceTree"):
        match(s[key], SHA)
    for key in ("reviewBodyDigest", "setupRouteDigest", "proofRouteDigest"):
        match(s[key], DIGEST)
    match(s["reviewCommentId"], POSITIVE_ID)
    if type(s["reviewIssue"]) is not int or s["reviewIssue"] <= 0:
        raise FixtureDenied("E_FIXTURE_SOURCE")
    a = exact(
        b["authority"],
        {
            "ownerId",
            "controlIssue",
            "workflowPath",
            "protectedEnvironment",
            "ownerGrantCommentId",
            "ownerGrantBodyDigest",
            "createdAt",
            "expiresAt",
        },
    )
    if (
        type(a["ownerId"]) is not int
        or a["ownerId"] != 23397294
        or type(a["controlIssue"]) is not int
        or a["controlIssue"] != 745
        or a["workflowPath"] != WORKFLOW
        or a["protectedEnvironment"] != ENVIRONMENT
    ):
        raise FixtureDenied("E_FIXTURE_AUTHORITY")
    match(a["ownerGrantCommentId"], POSITIVE_ID)
    match(a["ownerGrantBodyDigest"], DIGEST)
    start, end = instant(a["createdAt"]), instant(a["expiresAt"])
    if (
        now.tzinfo is None
        or not start <= now < end
        or not timedelta(0) < end - start <= timedelta(hours=2)
    ):
        raise FixtureDenied("E_FIXTURE_EXPIRED")
    seed = exact(
        b["seed"],
        {
            "baselineRevision",
            "recipeDigest",
            "databaseIdentityDigest",
            "unknownJournalDigest",
            "expectedUnknown",
        },
    )
    if (
        seed["baselineRevision"] != BASELINE
        or type(seed["expectedUnknown"]) is not int
        or seed["expectedUnknown"] != 1
    ):
        raise FixtureDenied("E_FIXTURE_SEED")
    for key in ("recipeDigest", "databaseIdentityDigest", "unknownJournalDigest"):
        match(seed[key], DIGEST)
    caps = exact(
        b["caps"], {"helperDeploys", "pgSetupDeploys", "modeInvocations", "totalInvocations"}
    )
    if (
        type(caps["helperDeploys"]) is not int
        or type(caps["pgSetupDeploys"]) is not int
        or type(caps["totalInvocations"]) is not int
        or caps["helperDeploys"] != 2
        or caps["pgSetupDeploys"] != 1
        or caps["modeInvocations"] != MODE_CAPS
        or caps["totalInvocations"] != 7
    ):
        raise FixtureDenied("E_FIXTURE_CAPS")
    exact(caps["modeInvocations"], set(MODE_CAPS))
    if any(type(value) is not int for value in caps["modeInvocations"].values()):
        raise FixtureDenied("E_FIXTURE_CAPS")
    return Binding(
        MappingProxyType(t),
        MappingProxyType(s),
        MappingProxyType(a),
        MappingProxyType(seed),
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )


def load_binding(*, now: datetime) -> Binding:
    """Only this checked-in byte string is used by hosted/CLI entry points."""
    return admit_manifest(MANIFEST_JSON, now=now)


def check_runtime(binding: Binding, read_control: Callable[[str], str | None]) -> None:
    for key, variable in RUNTIME_KEYS.items():
        expected = binding.source[key] if key == "currentSourceSha" else binding.target[key]
        if read_control(variable) != expected:
            raise FixtureDenied("E_FIXTURE_RUNTIME")


@dataclass(frozen=True)
class ExecutionProof:
    """Returned only by an independently authenticated, future fixed executor."""

    binding_digest: str
    source_sha: str
    source_tree: str
    owner_grant_comment_id: str
    owner_grant_body_digest: str
    workflow_path: str
    protected_environment: str
    run_id: str
    run_attempt: int
    mode: Mode
    invocation: int
    total_invocations: int
    quiescence_receipt_digest: str | None = None
    retained_case_receipt_digest: str | None = None
    reset_grant_comment_id: str | None = None
    reset_grant_body_digest: str | None = None


class TrustedFixtureExecutor(ABC):
    """Missing hosted capability, intentionally without a production implementation.

    A future separately reviewed adapter must authenticate GitHub source/review,
    immutable owner grant, current run, exact physical inventory, quiescence,
    retained first-case artifacts and fresh reset grant, and enforce global caps.
    Environment strings, request headers, boolean ACCEPT or digest shape alone
    cannot implement this interface or grant effects.
    """

    @abstractmethod
    async def authenticate(self, mode: Mode, binding: Binding) -> ExecutionProof: ...

    @abstractmethod
    def database_url(self) -> str: ...

    @abstractmethod
    def fixture_admin_token(self) -> str: ...


def check_execution(binding: Binding, proof: ExecutionProof, mode: Mode, *, now: datetime) -> None:
    if mode not in MODE_CAPS:
        raise FixtureDenied("E_FIXTURE_MODE")
    if (
        proof.binding_digest != binding.manifest_digest
        or proof.source_sha != binding.source["currentSourceSha"]
        or proof.source_tree != binding.source["currentSourceTree"]
        or proof.owner_grant_comment_id != binding.authority["ownerGrantCommentId"]
        or proof.owner_grant_body_digest != binding.authority["ownerGrantBodyDigest"]
        or proof.workflow_path != WORKFLOW
        or proof.protected_environment != ENVIRONMENT
        or proof.mode != mode
        or type(proof.run_attempt) is not int
        or proof.run_attempt != 1
    ):
        raise FixtureDenied("E_FIXTURE_EXECUTOR_PROOF")
    match(proof.run_id, POSITIVE_ID)
    if (
        type(proof.invocation) is not int
        or not 1 <= proof.invocation <= MODE_CAPS[mode]
        or type(proof.total_invocations) is not int
        or not 1 <= proof.total_invocations <= 7
    ):
        raise FixtureDenied("E_FIXTURE_INVOCATION_CAP")
    if now >= instant(binding.authority["expiresAt"]):
        raise FixtureDenied("E_FIXTURE_EXPIRED")
    if mode != "unknown-proof" and now >= instant(binding.authority["createdAt"]) + timedelta(
        minutes=100
    ):
        raise FixtureDenied("E_FIXTURE_NEW_TEST_DEADLINE")
    reset_fields = (
        proof.quiescence_receipt_digest,
        proof.retained_case_receipt_digest,
        proof.reset_grant_comment_id,
        proof.reset_grant_body_digest,
    )
    if mode == "reset":
        match(proof.quiescence_receipt_digest, DIGEST)
        match(proof.retained_case_receipt_digest, DIGEST)
        match(proof.reset_grant_body_digest, DIGEST)
        match(proof.reset_grant_comment_id, POSITIVE_ID)
        if proof.reset_grant_comment_id == proof.owner_grant_comment_id:
            raise FixtureDenied("E_FIXTURE_FRESH_RESET_GRANT")
    elif any(value is not None for value in reset_fields):
        raise FixtureDenied("E_FIXTURE_EXECUTOR_PROOF")


async def authorize(
    mode: Mode,
    binding: Binding,
    executor: object,
    *,
    now: datetime,
) -> tuple[TrustedFixtureExecutor, ExecutionProof]:
    if not isinstance(executor, TrustedFixtureExecutor):
        raise FixtureDenied("E_FIXTURE_TRUSTED_ROUTE_MISSING")
    proof = await executor.authenticate(mode, binding)
    if not isinstance(proof, ExecutionProof):
        raise FixtureDenied("E_FIXTURE_EXECUTOR_PROOF")
    check_execution(binding, proof, mode, now=now)
    return executor, proof
