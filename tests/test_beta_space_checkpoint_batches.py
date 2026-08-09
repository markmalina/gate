from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from app.infrastructure import beta_checkpoint_export
from app.infrastructure import beta_space_checkpoint_batches as checkpoint_batches
from app.infrastructure.beta_space_checkpoint_batches import (
    _MATERIAL_TRUNCATION_MARKER,
    BetaSpaceCheckpointBatchError,
    _portable_wedding_event,
    acknowledge_beta_space_checkpoint_batch,
    next_beta_space_checkpoint_batch,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

GROUP_ID = "beta-group-wedding-minsk-001"


def anyio_backend() -> str:
    return "asyncio"


def _canonical_event(sequence: int, *, text: str = "synthetic") -> dict[str, Any]:
    return {
        "schema_version": "beta.context-event@1",
        "event_id": f"beta-event-{sequence:032x}",
        "group_id": GROUP_ID,
        "sequence": sequence,
        "kind": "message.inbound",
        "occurred_at": "2026-08-08T20:00:00.000Z",
        "recorded_at": "2026-08-08T20:00:01.000Z",
        "actor_ref": "actor-0000000000000000",
        "content": {
            "format": "structured_text",
            "text": text,
            "language": "ru",
        },
        "provenance": {
            "source": "telegram",
            "source_event_ref": f"gate-event-{sequence:064x}",
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "idempotency_key": f"gate.context.{sequence:032x}",
        },
        "supersedes": None,
        "tombstones": None,
    }


class _AllResult:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _CheckpointSession:
    def __init__(self, *, binding: Any, rows: list[Any]) -> None:
        self.binding = binding
        self._scalar_values = [binding, len(rows)]
        self._rows = rows
        self.added: list[Any] = []
        self.flushed = False

    async def scalar(self, _statement: Any) -> Any:
        return self._scalar_values.pop(0)

    async def scalars(self, _statement: Any) -> _AllResult:
        return _AllResult([])

    async def execute(self, _statement: Any) -> _AllResult:
        return _AllResult(self._rows)

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed = True


class _MissingPendingSession:
    def __init__(self, binding: Any) -> None:
        self._scalar_values = [binding, None]
        self.flushed = False

    async def scalar(self, _statement: Any) -> Any:
        return self._scalar_values.pop(0)

    async def flush(self) -> None:
        self.flushed = True


class _AckSession:
    def __init__(self, binding: Any, batch: Any, accepted_sequence: int) -> None:
        self._scalar_values = [binding, batch, accepted_sequence]
        self.flushed = False

    async def scalar(self, _statement: Any) -> Any:
        return self._scalar_values.pop(0)

    async def flush(self) -> None:
        self.flushed = True


def _binding(*, recipient_pem: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        tenant_ref="workspace-beta-001",
        space_ref="spc_fcdd8e57a7e832840afcc187fb1d4b2c",
        storage_ref=GROUP_ID,
        checkpoint_last_exported_sequence=0,
        checkpoint_recipient_pem=recipient_pem,
        updated_at=None,
    )


class _ArtifactVerifier:
    def __init__(self, *, error_code: str | None = None) -> None:
        self.error_code = error_code
        self.calls: list[dict[str, Any]] = []

    async def verify(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.error_code is not None:
            raise SimpleNamespaceError(self.error_code)


class SimpleNamespaceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _rows(count: int) -> list[tuple[SimpleNamespace, SimpleNamespace, None, None]]:
    return [
        (
            SimpleNamespace(
                checkpoint_sequence=sequence,
                media_kind="text",
            ),
            SimpleNamespace(),
            None,
            None,
        )
        for sequence in range(1, count + 1)
    ]


@pytest.mark.anyio
async def test_ack_advances_only_after_exact_artifact_verification() -> None:
    binding = _binding(recipient_pem="synthetic-public-key")
    batch = SimpleNamespace(
        group_id=GROUP_ID,
        sequence_start=1,
        sequence_end=4,
        ciphertext_sha256="c" * 64,
        state="pending",
        github_commit_sha=None,
        acknowledged_at=None,
    )
    verifier = _ArtifactVerifier()
    session = _AckSession(binding, batch, accepted_sequence=4)

    result = await acknowledge_beta_space_checkpoint_batch(
        session,  # type: ignore[arg-type]
        installation_ref=binding.installation_ref,
        expected_user_workspace_id=binding.tenant_ref,
        expected_group_id=binding.storage_ref,
        sequence_start=1,
        sequence_end=4,
        ciphertext_sha256="c" * 64,
        github_commit_sha="d" * 40,
        artifact_verifier=verifier,
    )

    assert verifier.calls == [
        {
            "space_id": binding.space_ref,
            "sequence_start": 1,
            "sequence_end": 4,
            "ciphertext_sha256": "c" * 64,
            "github_commit_sha": "d" * 40,
        }
    ]
    assert batch.state == "acknowledged"
    assert binding.checkpoint_last_exported_sequence == 4
    assert session.flushed is True
    assert result["last_exported_sequence"] == 4


@pytest.mark.anyio
async def test_false_artifact_proof_never_advances_cursor() -> None:
    binding = _binding(recipient_pem="synthetic-public-key")
    batch = SimpleNamespace(
        group_id=GROUP_ID,
        sequence_start=1,
        sequence_end=1,
        ciphertext_sha256="c" * 64,
        state="pending",
        github_commit_sha=None,
        acknowledged_at=None,
    )
    verifier = _ArtifactVerifier(
        error_code="beta_checkpoint_artifact_hash_mismatch"
    )
    session = _AckSession(binding, batch, accepted_sequence=1)

    with pytest.raises(BetaSpaceCheckpointBatchError) as caught:
        await acknowledge_beta_space_checkpoint_batch(
            session,  # type: ignore[arg-type]
            installation_ref=binding.installation_ref,
            expected_user_workspace_id=binding.tenant_ref,
            expected_group_id=binding.storage_ref,
            sequence_start=1,
            sequence_end=1,
            ciphertext_sha256="c" * 64,
            github_commit_sha="d" * 40,
            artifact_verifier=verifier,
        )

    assert caught.value.code == "beta_checkpoint_artifact_hash_mismatch"
    assert batch.state == "pending"
    assert batch.github_commit_sha is None
    assert binding.checkpoint_last_exported_sequence == 0
    assert session.flushed is False


@pytest.mark.anyio
async def test_checkpoint_stops_before_nonterminal_material_without_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = X25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    binding = _binding(recipient_pem=public_pem)
    rows = [
        (
            SimpleNamespace(checkpoint_sequence=1, media_kind="text"),
            SimpleNamespace(),
            None,
            None,
        ),
        (
            SimpleNamespace(checkpoint_sequence=2, media_kind="document"),
            SimpleNamespace(),
            None,
            SimpleNamespace(state="processing"),
        ),
    ]
    events = [_canonical_event(1, text="готово"), _canonical_event(2)]
    session = _CheckpointSession(binding=binding, rows=rows)
    _install_projection(monkeypatch, events)

    result = await next_beta_space_checkpoint_batch(
        session,  # type: ignore[arg-type]
        installation_ref=binding.installation_ref,
        expected_user_workspace_id=binding.tenant_ref,
        expected_group_id=binding.storage_ref,
    )

    assert result is not None
    assert result["sequence_start"] == 1
    assert result["sequence_end"] == 1
    assert result["event_count"] == 1
    assert result["accepted_sequence"] == 2
    assert result["checkpoint_lag_events"] == 1


@pytest.mark.anyio
async def test_checkpoint_uses_largest_canonical_byte_bounded_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = X25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    events = [
        _canonical_event(sequence, text="🙂" * 65_536)
        for sequence in range(1, 11)
    ]
    session = _CheckpointSession(
        binding=_binding(recipient_pem=public_pem),
        rows=_rows(len(events)),
    )
    _install_projection(monkeypatch, events)

    result = await next_beta_space_checkpoint_batch(
        session,  # type: ignore[arg-type]
        installation_ref=session.binding.installation_ref,
        expected_user_workspace_id=session.binding.tenant_ref,
        expected_group_id=session.binding.storage_ref,
    )

    assert result is not None
    assert 1 <= result["event_count"] < len(events)
    assert result["sequence_start"] == 1
    assert result["sequence_end"] == result["event_count"]
    assert result["checkpoint_lag_events"] == len(events) - result["event_count"]
    assert len(session.added) == 1


def test_byte_bounded_prefix_rejects_oversized_singleton_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _canonical_event(1)
    monkeypatch.setattr(
        beta_checkpoint_export,
        "MAX_PLAINTEXT_BYTES",
        1,
    )

    with pytest.raises(beta_checkpoint_export.BetaCheckpointExportError) as caught:
        beta_checkpoint_export._byte_bounded_event_prefix(
            group_id=GROUP_ID,
            events=[event],
        )

    assert caught.value.code == "beta_checkpoint_event_oversized"


def _install_projection(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
) -> None:
    def project(*, event: Any, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(events[event.checkpoint_sequence - 1])

    monkeypatch.setattr(checkpoint_batches, "_portable_wedding_event", project)


def test_wedding_checkpoint_projection_keeps_text_and_redacts_transport_ids() -> None:
    binding = SimpleNamespace(
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        storage_ref="beta-group-wedding-minsk-001",
    )
    event = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000123"),
        checkpoint_sequence=7,
        media_kind="text",
        telegram_user_id="99887766",
        occurred_at=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        created_at=datetime(2026, 8, 7, 20, 0, 1, tzinfo=UTC),
    )
    receipt = SimpleNamespace(
        payload={
            "message": {
                "text": "Согласовали площадку на сорок гостей.",
                "chat": {"id": -100123456789},
                "from": {"id": 99887766},
            }
        }
    )

    projected = _portable_wedding_event(
        binding=binding,
        event=event,
        receipt=receipt,
        voice=None,
    )

    encoded = json.dumps(projected, ensure_ascii=False)
    assert projected["sequence"] == 7
    assert projected["content"]["text"] == "Согласовали площадку на сорок гостей."
    assert "99887766" not in encoded
    assert "-100123456789" not in encoded
    assert projected["actor_ref"].startswith("actor-")
    assert projected["provenance"]["source_event_ref"].startswith("gate-event-")


def test_wedding_voice_checkpoint_contains_transcript_and_structure_only() -> None:
    binding = SimpleNamespace(
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        storage_ref="beta-group-wedding-minsk-001",
    )
    event = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000124"),
        checkpoint_sequence=8,
        media_kind="voice",
        telegram_user_id="11223344",
        occurred_at=datetime(2026, 8, 7, 20, 1, tzinfo=UTC),
        created_at=datetime(2026, 8, 7, 20, 1, 1, tzinfo=UTC),
    )
    receipt = SimpleNamespace(
        payload={
            "message": {
                "voice": {"file_id": "private-provider-file-id"},
                "chat": {"id": -100123456789},
            }
        }
    )
    voice = SimpleNamespace(
        transcript_text="Нужно подготовить список гостей.",
        structured_text="Задачи: подготовить список гостей.",
    )

    projected = _portable_wedding_event(
        binding=binding,
        event=event,
        receipt=receipt,
        voice=voice,
    )

    encoded = json.dumps(projected, ensure_ascii=False)
    assert "Нужно подготовить список гостей." in projected["content"]["text"]
    assert "Задачи: подготовить список гостей." in projected["content"]["text"]
    assert "private-provider-file-id" not in encoded
    assert "-100123456789" not in encoded


def test_material_projection_truncates_extraction_and_preserves_structure() -> None:
    binding = SimpleNamespace(
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        storage_ref=GROUP_ID,
    )
    event = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000125"),
        checkpoint_sequence=9,
        media_kind="document",
        telegram_user_id="44556677",
        occurred_at=datetime(2026, 8, 7, 20, 2, tzinfo=UTC),
        created_at=datetime(2026, 8, 7, 20, 2, 1, tzinfo=UTC),
    )
    receipt = SimpleNamespace(payload={"message": {"document": {}}})
    structured = "Решение: сохранить структурированный результат полностью."
    material = SimpleNamespace(
        state="structured",
        media_kind="document",
        extracted_text="Д" * 100_000,
        structured_text=structured,
    )

    first = _portable_wedding_event(
        binding=binding,
        event=event,
        receipt=receipt,
        voice=None,
        material=material,
    )
    second = _portable_wedding_event(
        binding=binding,
        event=event,
        receipt=receipt,
        voice=None,
        material=material,
    )

    text_value = first["content"]["text"]
    assert isinstance(text_value, str)
    assert first == second
    assert len(text_value) == 65_536
    assert _MATERIAL_TRUNCATION_MARKER in text_value
    assert text_value.endswith(f"Структура Беты:\n{structured}")
    assert first["provenance"]["content_sha256"] == hashlib.sha256(
        text_value.encode()
    ).hexdigest()


