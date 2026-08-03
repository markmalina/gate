"""PostgreSQL repository helpers for Alfa transcription jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.alpha_transcription_product import CreateAlphaTranscriptionCommand
from app.models.alpha_transcription import (
    AlphaTranscriptionJobModel,
    AlphaTranscriptionPartModel,
)
from app.models.canonical import Workspace


async def create_transcription_job(
    session: AsyncSession,
    command: CreateAlphaTranscriptionCommand,
    *,
    source_sha256: str | None = None,
    source_size_bytes: int | None = None,
) -> tuple[AlphaTranscriptionJobModel, bool]:
    workspace_uuid = uuid.UUID(command.workspace_id)
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_uuid,
            Workspace.is_active.is_(True),
        )
    )
    if workspace is None:
        raise ValueError("alpha_transcription_workspace_unavailable")
    if source_sha256 is not None:
        existing = await session.scalar(
            select(AlphaTranscriptionJobModel).where(
                AlphaTranscriptionJobModel.workspace_id == workspace.id,
                AlphaTranscriptionJobModel.source_sha256 == source_sha256,
            )
        )
        if existing is not None:
            return existing, True
    job = AlphaTranscriptionJobModel(
        workspace_id=workspace.id,
        source_path=command.source_path,
        source_filename=command.source_filename,
        source_media_type=command.source_media_type,
        source_sha256=source_sha256,
        source_size_bytes=source_size_bytes,
        title=command.title.strip(),
        analysis_requested=command.analysis_requested,
        language=command.language,
        model=command.model,
        state="queued",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, False


async def get_transcription_job(
    session: AsyncSession,
    *,
    workspace_id: str,
    job_id: str,
) -> AlphaTranscriptionJobModel | None:
    try:
        workspace_uuid = uuid.UUID(workspace_id)
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return None
    result: AlphaTranscriptionJobModel | None = await session.scalar(
        select(AlphaTranscriptionJobModel).where(
            AlphaTranscriptionJobModel.id == job_uuid,
            AlphaTranscriptionJobModel.workspace_id == workspace_uuid,
        )
    )
    return result


async def list_transcription_jobs(
    session: AsyncSession,
    *,
    workspace_id: str,
    limit: int,
) -> list[AlphaTranscriptionJobModel]:
    workspace_uuid = uuid.UUID(workspace_id)
    rows = await session.scalars(
        select(AlphaTranscriptionJobModel)
        .where(AlphaTranscriptionJobModel.workspace_id == workspace_uuid)
        .order_by(AlphaTranscriptionJobModel.created_at.desc())
        .limit(max(1, min(limit, 50)))
    )
    return list(rows)


async def get_job_parts(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> list[AlphaTranscriptionPartModel]:
    rows = await session.scalars(
        select(AlphaTranscriptionPartModel)
        .where(AlphaTranscriptionPartModel.job_id == job_id)
        .order_by(
            AlphaTranscriptionPartModel.part_index,
            AlphaTranscriptionPartModel.attempt_version.desc(),
        )
    )
    latest: dict[int, AlphaTranscriptionPartModel] = {}
    for row in rows:
        latest.setdefault(row.part_index, row)
    return [latest[index] for index in sorted(latest)]


async def retry_failed_transcription_job(
    session: AsyncSession,
    *,
    job: AlphaTranscriptionJobModel,
) -> AlphaTranscriptionJobModel:
    """Create new attempt rows only after an explicit retry of a known failure."""

    if job.state != "failed":
        raise ValueError("alpha_transcription_retry_not_allowed")
    latest = await get_job_parts(session, job_id=job.id)
    failed = [part for part in latest if part.state == "failed"]
    if latest and not failed:
        raise ValueError("alpha_transcription_retry_not_allowed")
    for part in failed:
        session.add(
            AlphaTranscriptionPartModel(
                job_id=job.id,
                part_index=part.part_index,
                attempt_version=part.attempt_version + 1,
                start_millis=part.start_millis,
                end_millis=part.end_millis,
                source_sha256=part.source_sha256,
                source_size_bytes=part.source_size_bytes,
                state="queued",
                attempts=0,
            )
        )
    job.state = "transcribing" if latest else "queued"
    job.error_code = None
    job.next_attempt_at = datetime.now(UTC)
    job.completed_at = None
    await session.commit()
    await session.refresh(job)
    return job


def job_payload(job: AlphaTranscriptionJobModel) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "canonical_job_id": (
            str(job.canonical_job_id) if job.canonical_job_id else None
        ),
        "title": job.title,
        "state": job.state,
        "source_filename": job.source_filename,
        "source_sha256": job.source_sha256,
        "source_size_bytes": job.source_size_bytes,
        "duration_millis": job.duration_millis,
        "language": job.language,
        "model": job.model,
        "chunk_count": job.chunk_count,
        "completed_chunk_count": job.completed_chunk_count,
        "provider_attempts": job.provider_attempts,
        "billed_quantity": job.billed_quantity,
        "actual_microusd": job.actual_microusd,
        "analysis_requested": job.analysis_requested,
        "artifacts": {
            "verbatim.md": bool(job.verbatim_path),
            "clean.md": bool(job.clean_path),
            "analysis.md": bool(job.analysis_path),
            "manifest.json": bool(job.manifest_path),
        },
        "error_code": job.error_code,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
    }


def artifact_path(job: AlphaTranscriptionJobModel, artifact: str) -> str | None:
    return {
        "verbatim.md": job.verbatim_path,
        "clean.md": job.clean_path,
        "analysis.md": job.analysis_path,
        "manifest.json": job.manifest_path,
    }.get(artifact)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
