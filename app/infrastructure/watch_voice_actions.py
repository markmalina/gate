"""Private Mark Actions job: verified binding → one note → one publication part."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.watch_voice_actions_oidc import (
    OWNER_ID,
    REPOSITORY,
    REPOSITORY_ID,
    WORKFLOW_PATH,
)
from app.infrastructure.watch_voice_github import MarkInboxClient, WatchVoiceError
from app.infrastructure.watch_voice_journal import pulse
from app.infrastructure.watch_voice_metrics import OBSERVATION, observed_cycle
from app.infrastructure.watch_voice_post_payment import continue_note
from app.infrastructure.watch_voice_recovery import (
    RECOVERY_ERRORS,
    authorized_at,
    recover_note,
)
from app.infrastructure.watch_voice_sources import archive_source, drain_sources
from app.infrastructure.watch_voice_sources import discover as discover_source
from app.models.watch_voice import (
    WatchVoiceCursor,
    WatchVoiceDrainRun,
    WatchVoiceNote,
    WatchVoiceRecoveryClaim,
)
from app.worker import watch_voice as worker
from app.worker.dropbox_calls import _refresh_access_token, _verify_account_id

BINDING_PATH = ".gate/watch-voice-binding.json"
BOT_WRITER = "github-actions[bot]"


def result(kind: str, **counts: int) -> dict[str, Any]:
    return {
        "schema": "watch-voice-drain.v1",
        "result": kind,
        "considered": counts.get("considered", 0),
        "published_parts": counts.get("published_parts", 0),
        "delivered": counts.get("delivered", 0),
        "reconciled": counts.get("reconciled", 0),
        "private_content_exposed": False,
    }


class ActionsInboxClient(MarkInboxClient):
    """A request-local job credential; fixed bot-author receipt validation."""

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        super().__init__(http, token, BOT_WRITER)

    async def _get(self, path: str) -> Any:
        await pulse("github_read", seconds=90)
        try:
            response = await self.http.get(
                "https://api.github.com" + path,
                headers=self.headers,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise WatchVoiceError("watch_voice_actions_read_rejected")
            if len(response.content) > 262_144:
                raise WatchVoiceError("watch_voice_actions_read_limit")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise WatchVoiceError("watch_voice_actions_read_unavailable") from None

    async def file(self, path: str, sha: str, *, maximum: int) -> bytes:
        obj = await self._get(f"/repos/{REPOSITORY}/contents/{path}?ref={sha}")
        if (
            not isinstance(obj, dict)
            or obj.get("type") != "file"
            or obj.get("path") != path
            or obj.get("encoding") != "base64"
            or type(obj.get("size")) is not int
            or not 0 < obj["size"] <= maximum
            or not isinstance(obj.get("content"), str)
        ):
            raise WatchVoiceError("watch_voice_actions_file_invalid")
        try:
            data = base64.b64decode("".join(obj["content"].split()), validate=True)
        except (ValueError, TypeError):
            raise WatchVoiceError("watch_voice_actions_file_invalid") from None
        if len(data) != obj["size"] or len(data) > maximum:
            raise WatchVoiceError("watch_voice_actions_file_invalid")
        return data

    async def preflight_actions(self, claims: dict[str, Any], workflow_pin: str) -> dict[str, Any]:
        repo = await self._get(f"/repos/{REPOSITORY}")
        if (
            not isinstance(repo, dict)
            or repo.get("full_name") != REPOSITORY
            or str(repo.get("id")) != REPOSITORY_ID
            or not isinstance(repo.get("owner"), dict)
            or str(repo["owner"].get("id")) != OWNER_ID
            or repo["owner"].get("login") != "markmalina"
            or repo.get("private") is not True
            or repo.get("archived") is not False
            or repo.get("has_issues") is not True
            or repo.get("default_branch") != "main"
        ):
            raise WatchVoiceError("watch_voice_actions_private_target_mismatch")
        main = await self._get(f"/repos/{REPOSITORY}/git/ref/heads/main")
        if (
            not isinstance(main, dict)
            or not isinstance(main.get("object"), dict)
            or main["object"].get("sha") != claims["sha"]
        ):
            raise WatchVoiceError("watch_voice_actions_stale_main")
        run = await self._get(f"/repos/{REPOSITORY}/actions/runs/{claims['run_id']}")
        if (
            not isinstance(run, dict)
            or str(run.get("id")) != claims["run_id"]
            or run.get("head_sha") != claims["sha"]
            or run.get("head_branch") != "main"
            or run.get("run_attempt") != 1
            or run.get("path") != WORKFLOW_PATH
            or run.get("event") != claims["event_name"]
            or run.get("status") != "in_progress"
        ):
            raise WatchVoiceError("watch_voice_actions_run_mismatch")
        workflow = await self.file(WORKFLOW_PATH, claims["sha"], maximum=32_768)
        if hashlib.sha256(workflow).hexdigest() != workflow_pin:
            raise WatchVoiceError("watch_voice_actions_workflow_mismatch")
        try:
            binding = json.loads(await self.file(BINDING_PATH, claims["sha"], maximum=2_048))
        except (ValueError, UnicodeError):
            raise WatchVoiceError("watch_voice_actions_binding_invalid") from None
        return validate_binding(binding)


def validate_binding(binding: object) -> dict[str, Any]:
    if not isinstance(binding, dict):
        raise WatchVoiceError("watch_voice_actions_binding_invalid")
    scope = binding.get("scope")
    required = {"schema", "enabled", "workspace_key", "scope"}
    required |= (
        {"file_id", "revision"}
        if scope in {"initial-exact", "initial-quota-probe", "initial-post-payment"}
        else {"accepted_initial_event_id"}
    )
    if scope == "initial-quota-probe":
        required |= {
            "event_id",
            "expected_audio_sha256",
            "expected_error",
            "owner_quota_probe",
            "authorized_at",
            "provider_cause",
        }
    if scope == "initial-post-payment":
        required |= {
            "event_id",
            "expected_audio_sha256",
            "expected_error",
            "owner_post_payment",
            "authorized_at",
            "provider_cause",
        }
    if (
        set(binding) != required
        or binding.get("schema") != "watch-voice-binding.v1"
        or binding.get("enabled") is not True
        or binding.get("workspace_key") != "mark-personal"
        or scope
        not in {"initial-exact", "future-only", "initial-quota-probe", "initial-post-payment"}
    ):
        raise WatchVoiceError("watch_voice_actions_binding_invalid")
    if scope in {"initial-exact", "initial-quota-probe", "initial-post-payment"}:
        if (
            not isinstance(binding["file_id"], str)
            or re.fullmatch(r"id:[A-Za-z0-9_-]{1,252}", binding["file_id"]) is None
            or not isinstance(binding["revision"], str)
            or re.fullmatch(r"[a-f0-9]{1,255}", binding["revision"]) is None
        ):
            raise WatchVoiceError("watch_voice_actions_binding_invalid")
        if scope in {"initial-quota-probe", "initial-post-payment"}:
            if (
                binding[
                    "owner_quota_probe" if scope == "initial-quota-probe" else "owner_post_payment"
                ]
                is not True
                or binding["provider_cause"]
                != (
                    "insufficient_quota"
                    if scope == "initial-quota-probe"
                    else "credit_balance_exhausted"
                )
                or not isinstance(binding["expected_error"], str)
                or binding["expected_error"] not in RECOVERY_ERRORS
                or (
                    scope == "initial-post-payment"
                    and binding["expected_error"] != "openai_transcription_quota_unavailable"
                )
                or not isinstance(binding["event_id"], str)
                or re.fullmatch(r"[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}", binding["event_id"])
                is None
                or not isinstance(binding["expected_audio_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", binding["expected_audio_sha256"]) is None
            ):
                raise WatchVoiceError("watch_voice_actions_binding_invalid")
            authorized_at(binding["authorized_at"])
    elif (
        not isinstance(binding["accepted_initial_event_id"], str)
        or re.fullmatch(
            r"[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}", binding["accepted_initial_event_id"]
        )
        is None
    ):
        raise WatchVoiceError("watch_voice_actions_binding_invalid")
    return binding


async def bound_settings(
    settings: Settings,
    binding: dict[str, Any],
    factory: async_sessionmaker[AsyncSession],
    *,
    job_token: str,
) -> Settings:
    """Selectors exist only in protected private config and request-local memory."""
    if settings.gate_workspace_id != "mark-personal" or settings.gate_watch_voice_enabled:
        raise WatchVoiceError("watch_voice_actions_runtime_scope_mismatch")
    file_id, revision = "", ""
    if binding["scope"] in {"initial-exact", "initial-quota-probe", "initial-post-payment"}:
        file_id, revision = binding["file_id"], binding["revision"]
    else:
        async with factory() as session:
            note = await session.get(WatchVoiceNote, binding["accepted_initial_event_id"])
            cursor = await session.get(
                WatchVoiceCursor, (settings.gate_workspace_id, settings.dropbox_call_account_id)
            )
            if (
                note is None
                or note.workspace_key != settings.gate_workspace_id
                or note.account_id != settings.dropbox_call_account_id
                or note.transcription_state != "transcribed"
                or note.delivery_state != "delivered"
                or not note.issue_url
                or cursor is None
                or not cursor.cursor
            ):
                raise WatchVoiceError("watch_voice_actions_initial_acceptance_required")
    # Pydantic validates a fresh instance; the credential is never persisted.
    values = settings.model_dump()
    values.update(
        {
            "gate_watch_voice_enabled": True,
            "gate_watch_voice_data_gate_approved": True,
            "gate_watch_voice_workspace_key": "mark-personal",
            "gate_watch_voice_file_id": file_id,
            "gate_watch_voice_revision": revision,
            "gate_mark_github_token": job_token,
            "gate_mark_github_writer": BOT_WRITER,
        }
    )
    scoped = Settings(
        **{
            (field.validation_alias if isinstance(field.validation_alias, str) else name): values[
                name
            ]
            for name, field in Settings.model_fields.items()
        }
    )
    worker.validate_settings(scoped)
    return scoped


async def claim_run(factory: async_sessionmaker[AsyncSession], claims: dict[str, Any]) -> bool:
    run_key = hashlib.sha256(f"github-mark-watch:{claims['run_id']}:1".encode()).hexdigest()
    jwt_key = hashlib.sha256(f"github-mark-watch-jti:{claims['jti']}".encode()).hexdigest()
    async with factory() as session:
        session.add(
            WatchVoiceDrainRun(run_key=run_key, jwt_key=jwt_key, claimed_at=datetime.now(UTC))
        )
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


async def drain(
    *,
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    claims: dict[str, Any],
    job_token: str,
) -> dict[str, Any]:
    if settings.gate_watch_voice_enabled:
        raise WatchVoiceError("watch_voice_driver_conflict")
    client = ActionsInboxClient(http, job_token)
    binding = await client.preflight_actions(
        claims, settings.gate_watch_voice_actions_workflow_sha256
    )
    scoped = await bound_settings(settings, binding, factory, job_token=job_token)
    # Start durable observation only after the existing private binding has
    # authorized this run. The runtime flag need not carry its data grant.
    observed_settings = settings.model_copy(
        update={
            "gate_watch_voice_data_gate_approved": True,
            "gate_watch_voice_workspace_key": scoped.gate_workspace_id,
        }
    )
    return await _drain_bound(
        settings=observed_settings,
        scoped=scoped,
        factory=factory,
        http=http,
        claims=claims,
        client=client,
        binding=binding,
    )


@observed_cycle
async def _drain_bound(
    *,
    settings: Settings,
    scoped: Settings,
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    claims: dict[str, Any],
    client: ActionsInboxClient,
    binding: dict[str, Any],
) -> dict[str, Any]:
    if not await claim_run(factory, claims):
        return result("replayed")
    if binding["scope"] == "initial-quota-probe":
        recovered_note = await recover_note(
            scoped, factory, http, binding, authorization_sha=claims["sha"]
        )
        if recovered_note is None:
            return result("replayed")
        return await publish_result(factory, client, recovered_note)
    if binding["scope"] == "initial-post-payment":
        continued_note = await continue_note(
            scoped, factory, http, binding, authorization_sha=claims["sha"]
        )
        return await publish_result(factory, client, continued_note)
    await worker.ensure_cursor(scoped, factory)
    if binding["scope"] == "future-only":
        # Stable cutover excludes every preexisting archived/queued note. Commit
        # before intake, so a later outage cannot move the watermark past new notes.
        async with factory() as session:
            await session.execute(
                update(WatchVoiceCursor)
                .where(
                    WatchVoiceCursor.workspace_key == scoped.gate_workspace_id,
                    WatchVoiceCursor.account_id == scoped.dropbox_call_account_id,
                    WatchVoiceCursor.future_cutover_at.is_(None),
                )
                .values(future_cutover_at=datetime.now(UTC))
            )
            await session.commit()
    async with factory() as coordinator:
        state = await coordinator.scalar(
            select(WatchVoiceCursor)
            .where(
                WatchVoiceCursor.workspace_key == scoped.gate_workspace_id,
                WatchVoiceCursor.account_id == scoped.dropbox_call_account_id,
            )
            .with_for_update(skip_locked=True)
        )
        if state is None:
            return result("deferred")
        cutover = worker.future_cutover(state) if binding["scope"] == "future-only" else None
        await pulse("cursor_claimed", owns_cursor=True)
        await pulse("dropbox_scan", scan_started_at=datetime.now(UTC))
        token = await _refresh_access_token(scoped, http)
        await _verify_account_id(scoped, http, token)
        cursor = state.cursor
        for _ in range(100):
            await pulse("dropbox_delta")
            endpoint = "list_folder/continue" if cursor else "list_folder"
            body = (
                {"cursor": cursor}
                if cursor
                else {
                    "path": worker.PATH,
                    "recursive": False,
                    "include_deleted": False,
                }
            )
            try:
                response = await http.post(
                    "https://api.dropboxapi.com/2/files/" + endpoint,
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                    follow_redirects=False,
                )
                delta = response.json()
            except (httpx.HTTPError, ValueError):
                raise WatchVoiceError("watch_voice_delta_unavailable") from None
            if (
                response.status_code != 200
                or not isinstance(delta, dict)
                or not isinstance(delta.get("entries"), list)
                or not isinstance(delta.get("cursor"), str)
                or not delta["cursor"]
                or type(delta.get("has_more")) is not bool
            ):
                raise WatchVoiceError("watch_voice_delta_invalid")
            for item in delta["entries"]:
                key = await discover_source(scoped, factory, item, cutover)
                if key is not None:
                    await archive_source(scoped, factory, http, token, key)
            state.cursor = cursor = delta["cursor"]
            if not delta["has_more"]:
                break
        else:
            raise WatchVoiceError("watch_voice_actions_delta_page_limit")
        await drain_sources(scoped, factory, http, token, cutover)
        OBSERVATION.scanned_at = datetime.now(UTC)
        await pulse("dropbox_scanned", scan_finished_at=datetime.now(UTC), scan_error=None)
        scope = worker.note_scope(scoped)
        if cutover is not None:
            scope += worker.future_note_scope(cutover)
        stale = datetime.now(UTC) - timedelta(hours=1)
        async with factory() as session:
            for field, active, error in (
                (
                    WatchVoiceNote.transcription_state,
                    "processing",
                    "watch_voice_stale_transcription",
                ),
                (WatchVoiceNote.delivery_state, "sending", "watch_voice_stale_publication"),
            ):
                await session.execute(
                    update(WatchVoiceNote)
                    .where(
                        *scope,
                        WatchVoiceNote.attempted_at < stale,
                        field == active,
                    )
                    .values({field.key: "unknown", "last_error": error})
                )
            await session.commit()
            note = await session.scalar(
                select(WatchVoiceNote)
                .where(
                    *scope,
                    or_(
                        WatchVoiceNote.transcription_state == "received",
                        (WatchVoiceNote.transcription_state == "transcribed")
                        & WatchVoiceNote.delivery_state.in_(("pending", "unknown")),
                    ),
                )
                .order_by(*worker.delivery_order())
                .limit(1)
            )
        if note is None:
            await coordinator.commit()
            return result("idle")
        if note.transcription_state == "received":
            await worker.transcribe_note(scoped, factory, http, note.id)
            async with factory() as session:
                refreshed = await session.get(WatchVoiceNote, note.id)
            if refreshed is None:
                raise WatchVoiceError("watch_voice_actions_note_missing")
            note = refreshed
        if note.transcription_state != "transcribed":
            await coordinator.commit()
            return result("processed", considered=1)
        payload = await publish_result(factory, client, note)
        await coordinator.commit()
        return payload


async def publish_result(
    factory: async_sessionmaker[AsyncSession], client: ActionsInboxClient, note: WatchVoiceNote
) -> dict[str, Any]:
    if note.transcription_state != "transcribed":
        return result("processed", considered=1)
    before_part = note.next_part
    was_unknown = note.delivery_state == "unknown"
    await worker.publish_note(factory, client, note, max_parts=1)
    async with factory() as session:
        refreshed = await session.get(WatchVoiceNote, note.id)
    if refreshed is None:
        raise WatchVoiceError("watch_voice_actions_note_missing")
    advanced = int(refreshed.next_part == before_part + 1)
    return result(
        "processed",
        considered=1,
        published_parts=advanced if not was_unknown else 0,
        reconciled=advanced if was_unknown else 0,
        delivered=int(refreshed.delivery_state == "delivered" and bool(advanced)),
    )


async def operational_status(
    session: AsyncSession, settings: Settings, *, scope_allowed: bool
) -> dict[str, Any]:
    """Live flags and scoped journal counts; never claims current token or binding readiness."""
    delivery_counts: dict[str, int] = {}
    transcription_counts: dict[str, int] = {}
    recovery_counts: dict[str, int] = {}
    runs = 0
    if scope_allowed and settings.gate_watch_voice_actions_enabled:
        scope = (
            WatchVoiceNote.workspace_key == settings.gate_workspace_id,
            WatchVoiceNote.account_id == settings.dropbox_call_account_id,
        )
        for field, target in (
            (WatchVoiceNote.delivery_state, delivery_counts),
            (WatchVoiceNote.transcription_state, transcription_counts),
        ):
            rows = await session.execute(select(field, func.count()).where(*scope).group_by(field))
            target.update({str(state): count for state, count in rows})
        runs = int(await session.scalar(select(func.count()).select_from(WatchVoiceDrainRun)) or 0)
        rows = await session.execute(
            select(WatchVoiceRecoveryClaim.outcome, func.count())
            .join(WatchVoiceNote, WatchVoiceNote.id == WatchVoiceRecoveryClaim.note_id)
            .where(*scope)
            .group_by(WatchVoiceRecoveryClaim.outcome)
        )
        recovery_counts.update({str(state): count for state, count in rows})
    return {
        "enabled": settings.gate_watch_voice_actions_enabled,
        "workflow_pin_configured": bool(
            re.fullmatch(r"[a-f0-9]{64}", settings.gate_watch_voice_actions_workflow_sha256)
        ),
        "ordinary_worker_enabled": settings.gate_watch_voice_enabled,
        # Presence is not validity or permission to enable the ordinary worker.
        # Never reveal the credential or configured writer identity, even here.
        "server_writer_configuration": (
            {
                "token_present": isinstance(settings.gate_mark_github_token, SecretStr)
                and bool(settings.gate_mark_github_token.get_secret_value()),
                "writer_present": bool(settings.gate_mark_github_writer),
                "verified": False,
            }
            if scope_allowed
            else None
        ),
        "scope_allowed": scope_allowed,
        "run_claim_count": runs,
        "delivery_counts": delivery_counts,
        "transcription_counts": transcription_counts,
        "recovery_counts": recovery_counts,
        "private_content_exposed": False,
    }