@pytest.mark.anyio
async def test_65_536_code_points_validate_before_pending_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = X25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    events = [_canonical_event(1, text="🙂" * 65_536)]
    session = _CheckpointSession(
        binding=_binding(recipient_pem=public_pem),
        rows=_rows(1),
    )
    _install_projection(monkeypatch, events)

    result = await next_beta_space_checkpoint_batch(
        session,  # type: ignore[arg-type]
        installation_ref="installation-4de1df8a77f8468d8b30e8a41db876aa",
        expected_user_workspace_id="workspace-beta-001",
        expected_group_id=GROUP_ID,
    )

    assert result is not None
    assert result["event_count"] == 1
    assert len(session.added) == 1
    assert session.flushed is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        "content_65_537",
        "event_count_51",
        "schema_version",
        "event_id",
        "group_id",
        "sequence_zero",
        "sequence_too_large",
        "sequence_bool",
        "kind",
        "unhashable_kind",
        "occurred_at",
        "recorded_at",
        "actor_ref",
        "content_format",
        "language_33",
        "source",
        "source_event_ref",
        "source_event_ref_129",
        "idempotency_key",
        "extra_event_field",
        "extra_content_field",
        "extra_provenance_field",
        "missing_event_field",
        "missing_content_field",
        "missing_provenance_field",
        "content_hash",
        "invalid_target_shape",
        "created_at",
    ],
)
async def test_invalid_v1_event_fails_before_pending_insert(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    private = X25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    crypto_started = False

    def unexpected_private_key_generation() -> None:
        nonlocal crypto_started
        crypto_started = True
        raise AssertionError("invalid checkpoint reached encryption")

    monkeypatch.setattr(
        beta_checkpoint_export,
        "X25519PrivateKey",
        SimpleNamespace(generate=unexpected_private_key_generation),
    )
    count = 51 if case == "event_count_51" else 1
    events = [_canonical_event(sequence) for sequence in range(1, count + 1)]
    if case == "content_65_537":
        events[0] = _canonical_event(1, text="🙂" * 65_537)
    elif case == "schema_version":
        events[0]["schema_version"] = "beta.context-event@2"
    elif case == "event_id":
        events[0]["event_id"] = "event-invalid"
    elif case == "group_id":
        events[0]["group_id"] = "wedding-minsk"
    elif case == "sequence_zero":
        events[0]["sequence"] = 0
    elif case == "sequence_too_large":
        events[0]["sequence"] = 1_000_000_000_000
    elif case == "sequence_bool":
        events[0]["sequence"] = True
    elif case == "kind":
        events[0]["kind"] = "message.unknown"
    elif case == "unhashable_kind":
        events[0]["kind"] = []
    elif case == "occurred_at":
        events[0]["occurred_at"] = "2026-08-08T20:00:00Z"
    elif case == "recorded_at":
        events[0]["recorded_at"] = "2026-02-30T20:00:01.000Z"
    elif case == "actor_ref":
        events[0]["actor_ref"] = "actor-invalid"
    elif case == "content_format":
        events[0]["content"]["format"] = "markdown"
    elif case == "language_33":
        events[0]["content"]["language"] = "я" * 33
    elif case == "source":
        events[0]["provenance"]["source"] = "github"
    elif case == "source_event_ref":
        events[0]["provenance"]["source_event_ref"] = "short"
    elif case == "source_event_ref_129":
        events[0]["provenance"]["source_event_ref"] = "s" * 129
    elif case == "idempotency_key":
        events[0]["provenance"]["idempotency_key"] = "invalid key"
    elif case == "extra_event_field":
        events[0]["unexpected"] = True
    elif case == "extra_content_field":
        events[0]["content"]["unexpected"] = True
    elif case == "extra_provenance_field":
        events[0]["provenance"]["unexpected"] = True
    elif case == "missing_event_field":
        del events[0]["actor_ref"]
    elif case == "missing_content_field":
        del events[0]["content"]["language"]
    elif case == "missing_provenance_field":
        del events[0]["provenance"]["source"]
    elif case == "content_hash":
        events[0]["provenance"]["content_sha256"] = "0" * 64
    elif case == "invalid_target_shape":
        events[0]["supersedes"] = (
            "beta-event-00000000000000000000000000000002"
        )
    elif case == "created_at":
        monkeypatch.setattr(
            checkpoint_batches,
            "_iso_datetime",
            lambda _value: "2026-08-08T20:00:00Z",
        )

    session = _CheckpointSession(
        binding=_binding(recipient_pem=public_pem),
        rows=_rows(count),
    )
    _install_projection(monkeypatch, events)

    with pytest.raises(BetaSpaceCheckpointBatchError) as caught:
        await next_beta_space_checkpoint_batch(
            session,  # type: ignore[arg-type]
            installation_ref=(
                "installation-4de1df8a77f8468d8b30e8a41db876aa"
            ),
            expected_user_workspace_id="workspace-beta-001",
            expected_group_id=GROUP_ID,
        )

    assert caught.value.code == (
        "beta_checkpoint_event_limit_exceeded"
        if case == "event_count_51"
        else (
            "beta_checkpoint_timestamp_invalid"
            if case == "created_at"
            else (
                "beta_checkpoint_context_not_contiguous"
                if case
                in {"sequence_zero", "sequence_too_large", "sequence_bool"}
                else "beta_checkpoint_event_invalid"
            )
        )
    )
    assert str(caught.value) == caught.value.code
    assert crypto_started is False
    assert session.added == []
    assert session.flushed is False
    assert session.binding.checkpoint_last_exported_sequence == 0


@pytest.mark.anyio
async def test_invalid_projection_cannot_enter_ack_cursor_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(recipient_pem="not-a-public-key")
    events = [_canonical_event(1)]
    events[0]["provenance"]["content_sha256"] = "0" * 64
    next_session = _CheckpointSession(binding=binding, rows=_rows(1))
    _install_projection(monkeypatch, events)

    with pytest.raises(BetaSpaceCheckpointBatchError):
        await next_beta_space_checkpoint_batch(
            next_session,  # type: ignore[arg-type]
            installation_ref=binding.installation_ref,
            expected_user_workspace_id=binding.tenant_ref,
            expected_group_id=binding.storage_ref,
        )

    ack_session = _MissingPendingSession(binding)
    with pytest.raises(BetaSpaceCheckpointBatchError) as caught:
        await acknowledge_beta_space_checkpoint_batch(
            ack_session,  # type: ignore[arg-type]
            installation_ref=binding.installation_ref,
            expected_user_workspace_id=binding.tenant_ref,
            expected_group_id=binding.storage_ref,
            sequence_start=1,
            sequence_end=1,
            ciphertext_sha256="a" * 64,
            github_commit_sha="b" * 40,
        )

    assert caught.value.code == "beta_checkpoint_pending_missing"
    assert binding.checkpoint_last_exported_sequence == 0
    assert ack_session.flushed is False
