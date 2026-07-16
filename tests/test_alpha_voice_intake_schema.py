"""Schema guards for the dormant Alpha voice projection and queue."""

import importlib.util
from pathlib import Path

from app.models.alpha import AgentInstallationModel
from app.models.alpha_voice import AlphaVoiceJobModel, CanonicalVoiceMediaModel
from app.models.canonical import CanonicalMessage
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint


def _constraint_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints}


def _unique_column_sets(model: type[object]) -> set[tuple[str, ...]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_names(model: type[object]) -> set[str | None]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def test_voice_media_stores_only_bounded_metadata_and_hashed_unique_id() -> None:
    columns = set(CanonicalVoiceMediaModel.__table__.columns.keys())

    assert "provider_file_id" in columns
    assert "file_unique_id_sha256" in columns
    assert "file_unique_id" not in columns
    assert not columns.intersection(
        {"audio", "audio_bytes", "audio_blob", "file_path", "local_path"}
    )
    assert "ck_canonical_voice_media_unique_hash" in _constraint_names(
        CanonicalVoiceMediaModel
    )
    assert "ck_canonical_voice_media_size" in _constraint_names(
        CanonicalVoiceMediaModel
    )
    assert "ck_canonical_voice_media_duration" in _constraint_names(
        CanonicalVoiceMediaModel
    )


def test_voice_jobs_are_workspace_dialog_installation_message_and_media_bound() -> None:
    foreign_keys = _foreign_key_names(AlphaVoiceJobModel)

    assert {
        "fk_alpha_voice_jobs_workspace",
        "fk_alpha_voice_jobs_dialog_workspace",
        "fk_alpha_voice_jobs_installation_dialog_workspace",
        "fk_alpha_voice_jobs_message_dialog_workspace",
        "fk_alpha_voice_jobs_media_message_scope",
    }.issubset(foreign_keys)
    assert (
        "workspace_id",
        "canonical_message_id",
        "operation",
    ) in _unique_column_sets(AlphaVoiceJobModel)
    assert (
        "id",
        "dialog_id",
        "workspace_id",
    ) in _unique_column_sets(AgentInstallationModel)
    assert (
        "id",
        "dialog_id",
        "workspace_id",
    ) in _unique_column_sets(CanonicalMessage)


def test_voice_job_state_machine_is_constrained_and_has_no_delivery_target() -> None:
    columns = set(AlphaVoiceJobModel.__table__.columns.keys())

    assert "ck_alpha_voice_jobs_operation" in _constraint_names(AlphaVoiceJobModel)
    assert "ck_alpha_voice_jobs_state_payload" in _constraint_names(AlphaVoiceJobModel)
    assert "ck_alpha_voice_jobs_version" in _constraint_names(AlphaVoiceJobModel)
    assert not columns.intersection(
        {
            "recipient_id",
            "provider_dialog_id",
            "telegram_chat_id",
            "send_attempt_id",
            "audio_bytes",
            "file_path",
        }
    )


def test_voice_migration_follows_current_head_and_is_reversible() -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0027_alpha_voice_intake.py"
    )
    spec = importlib.util.spec_from_file_location("alpha_voice_intake_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0027_alpha_voice_intake"
    assert module.down_revision == "0026_alpha_model_preflight"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_alembic_metadata_registers_voice_tables_for_schema_drift_checks() -> None:
    env_source = (
        Path(__file__).parents[1] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")

    assert "alpha_voice" in env_source


def test_voice_runtime_remains_dormant_by_default() -> None:
    main_source = (Path(__file__).parents[1] / "app" / "main.py").read_text(
        encoding="utf-8"
    )
    repository_source = (
        Path(__file__).parents[1]
        / "app"
        / "infrastructure"
        / "alpha_voice_intake.py"
    ).read_text(encoding="utf-8")

    assert "alpha_voice" not in main_source
    assert "import httpx" not in repository_source
    assert "import openai" not in repository_source.casefold()
    assert ".download(" not in repository_source
    assert "send_message(" not in repository_source
