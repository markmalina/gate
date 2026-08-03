"""Durable background worker for private Alfa transcription jobs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.alpha_transcription_product import (
    AlphaTranscriptionError,
    StoredArtifact,
    analyze_transcript,
    build_manifest,
    clean_transcript,
    merge_overlapping_transcripts,
    sha256_bytes,
)
from app.application.telegram_voice_transcription import TranscriptionProviderError
from app.core.config import Settings
from app.infrastructure.alpha_media_chunking import PreparedMedia, prepare_media_chunks
from app.infrastructure.alpha_transcription_jobs import get_job_parts
from app.infrastructure.dropbox_transcription_storage import DropboxTranscriptionStorage
from app.infrastructure.openai_transcription_parts import (
    OpenAITranscriptionPartClient,
    OpenAITranscriptionPartConfig,
)
from app.models.alpha_transcription import (
    AlphaTranscriptionJobModel,
    AlphaTranscriptionPartModel,
)

logger = logging.getLogger(__name__)
_LEASE = timedelta(hours=2)
_RETRY_ASSEMBLY = timedelta(minutes=5)
_IDLE_SECONDS = 5.0


async def run_alpha_transcription_worker_loop(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Drain durable jobs; never auto-retry a consumed provider attempt."""

    if not _configured(settings):
        return
    await reconcile_interrupted_transcriptions(session_factory)
    async with httpx.AsyncClient() as http:
        storage = DropboxTranscriptionStorage(settings=settings, http=http)
        while True:
            try:
                job_id = await _claim_next_job(session_factory)
                if job_id is None:
                    await asyncio.sleep(_IDLE_SECONDS)
                    continue
                await _process_job(
                    job_id=job_id,
                    session_factory=session_factory,
                    storage=storage,
                    settings=settings,
                    http=http,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Alfa transcription worker iteration failed")
                await asyncio.sleep(_IDLE_SECONDS)


async def reconcile_interrupted_transcriptions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Make uncertain provider outcomes terminal before any new processing."""

    async with session_factory() as session:
        running = list(
            await session.scalars(
                select(AlphaTranscriptionPartModel).where(
                    AlphaTranscriptionPartModel.state == "running"
                )
            )
        )
        job_ids: set[uuid.UUID] = set()
        for part in running:
            part.state = "unknown"
            part.error_code = "alpha_transcription_interrupted_outcome_unknown"
            part.completed_at = datetime.now(UTC)
            job_ids.add(part.job_id)
        for job_id in job_ids:
            job = await session.get(AlphaTranscriptionJobModel, job_id)
            if job is not None:
                job.state = "unknown"
                job.error_code = "alpha_transcription_interrupted_outcome_unknown"
                job.next_attempt_at = None
        await session.commit()


async def _claim_next_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID | None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            job = await session.scalar(
                select(AlphaTranscriptionJobModel)
                .where(
                    AlphaTranscriptionJobModel.state.in_(
                        ("queued", "preparing", "transcribing", "assembling")
                    ),
                    or_(
                        AlphaTranscriptionJobModel.next_attempt_at.is_(None),
                        AlphaTranscriptionJobModel.next_attempt_at <= now,
                    ),
                )
                .order_by(AlphaTranscriptionJobModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.state == "queued":
                job.state = "preparing"
                job.started_at = job.started_at or now
            job.next_attempt_at = now + _LEASE
            job.error_code = None
            return job.id


async def _process_job(
    *,
    job_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    storage: DropboxTranscriptionStorage,
    settings: Settings,
    http: httpx.AsyncClient,
) -> None:
    with tempfile.TemporaryDirectory(prefix="gate-alpha-transcription-") as directory:
        root = Path(directory)
        source = root / "source-media"
        job = await _load_job(session_factory, job_id)
        provider = OpenAITranscriptionPartClient(
            config=OpenAITranscriptionPartConfig(
                api_key=settings.openai_api_key.get_secret_value(),
                model=job.model,
                base_url=settings.openai_api_base_url,
                timeout_seconds=300,
            ),
            http=http,
        )
        try:
            downloaded_size = await storage.download_to_file(job.source_path, source)
            source_sha = await asyncio.to_thread(_sha256_file, source)
            duplicate = await _bind_source_identity(
                session_factory=session_factory,
                job_id=job_id,
                source_sha256=source_sha,
                source_size_bytes=downloaded_size,
            )
            if duplicate:
                return
            permanent_source_path = _source_path(job_id, job.source_filename)
            if job.source_path != permanent_source_path:
                await storage.upload_file(permanent_source_path, source)
                await _set_source_path(session_factory, job_id, permanent_source_path)
            prepared = await prepare_media_chunks(source, root / "chunks")
            await _ensure_parts(
                session_factory=session_factory,
                job_id=job_id,
                prepared=prepared,
            )
            complete = await _transcribe_parts(
                session_factory=session_factory,
                job_id=job_id,
                prepared=prepared,
                storage=storage,
                provider=provider,
            )
            if not complete:
                return
            await _assemble_artifacts(
                session_factory=session_factory,
                job_id=job_id,
                storage=storage,
            )
        except AlphaTranscriptionError as exc:
            await _mark_job_error(
                session_factory=session_factory,
                job_id=job_id,
                code=exc.code,
                retryable_assembly=False,
            )
        except OSError:
            await _mark_job_error(
                session_factory=session_factory,
                job_id=job_id,
                code="alpha_transcription_local_io_failed",
                retryable_assembly=False,
            )


async def _load_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
) -> AlphaTranscriptionJobModel:
    async with session_factory() as session:
        job = await session.get(AlphaTranscriptionJobModel, job_id)
        if job is None:
            raise AlphaTranscriptionError("alpha_transcription_job_missing")
        return job


async def _bind_source_identity(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    source_sha256: str,
    source_size_bytes: int,
) -> bool:
    async with session_factory() as session:
        async with session.begin():
            job = await session.scalar(
                select(AlphaTranscriptionJobModel)
                .where(AlphaTranscriptionJobModel.id == job_id)
                .with_for_update()
            )
            if job is None:
                raise AlphaTranscriptionError("alpha_transcription_job_missing")
            if job.source_sha256 is not None and job.source_sha256 != source_sha256:
                raise AlphaTranscriptionError(
                    "alpha_transcription_source_digest_changed"
                )
            existing = await session.scalar(
                select(AlphaTranscriptionJobModel)
                .where(
                    AlphaTranscriptionJobModel.workspace_id == job.workspace_id,
                    AlphaTranscriptionJobModel.source_sha256 == source_sha256,
                    AlphaTranscriptionJobModel.id != job.id,
                    AlphaTranscriptionJobModel.state != "duplicate",
                )
                .order_by(AlphaTranscriptionJobModel.created_at)
                .limit(1)
            )
            if existing is not None:
                job.state = "duplicate"
                job.canonical_job_id = existing.id
                job.source_size_bytes = source_size_bytes
                job.next_attempt_at = None
                job.completed_at = datetime.now(UTC)
                return True
            job.source_sha256 = source_sha256
            job.source_size_bytes = source_size_bytes
            return False


async def _set_source_path(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    path: str,
) -> None:
    async with session_factory() as session:
        job = await session.get(AlphaTranscriptionJobModel, job_id)
        if job is None:
            return
        job.source_path = path
        await session.commit()


async def _ensure_parts(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    prepared: PreparedMedia,
) -> None:
    async with session_factory() as session:
        job = await session.get(AlphaTranscriptionJobModel, job_id)
        if job is None:
            raise AlphaTranscriptionError("alpha_transcription_job_missing")
        latest = await get_job_parts(session, job_id=job_id)
        if latest:
            if len(latest) != len(prepared.chunks):
                raise AlphaTranscriptionError(
                    "alpha_transcription_part_contract_changed"
                )
            for row, chunk in zip(latest, prepared.chunks, strict=True):
                if (
                    row.part_index != chunk.window.index
                    or row.start_millis != chunk.window.start_millis
                    or row.end_millis != chunk.window.end_millis
                    or row.source_sha256 != chunk.sha256
                    or row.source_size_bytes != chunk.size_bytes
                ):
                    raise AlphaTranscriptionError(
                        "alpha_transcription_part_contract_changed"
                    )
        else:
            for chunk in prepared.chunks:
                session.add(
                    AlphaTranscriptionPartModel(
                        job_id=job_id,
                        part_index=chunk.window.index,
                        attempt_version=1,
                        start_millis=chunk.window.start_millis,
                        end_millis=chunk.window.end_millis,
                        source_sha256=chunk.sha256,
                        source_size_bytes=chunk.size_bytes,
                        state="queued",
                        attempts=0,
                    )
                )
        job.duration_millis = prepared.duration_millis
        job.chunk_count = len(prepared.chunks)
        job.state = "transcribing"
        job.next_attempt_at = datetime.now(UTC) + _LEASE
        await session.commit()


async def _transcribe_parts(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    prepared: PreparedMedia,
    storage: DropboxTranscriptionStorage,
    provider: OpenAITranscriptionPartClient,
) -> bool:
    prior_texts: list[str] = []
    latest = await _latest_parts(session_factory, job_id)
    job = await _load_job(session_factory, job_id)
    for row, chunk in zip(latest, prepared.chunks, strict=True):
        if row.state == "completed":
            if row.transcript_path is None:
                raise AlphaTranscriptionError(
                    "alpha_transcription_part_artifact_missing"
                )
            prior_texts.append(
                (
                    await storage.download_bytes(
                        row.transcript_path,
                        maximum=2_000_000,
                    )
                ).decode("utf-8")
            )
            continue
        if row.state in {"failed", "unknown", "running"}:
            return False
        await _mark_part_running(session_factory, row.id, job_id)
        data = chunk.path.read_bytes()
        continuity = prior_texts[-1][-1_000:] if prior_texts else None
        try:
            result = await provider.transcribe_part(
                data=data,
                filename=chunk.path.name,
                language=job.language,
                continuity_prompt=continuity,
            )
        except TranscriptionProviderError as exc:
            await _mark_part_provider_error(
                session_factory=session_factory,
                part_id=row.id,
                job_id=job_id,
                code=exc.code,
                unknown=exc.outcome_unknown,
            )
            return False
        transcript = result.text.strip().encode("utf-8")
        path = _part_transcript_path(
            job_id,
            row.part_index,
            row.attempt_version,
        )
        try:
            await storage.upload_bytes(path, transcript)
        except AlphaTranscriptionError:
            await _mark_part_provider_error(
                session_factory=session_factory,
                part_id=row.id,
                job_id=job_id,
                code="alpha_transcription_result_storage_unknown",
                unknown=True,
            )
            return False
        await _complete_part(
            session_factory=session_factory,
            part_id=row.id,
            job_id=job_id,
            path=path,
            transcript=transcript,
            provider_ref=result.provider_operation_ref,
            billed_quantity=result.billed_quantity,
            actual_microusd=result.actual_microusd,
        )
        prior_texts.append(result.text.strip())
    async with session_factory() as session:
        current = await session.get(AlphaTranscriptionJobModel, job_id)
        if current is None:
            return False
        current.state = "assembling"
        current.next_attempt_at = datetime.now(UTC)
        await session.commit()
    return True


async def _latest_parts(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
) -> list[AlphaTranscriptionPartModel]:
    async with session_factory() as session:
        return await get_job_parts(session, job_id=job_id)


async def _mark_part_running(
    session_factory: async_sessionmaker[AsyncSession],
    part_id: uuid.UUID,
    job_id: uuid.UUID,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            part = await session.scalar(
                select(AlphaTranscriptionPartModel)
                .where(AlphaTranscriptionPartModel.id == part_id)
                .with_for_update()
            )
            job = await session.get(AlphaTranscriptionJobModel, job_id)
            if (
                part is None
                or job is None
                or part.state != "queued"
                or part.attempts != 0
            ):
                raise AlphaTranscriptionError(
                    "alpha_transcription_part_not_claimable"
                )
            part.state = "running"
            part.attempts = 1
            part.started_at = datetime.now(UTC)
            job.provider_attempts += 1
            job.next_attempt_at = datetime.now(UTC) + _LEASE


async def _mark_part_provider_error(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    part_id: uuid.UUID,
    job_id: uuid.UUID,
    code: str,
    unknown: bool,
) -> None:
    async with session_factory() as session:
        part = await session.get(AlphaTranscriptionPartModel, part_id)
        job = await session.get(AlphaTranscriptionJobModel, job_id)
        if part is not None:
            part.state = "unknown" if unknown else "failed"
            part.error_code = code
            part.completed_at = datetime.now(UTC)
        if job is not None:
            job.state = "unknown" if unknown else "failed"
            job.error_code = code
            job.next_attempt_at = None
        await session.commit()


async def _complete_part(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    part_id: uuid.UUID,
    job_id: uuid.UUID,
    path: str,
    transcript: bytes,
    provider_ref: str | None,
    billed_quantity: int,
    actual_microusd: int,
) -> None:
    async with session_factory() as session:
        part = await session.get(AlphaTranscriptionPartModel, part_id)
        job = await session.get(AlphaTranscriptionJobModel, job_id)
        if part is None or job is None or part.state != "running":
            raise AlphaTranscriptionError(
                "alpha_transcription_part_completion_invalid"
            )
        part.state = "completed"
        part.provider_operation_ref = provider_ref
        part.billed_quantity = billed_quantity
        part.actual_microusd = actual_microusd
        part.transcript_path = path
        part.transcript_sha256 = sha256_bytes(transcript)
        part.transcript_chars = len(transcript.decode("utf-8"))
        part.completed_at = datetime.now(UTC)
        job.completed_chunk_count += 1
        job.billed_quantity += billed_quantity
        job.actual_microusd += actual_microusd
        job.next_attempt_at = datetime.now(UTC) + _LEASE
        await session.commit()


async def _assemble_artifacts(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    storage: DropboxTranscriptionStorage,
) -> None:
    job = await _load_job(session_factory, job_id)
    latest = await _latest_parts(session_factory, job_id)
    if not latest or any(part.state != "completed" for part in latest):
        raise AlphaTranscriptionError("alpha_transcription_parts_incomplete")
    transcripts: list[str] = []
    for part in latest:
        if part.transcript_path is None:
            raise AlphaTranscriptionError(
                "alpha_transcription_part_artifact_missing"
            )
        transcripts.append(
            (
                await storage.download_bytes(
                    part.transcript_path,
                    maximum=2_000_000,
                )
            ).decode("utf-8")
        )
    verbatim_text = merge_overlapping_transcripts(transcripts)
    clean_text = clean_transcript(verbatim_text)
    analysis_text = analyze_transcript(verbatim_text) if job.analysis_requested else None
    base = _job_root(job_id)
    artifacts: dict[str, StoredArtifact] = {}
    try:
        artifacts["verbatim.md"] = await _store_text(
            storage,
            f"{base}/verbatim.md",
            verbatim_text + "\n",
        )
        artifacts["clean.md"] = await _store_text(
            storage,
            f"{base}/clean.md",
            clean_text + "\n",
        )
        if analysis_text is not None:
            artifacts["analysis.md"] = await _store_text(
                storage,
                f"{base}/analysis.md",
                analysis_text,
            )
        completed_at = datetime.now(UTC)
        manifest = build_manifest(
            job_id=str(job.id),
            title=job.title,
            source_filename=job.source_filename,
            source_path=job.source_path,
            source_sha256=job.source_sha256 or "",
            source_size_bytes=job.source_size_bytes or 0,
            duration_millis=job.duration_millis or 0,
            model=job.model,
            language=job.language,
            chunk_count=job.chunk_count,
            billed_quantity=job.billed_quantity,
            actual_microusd=job.actual_microusd,
            artifacts=artifacts,
            created_at=job.created_at,
            completed_at=completed_at,
        )
        manifest_path = f"{base}/manifest.json"
        await storage.upload_bytes(manifest_path, manifest)
    except AlphaTranscriptionError as exc:
        await _mark_job_error(
            session_factory=session_factory,
            job_id=job_id,
            code=exc.code,
            retryable_assembly=True,
        )
        return
    async with session_factory() as session:
        current = await session.get(AlphaTranscriptionJobModel, job_id)
        if current is None:
            return
        current.verbatim_path = artifacts["verbatim.md"].path
        current.verbatim_sha256 = artifacts["verbatim.md"].sha256
        current.clean_path = artifacts["clean.md"].path
        current.clean_sha256 = artifacts["clean.md"].sha256
        if "analysis.md" in artifacts:
            current.analysis_path = artifacts["analysis.md"].path
            current.analysis_sha256 = artifacts["analysis.md"].sha256
        current.manifest_path = manifest_path
        current.manifest_sha256 = sha256_bytes(manifest)
        current.state = "completed"
        current.error_code = None
        current.completed_at = completed_at
        current.next_attempt_at = None
        await session.commit()


async def _store_text(
    storage: DropboxTranscriptionStorage,
    path: str,
    text: str,
) -> StoredArtifact:
    data = text.encode("utf-8")
    entry = await storage.upload_bytes(path, data)
    return StoredArtifact(
        path=entry.path,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
    )


async def _mark_job_error(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    code: str,
    retryable_assembly: bool,
) -> None:
    async with session_factory() as session:
        job = await session.get(AlphaTranscriptionJobModel, job_id)
        if job is None or job.state in {"completed", "duplicate", "unknown"}:
            return
        job.error_code = code
        if (
            retryable_assembly
            and job.completed_chunk_count == job.chunk_count
            and job.chunk_count > 0
        ):
            job.state = "assembling"
            job.next_attempt_at = datetime.now(UTC) + _RETRY_ASSEMBLY
        else:
            job.state = "failed"
            job.next_attempt_at = None
        await session.commit()


def _configured(settings: Settings) -> bool:
    return bool(
        settings.openai_api_key.get_secret_value()
        and settings.dropbox_call_app_key
        and settings.dropbox_call_app_secret.get_secret_value()
        and settings.dropbox_call_refresh_token.get_secret_value()
        and settings.dropbox_call_account_id
    )


def _job_root(job_id: uuid.UUID) -> str:
    return f"/Alpha/Transcriptions/{job_id}"


def _source_path(job_id: uuid.UUID, filename: str) -> str:
    safe_name = PurePosixPath(filename).name
    return f"{_job_root(job_id)}/source/{safe_name}"


def _part_transcript_path(
    job_id: uuid.UUID,
    index: int,
    attempt_version: int,
) -> str:
    return f"{_job_root(job_id)}/parts/part-{index:04d}-v{attempt_version}.txt"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
