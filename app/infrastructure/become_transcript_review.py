"""PostgreSQL persistence for the bounded BECOME Pilot transcript review."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_controlled_intake import (
    BecomeControlledIntakeError,
)
from app.application.become_transcript_review import (
    PilotDeliveryJob,
    PilotReviewDisposition,
    request_digest,
)

_TRANSCRIPT_RETENTION = timedelta(days=30)
_TOMBSTONE_SCHEMA = "gate.telegram-content-tombstone@1"


class SessionFactoryPilotTranscriptReviewStore:
    """One-workspace review ledger with append-only versions and sends."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def apply_callback(
        self,
        *,
        action: str,
        callback_ref: str,
        subject_ref: str,
        provider_dialog_id: str,
        now: datetime,
    ) -> PilotReviewDisposition:
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT r.id, r.state, r.latest_version,
                               r.callback_ref::text,
                               r.approved_version,
                               v.transcript_digest, v.expires_at
                        FROM become_pilot_transcript_reviews AS r
                        JOIN dialogs AS d ON d.id = r.dialog_id
                        JOIN become_pilot_transcript_versions AS v
                          ON v.review_id = r.id
                         AND v.version = r.latest_version
                        WHERE r.callback_ref = CAST(:callback_ref AS uuid)
                          AND r.subject_ref = :subject_ref
                          AND d.provider_dialog_id = :provider_dialog_id
                        FOR UPDATE OF r, v
                        """
                    ),
                    {
                        "callback_ref": _uuid(callback_ref),
                        "subject_ref": subject_ref,
                        "provider_dialog_id": provider_dialog_id,
                    },
                )
            ).mappings().one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_pilot_callback_binding_invalid"
                )
            if row["expires_at"] <= now:
                raise BecomeControlledIntakeError(
                    "become_pilot_transcript_expired"
                )
            review_id = str(row["id"])
            version = int(row["latest_version"])
            digest = str(row["transcript_digest"])
            state = str(row["state"])
            exact_callback = str(row["callback_ref"]).replace("-", "")
            if exact_callback != callback_ref:
                raise BecomeControlledIntakeError(
                    "become_pilot_callback_mutated"
                )
            if action == "e":
                if state == "approved":
                    raise BecomeControlledIntakeError(
                        "become_pilot_approved_correction_forbidden"
                    )
                if state not in {"presented", "awaiting_correction"}:
                    raise BecomeControlledIntakeError(
                        "become_pilot_stale_callback"
                    )
                await session.execute(
                    text(
                        """
                        UPDATE become_pilot_transcript_reviews
                        SET state = 'awaiting_correction', updated_at = :now
                        WHERE id = CAST(:review_id AS uuid)
                        """
                    ),
                    {"review_id": review_id, "now": now},
                )
                await _insert_delivery(
                    session,
                    review_id=review_id,
                    kind="correction_prompt",
                    version=version,
                    transcript_digest=digest,
                    callback_ref=callback_ref,
                    now=now,
                )
                return PilotReviewDisposition.CORRECTION_REQUESTED
            if action != "c":
                raise BecomeControlledIntakeError(
                    "become_pilot_callback_action_invalid"
                )
            if state == "approved":
                if int(row["approved_version"] or 0) != version:
                    raise BecomeControlledIntakeError(
                        "become_pilot_approval_binding_invalid"
                    )
                return PilotReviewDisposition.APPROVED_DUPLICATE
            if state != "presented":
                raise BecomeControlledIntakeError(
                    "become_pilot_stale_callback"
                )
            version_row = (
                await session.execute(
                    text(
                        """
                        SELECT transcript_body, transcript_digest, expires_at
                        FROM become_pilot_transcript_versions
                        WHERE review_id = CAST(:review_id AS uuid)
                          AND version = :version
                        FOR UPDATE
                        """
                    ),
                    {"review_id": review_id, "version": version},
                )
            ).mappings().one()
            body = version_row["transcript_body"]
            if not isinstance(body, str) or not body:
                raise BecomeControlledIntakeError(
                    "become_pilot_transcript_content_missing"
                )
            media_row = (
                await session.execute(
                    text(
                        """
                        SELECT media_intake_id::text
                        FROM become_pilot_transcript_reviews
                        WHERE id = CAST(:review_id AS uuid)
                        """
                    ),
                    {"review_id": review_id},
                )
            ).mappings().one()
            updated = await session.execute(
                text(
                    """
                    UPDATE become_media_intakes
                    SET transcript_state = 'confirmed',
                        confirmed_transcript = :body,
                        transcript_digest = :digest,
                        transcript_confirmed_at = :now,
                        transcript_expires_at = :expires_at,
                        transcript_purged_at = NULL,
                        updated_at = :now
                    WHERE id = CAST(:media_intake_id AS uuid)
                      AND transcript_state = 'none'
                    """
                ),
                {
                    "media_intake_id": media_row["media_intake_id"],
                    "body": body,
                    "digest": digest,
                    "now": now,
                    "expires_at": version_row["expires_at"],
                },
            )
            if int(getattr(updated, "rowcount", 0) or 0) != 1:
                raise BecomeControlledIntakeError(
                    "become_pilot_media_approval_invalid"
                )
            await session.execute(
                text(
                    """
                    UPDATE become_pilot_transcript_reviews
                    SET state = 'approved',
                        approved_version = :version,
                        approved_digest = :digest,
                        approved_at = :now,
                        updated_at = :now
                    WHERE id = CAST(:review_id AS uuid)
                    """
                ),
                {
                    "review_id": review_id,
                    "version": version,
                    "digest": digest,
                    "now": now,
                },
            )
            await _insert_delivery(
                session,
                review_id=review_id,
                kind="confirmation",
                version=version,
                transcript_digest=digest,
                callback_ref=callback_ref,
                now=now,
            )
            return PilotReviewDisposition.APPROVED

    async def apply_correction(
        self,
        *,
        subject_ref: str,
        provider_dialog_id: str,
        correction_text: str,
        now: datetime,
    ) -> bool:
        normalized = correction_text.strip()
        if not normalized:
            raise BecomeControlledIntakeError(
                "become_pilot_correction_empty"
            )
        digest = _sha256(normalized)
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT r.id, r.latest_version
                        FROM become_pilot_transcript_reviews AS r
                        JOIN dialogs AS d ON d.id = r.dialog_id
                        WHERE r.state = 'awaiting_correction'
                          AND r.subject_ref = :subject_ref
                          AND d.provider_dialog_id = :provider_dialog_id
                        ORDER BY r.created_at
                        LIMIT 2
                        FOR UPDATE OF r
                        """
                    ),
                    {
                        "subject_ref": subject_ref,
                        "provider_dialog_id": provider_dialog_id,
                    },
                )
            ).mappings().all()
            if not rows:
                return False
            if len(rows) != 1:
                raise BecomeControlledIntakeError(
                    "become_pilot_correction_scope_ambiguous"
                )
            review_id = str(rows[0]["id"])
            version = int(rows[0]["latest_version"]) + 1
            callback_ref = uuid.uuid4().hex
            expires_at = now + _TRANSCRIPT_RETENTION
            await session.execute(
                text(
                    """
                    INSERT INTO become_pilot_transcript_versions (
                        id, review_id, version, source, transcript_body,
                        transcript_digest, expires_at, created_at
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:review_id AS uuid), :version,
                        'human_correction', :body, :digest, :expires_at, :now
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "review_id": review_id,
                    "version": version,
                    "body": normalized,
                    "digest": digest,
                    "expires_at": expires_at,
                    "now": now,
                },
            )
            await session.execute(
                text(
                    """
                    UPDATE become_pilot_transcript_reviews
                    SET state = 'pending_display',
                        latest_version = :version,
                        callback_ref = CAST(:callback_ref AS uuid),
                        approved_version = NULL,
                        approved_digest = NULL,
                        approved_at = NULL,
                        updated_at = :now
                    WHERE id = CAST(:review_id AS uuid)
                    """
                ),
                {
                    "review_id": review_id,
                    "version": version,
                    "callback_ref": _uuid(callback_ref),
                    "now": now,
                },
            )
            await _insert_delivery(
                session,
                review_id=review_id,
                kind="presentation",
                version=version,
                transcript_digest=digest,
                callback_ref=callback_ref,
                now=now,
            )
            return True

    async def claim_delivery(
        self,
        *,
        now: datetime,
        max_operations: int,
    ) -> PilotDeliveryJob | None:
        if not 1 <= max_operations <= 8:
            raise BecomeControlledIntakeError(
                "become_pilot_operation_cap_invalid"
            )
        async with self._sessions.begin() as session:
            operation_count = await session.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM become_pilot_transcript_deliveries
                    WHERE outcome IN ('reserved', 'sent', 'unknown')
                    """
                )
            )
            if int(operation_count or 0) >= max_operations:
                return None
            row = (
                await session.execute(
                    text(
                        """
                        SELECT dl.id, dl.kind, dl.version,
                               dl.transcript_digest,
                               dl.callback_ref::text,
                               d.provider_dialog_id,
                               v.transcript_body
                        FROM become_pilot_transcript_deliveries AS dl
                        JOIN become_pilot_transcript_reviews AS r
                          ON r.id = dl.review_id
                        JOIN dialogs AS d ON d.id = r.dialog_id
                        LEFT JOIN become_pilot_transcript_versions AS v
                          ON v.review_id = r.id
                         AND v.version = dl.version
                        WHERE dl.outcome = 'pending'
                        ORDER BY dl.created_at
                        LIMIT 1
                        FOR UPDATE OF dl
                        """
                    )
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            await session.execute(
                text(
                    """
                    UPDATE become_pilot_transcript_deliveries
                    SET outcome = 'reserved', updated_at = :now
                    WHERE id = CAST(:id AS uuid) AND outcome = 'pending'
                    """
                ),
                {"id": str(row["id"]), "now": now},
            )
            return PilotDeliveryJob(
                delivery_id=str(row["id"]),
                kind=str(row["kind"]),
                provider_dialog_id=str(row["provider_dialog_id"]),
                version=int(row["version"]),
                transcript_digest=str(row["transcript_digest"]),
                callback_ref=str(row["callback_ref"]).replace("-", ""),
                transcript_text=(
                    str(row["transcript_body"])
                    if row["transcript_body"] is not None
                    else None
                ),
            )

    async def settle_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        now: datetime,
    ) -> None:
        if outcome not in {"sent", "failed", "unknown"}:
            raise ValueError("become_pilot_delivery_outcome_invalid")
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id::text, review_id::text, kind, version,
                               outcome, telegram_message_id
                        FROM become_pilot_transcript_deliveries
                        WHERE id = CAST(:id AS uuid)
                        FOR UPDATE
                        """
                    ),
                    {"id": delivery_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise BecomeControlledIntakeError(
                    "become_pilot_delivery_missing"
                )
            if row["outcome"] == outcome and row["telegram_message_id"] == telegram_message_id:
                return
            if row["outcome"] != "reserved":
                raise BecomeControlledIntakeError(
                    "become_pilot_delivery_replay_invalid"
                )
            await session.execute(
                text(
                    """
                    UPDATE become_pilot_transcript_deliveries
                    SET outcome = :outcome,
                        telegram_message_id = :telegram_message_id,
                        settled_at = :now,
                        updated_at = :now
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": delivery_id,
                    "outcome": outcome,
                    "telegram_message_id": telegram_message_id,
                    "now": now,
                },
            )
            if outcome == "sent" and row["kind"] == "presentation":
                await session.execute(
                    text(
                        """
                        UPDATE become_pilot_transcript_reviews
                        SET state = 'presented', updated_at = :now
                        WHERE id = CAST(:review_id AS uuid)
                          AND latest_version = :version
                          AND state = 'pending_display'
                        """
                    ),
                    {
                        "review_id": row["review_id"],
                        "version": row["version"],
                        "now": now,
                    },
                )

    async def tombstone_receipt(
        self,
        *,
        receipt_id: str,
        now: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    """
                    UPDATE telegram_receipts
                    SET payload = jsonb_build_object(
                            'schema', :schema,
                            'update_id', update_id,
                            'content_purged', true
                        ),
                        updated_at = :now
                    WHERE id = CAST(:receipt_id AS uuid)
                      AND bot_key = 'telegram_become_group'
                      AND payload ->> 'schema' IS DISTINCT FROM :schema
                    """
                ),
                {
                    "receipt_id": receipt_id,
                    "schema": _TOMBSTONE_SCHEMA,
                    "now": now,
                },
            )

    async def purge_expired(self, *, now: datetime) -> dict[str, int]:
        async with self._sessions.begin() as session:
            versions = await session.execute(
                text(
                    """
                    UPDATE become_pilot_transcript_versions
                    SET transcript_body = NULL, purged_at = :now
                    WHERE transcript_body IS NOT NULL
                      AND expires_at <= :now
                    """
                ),
                {"now": now},
            )
            receipts = await session.execute(
                text(
                    """
                    UPDATE telegram_receipts AS tr
                    SET payload = jsonb_build_object(
                            'schema', :schema,
                            'update_id', tr.update_id,
                            'content_purged', true
                        ),
                        updated_at = :now
                    FROM become_media_intakes AS mi
                    WHERE mi.source_receipt_id = tr.id
                      AND tr.bot_key = 'telegram_become_group'
                      AND tr.payload ->> 'schema' IS DISTINCT FROM :schema
                    """
                ),
                {"schema": _TOMBSTONE_SCHEMA, "now": now},
            )
            return {
                "transcript_versions_purged": int(
                    getattr(versions, "rowcount", 0) or 0
                ),
                "telegram_payloads_tombstoned": int(
                    getattr(receipts, "rowcount", 0) or 0
                ),
            }

    async def status(self) -> dict[str, int]:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT
                          count(*) FILTER (
                            WHERE r.state = 'pending_display'
                          ) AS pending_display,
                          count(*) FILTER (
                            WHERE r.state = 'presented'
                          ) AS presented,
                          count(*) FILTER (
                            WHERE r.state = 'awaiting_correction'
                          ) AS awaiting_correction,
                          count(*) FILTER (
                            WHERE r.state = 'approved'
                          ) AS approved,
                          (
                            SELECT count(*)
                            FROM become_pilot_transcript_deliveries
                            WHERE outcome = 'unknown'
                          ) AS delivery_unknown
                        FROM become_pilot_transcript_reviews AS r
                        """
                    )
                )
            ).mappings().one()
            return {key: int(value or 0) for key, value in row.items()}


async def stage_provider_transcript(
    session: AsyncSession,
    *,
    attempt_id: str,
    workspace_id: str,
    correlation_id: str,
    media_intake_id: str,
    transcript_body: str,
    transcript_digest: str,
    provider_succeeded_at: datetime,
    now: datetime,
) -> bool:
    """Stage provider output as version 1 without creating human approval."""

    binding = (
        await session.execute(
            text(
                """
                SELECT mi.subject_ref, ai.dialog_id::text,
                       d.provider_dialog_id
                FROM become_media_intakes AS mi
                JOIN agent_installations AS ai
                  ON ai.id = mi.installation_id
                 AND ai.workspace_id = mi.workspace_id
                JOIN dialogs AS d
                  ON d.id = ai.dialog_id
                 AND d.workspace_id = mi.workspace_id
                WHERE mi.id = CAST(:media_intake_id AS uuid)
                  AND mi.workspace_id = CAST(:workspace_id AS uuid)
                """
            ),
            {
                "media_intake_id": media_intake_id,
                "workspace_id": workspace_id,
            },
        )
    ).mappings().one_or_none()
    if binding is None:
        raise BecomeControlledIntakeError(
            "become_pilot_transcript_binding_missing"
        )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text
                FROM become_pilot_transcript_reviews
                WHERE attempt_id = CAST(:attempt_id AS uuid)
                """
            ),
            {"attempt_id": attempt_id},
        )
    ).mappings().one_or_none()
    if existing is not None:
        return True
    review_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    callback_ref = uuid.uuid4().hex
    expires_at = provider_succeeded_at + _TRANSCRIPT_RETENTION
    await session.execute(
        text(
            """
            INSERT INTO become_pilot_transcript_reviews (
                id, workspace_id, correlation_id, media_intake_id,
                attempt_id, dialog_id, subject_ref, state,
                latest_version, callback_ref, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid), CAST(:workspace_id AS uuid),
                CAST(:correlation_id AS uuid),
                CAST(:media_intake_id AS uuid),
                CAST(:attempt_id AS uuid), CAST(:dialog_id AS uuid),
                :subject_ref, 'pending_display', 1,
                CAST(:callback_ref AS uuid), :now, :now
            )
            """
        ),
        {
            "id": review_id,
            "workspace_id": workspace_id,
            "correlation_id": correlation_id,
            "media_intake_id": media_intake_id,
            "attempt_id": attempt_id,
            "dialog_id": binding["dialog_id"],
            "subject_ref": binding["subject_ref"],
            "callback_ref": _uuid(callback_ref),
            "now": now,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO become_pilot_transcript_versions (
                id, review_id, version, source, transcript_body,
                transcript_digest, expires_at, created_at
            ) VALUES (
                CAST(:id AS uuid), CAST(:review_id AS uuid), 1,
                'provider', :body, :digest, :expires_at, :now
            )
            """
        ),
        {
            "id": version_id,
            "review_id": review_id,
            "body": transcript_body,
            "digest": transcript_digest,
            "expires_at": expires_at,
            "now": now,
        },
    )
    await _insert_delivery(
        session,
        review_id=review_id,
        kind="presentation",
        version=1,
        transcript_digest=transcript_digest,
        callback_ref=callback_ref,
        now=now,
    )
    return False


async def _insert_delivery(
    session: AsyncSession,
    *,
    review_id: str,
    kind: str,
    version: int,
    transcript_digest: str,
    callback_ref: str,
    now: datetime,
) -> None:
    digest = request_digest(
        kind=kind,
        version=version,
        transcript_digest=transcript_digest,
        callback_ref=callback_ref,
    )
    await session.execute(
        text(
            """
            INSERT INTO become_pilot_transcript_deliveries (
                id, review_id, kind, version, transcript_digest,
                callback_ref, request_digest, outcome, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid), CAST(:review_id AS uuid), :kind, :version,
                :transcript_digest, CAST(:callback_ref AS uuid),
                :request_digest, 'pending', :now, :now
            )
            ON CONFLICT (review_id, kind, version, transcript_digest)
            DO NOTHING
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "review_id": review_id,
            "kind": kind,
            "version": version,
            "transcript_digest": transcript_digest,
            "callback_ref": _uuid(callback_ref),
            "request_digest": digest,
            "now": now,
        },
    )


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        try:
            return str(uuid.UUID(hex=value))
        except ValueError:
            raise BecomeControlledIntakeError(
                "become_pilot_uuid_invalid"
            ) from None


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
