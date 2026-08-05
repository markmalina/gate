"""PostgreSQL adapter for the bounded Become Slice 3 pilot."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.become_learner_invitation import (
    ACCEPT_ACTION,
    CONSENT_POLICY_DIGEST,
    CONSENT_POLICY_VERSION,
    DECLINE_ACTION,
    PROGRAM_DIGEST,
    PROGRAM_ID,
    PROGRAM_VERSION,
    AcceptanceReceipt,
    CandidateBinding,
    DeliveryJob,
    InvitationReceipt,
    PinInvitationCommand,
    RelationsJob,
    Slice3Disposition,
    Slice3Error,
    canonical_digest,
    learner_binding_digest,
    validate_pin_command,
)


class SessionFactoryLearnerInvitationStore:
    """All slot, invitation and acceptance transitions lock inside one transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def register_candidate(
        self,
        *,
        workspace_key: str,
        source_receipt_id: str,
        subject_ref: str,
        telegram_actor_id: str,
        private_dialog_id: str,
        bot_installation_identity: str,
        become_space_ref: str,
        learner_binding_digest: str,
        now: Any,
    ) -> CandidateBinding:
        async with self._sessions() as session, session.begin():
            workspace_id = await _workspace_id(session, workspace_key)
            existing = (
                (
                    await session.execute(
                        text(
                            "SELECT id, subject_ref, private_dialog_id FROM "
                            "become_slice3_learner_candidates WHERE workspace_id=:workspace_id "
                            "AND learner_binding_digest=:digest FOR UPDATE"
                        ),
                        {
                            "workspace_id": workspace_id,
                            "digest": learner_binding_digest,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing["subject_ref"] != subject_ref
                    or existing["private_dialog_id"] != private_dialog_id
                ):
                    raise Slice3Error("slice3_candidate_digest_collision")
                return CandidateBinding(
                    candidate_id=str(existing["id"]),
                    subject_ref=subject_ref,
                    learner_binding_digest=learner_binding_digest,
                    provider_dialog_id=private_dialog_id,
                    replayed=True,
                )
            candidate_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO become_slice3_learner_candidates "
                    "(id,workspace_id,source_receipt_id,subject_ref,telegram_actor_id,"
                    "private_dialog_id,bot_installation_identity,become_space_ref,"
                    "learner_binding_digest,state,created_at,updated_at) VALUES "
                    "(:id,:workspace_id,:receipt,:subject,:actor,:dialog,:installation,"
                    ":space,:digest,'candidate',:now,:now)"
                ),
                {
                    "id": candidate_id,
                    "workspace_id": workspace_id,
                    "receipt": uuid.UUID(source_receipt_id),
                    "subject": subject_ref,
                    "actor": telegram_actor_id,
                    "dialog": private_dialog_id,
                    "installation": bot_installation_identity,
                    "space": become_space_ref,
                    "digest": learner_binding_digest,
                    "now": now,
                },
            )
            return CandidateBinding(
                candidate_id=str(candidate_id),
                subject_ref=subject_ref,
                learner_binding_digest=learner_binding_digest,
                provider_dialog_id=private_dialog_id,
            )

    async def pin_and_create_invitation(
        self, *, command: PinInvitationCommand, now: Any
    ) -> InvitationReceipt:
        validate_pin_command(command)
        async with self._sessions() as session, session.begin():
            workspace_id = await _workspace_id(session, command.workspace_key)
            await session.execute(
                text("SELECT id FROM workspaces WHERE id=:workspace_id FOR UPDATE"),
                {"workspace_id": workspace_id},
            )
            program = (
                (
                    await session.execute(
                        text(
                            "SELECT p.id,p.subject_ref,p.dialog_id,p.state,p.latest_version,"
                            "p.approved_version,p.approved_digest,v.program_digest,v.program_body,"
                            "v.expires_at,v.purged_at,d.provider_dialog_id AS expert_dialog "
                            "FROM become_pilot_program_drafts p "
                            "JOIN become_pilot_program_versions v ON v.draft_id=p.id "
                            "AND v.version=p.approved_version JOIN dialogs d ON d.id=p.dialog_id "
                            "WHERE p.id=:program_id AND p.workspace_id=:workspace_id FOR UPDATE"
                        ),
                        {
                            "program_id": uuid.UUID(command.program_id),
                            "workspace_id": workspace_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if program is None:
                raise Slice3Error("slice3_program_missing")
            if (
                program["state"] != "approved"
                or program["latest_version"] != PROGRAM_VERSION
                or program["approved_version"] != PROGRAM_VERSION
                or program["approved_digest"] != PROGRAM_DIGEST
                or program["program_digest"] != PROGRAM_DIGEST
                or program["program_body"] is None
                or program["purged_at"] is not None
                or program["expires_at"] <= now
            ):
                raise Slice3Error("slice3_program_not_exact_approved")
            if (
                program["subject_ref"] != command.expert_subject_ref
                or str(program["expert_dialog"]) != command.expert_dialog_id
            ):
                raise Slice3Error("slice3_expert_authority_mismatch")
            candidate = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM become_slice3_learner_candidates "
                            "WHERE workspace_id=:workspace_id AND learner_binding_digest=:digest "
                            "FOR UPDATE"
                        ),
                        {
                            "workspace_id": workspace_id,
                            "digest": command.learner_binding_digest,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None or candidate["state"] not in {"candidate", "pinned"}:
                raise Slice3Error("slice3_candidate_unpinned_or_missing")
            if (
                candidate["subject_ref"] == command.expert_subject_ref
                or candidate["become_space_ref"] != command.become_space_ref
                or candidate["bot_installation_identity"] != command.bot_installation_identity
            ):
                raise Slice3Error("slice3_candidate_scope_mismatch")
            slot = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM become_slice3_pilot_slots "
                            "WHERE workspace_id=:workspace_id "
                            "FOR UPDATE"
                        ),
                        {"workspace_id": workspace_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                slot is not None
                and slot["learner_binding_digest"] != command.learner_binding_digest
            ):
                raise Slice3Error("slice3_pilot_slot_occupied")
            if slot is None:
                await session.execute(
                    text(
                        "INSERT INTO become_slice3_pilot_slots "
                        "(workspace_id,learner_binding_digest,pinned_by_subject_ref,pinned_at) "
                        "VALUES (:workspace_id,:digest,:expert,:now)"
                    ),
                    {
                        "workspace_id": workspace_id,
                        "digest": command.learner_binding_digest,
                        "expert": command.expert_subject_ref,
                        "now": now,
                    },
                )
            tuple_value = {
                "approved_digest": command.approved_digest,
                "approved_version": command.approved_version,
                "become_space_ref": command.become_space_ref,
                "bot_installation_identity": command.bot_installation_identity,
                "consent_policy_digest": command.consent_policy_digest,
                "consent_policy_version": command.consent_policy_version,
                "expert_subject_ref": command.expert_subject_ref,
                "learner_binding_digest": command.learner_binding_digest,
                "program_id": command.program_id,
            }
            invitation_digest = canonical_digest(tuple_value)
            existing = (
                (
                    await session.execute(
                        text(
                            "SELECT id,invitation_digest FROM become_slice3_invitations "
                            "WHERE workspace_id=:workspace_id AND invitation_digest=:digest "
                            "FOR UPDATE"
                        ),
                        {"workspace_id": workspace_id, "digest": invitation_digest},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                return InvitationReceipt(
                    invitation_id=str(existing["id"]),
                    invitation_digest=existing["invitation_digest"],
                    learner_binding_digest=command.learner_binding_digest,
                    replayed=True,
                )
            invitation_id = uuid.uuid4()
            callback_ref = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO become_slice3_invitations "
                    "(id,workspace_id,program_id,candidate_id,expert_subject_ref,"
                    "learner_binding_digest,become_space_ref,bot_installation_identity,"
                    "approved_version,approved_digest,consent_policy_version,"
                    "consent_policy_digest,invitation_digest,callback_ref,callback_digest,"
                    "state,expires_at,"
                    "created_at,updated_at) VALUES (:id,:workspace_id,:program_id,:candidate_id,"
                    ":expert,:binding,:space,:installation,:version,:program_digest,"
                    ":consent_version,:consent_digest,:invitation_digest,:callback_ref,"
                    ":callback_digest,'pending_delivery',:expires,:now,:now)"
                ),
                {
                    "id": invitation_id,
                    "workspace_id": workspace_id,
                    "program_id": uuid.UUID(command.program_id),
                    "candidate_id": candidate["id"],
                    "expert": command.expert_subject_ref,
                    "binding": command.learner_binding_digest,
                    "space": command.become_space_ref,
                    "installation": command.bot_installation_identity,
                    "version": command.approved_version,
                    "program_digest": command.approved_digest,
                    "consent_version": command.consent_policy_version,
                    "consent_digest": command.consent_policy_digest,
                    "invitation_digest": invitation_digest,
                    "callback_ref": callback_ref,
                    "callback_digest": hashlib.sha256(callback_ref.hex.encode()).hexdigest(),
                    "expires": now + timedelta(days=7),
                    "now": now,
                },
            )
            await session.execute(
                text(
                    "UPDATE become_slice3_learner_candidates SET state='pinned',updated_at=:now "
                    "WHERE id=:candidate_id"
                ),
                {"candidate_id": candidate["id"], "now": now},
            )
            await _insert_delivery(
                session,
                invitation_id=invitation_id,
                kind="invitation",
                request_digest=canonical_digest(
                    {"invitation_digest": invitation_digest, "kind": "invitation"}
                ),
                now=now,
            )
            return InvitationReceipt(
                invitation_id=str(invitation_id),
                invitation_digest=invitation_digest,
                learner_binding_digest=command.learner_binding_digest,
                replayed=False,
            )

    async def apply_learner_callback(
        self,
        *,
        action: str,
        callback_ref: str,
        subject_ref: str,
        private_dialog_id: str,
        source_receipt_id: str,
        consent_policy_version: str,
        consent_policy_digest: str,
        now: Any,
    ) -> AcceptanceReceipt:
        if action not in {ACCEPT_ACTION, DECLINE_ACTION}:
            raise Slice3Error("slice3_callback_action_invalid")
        if (
            consent_policy_version != CONSENT_POLICY_VERSION
            or consent_policy_digest != CONSENT_POLICY_DIGEST
        ):
            raise Slice3Error("slice3_consent_policy_mismatch")
        callback_digest = hashlib.sha256(callback_ref.encode()).hexdigest()
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT i.*,c.subject_ref,c.telegram_actor_id,c.private_dialog_id,"
                            "c.bot_installation_identity AS candidate_installation,"
                            "c.become_space_ref AS candidate_space,c.state AS candidate_state,"
                            "p.subject_ref AS program_expert,p.dialog_id AS program_dialog_id,"
                            "p.state AS program_state,p.latest_version,p.approved_version "
                            "AS program_approved_version,p.approved_digest AS "
                            "program_approved_digest,p.invalidated_at,v.program_digest,"
                            "v.program_body,v.expires_at AS program_expires_at,v.purged_at,"
                            "d.provider_dialog_id AS expert_dialog,s.learner_binding_digest "
                            "AS slot_binding,s.pinned_by_subject_ref "
                            "FROM become_slice3_invitations i "
                            "JOIN become_slice3_learner_candidates c ON c.id=i.candidate_id "
                            "JOIN become_pilot_program_drafts p ON p.id=i.program_id "
                            "JOIN become_pilot_program_versions v ON v.draft_id=p.id "
                            "AND v.version=i.approved_version "
                            "JOIN dialogs d ON d.id=p.dialog_id "
                            "JOIN become_slice3_pilot_slots s ON s.workspace_id=i.workspace_id "
                            "WHERE i.callback_digest=:callback_digest "
                            "FOR UPDATE OF i,c,p,v,s"
                        ),
                        {"callback_digest": callback_digest},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise Slice3Error("slice3_invitation_unknown")
            await session.execute(
                text("SELECT id FROM workspaces WHERE id=:workspace_id FOR UPDATE"),
                {"workspace_id": row["workspace_id"]},
            )
            if (
                row["subject_ref"] != subject_ref
                or row["private_dialog_id"] != private_dialog_id
            ):
                raise Slice3Error("slice3_acceptance_actor_mismatch")
            if (
                row["candidate_state"] != "pinned"
                or row["telegram_actor_id"] is None
                or row["private_dialog_id"] is None
                or row["candidate_installation"] is None
                or row["candidate_space"] is None
            ):
                raise Slice3Error("slice3_candidate_binding_unavailable")
            recomputed_binding = learner_binding_digest(
                telegram_actor_id=str(row["telegram_actor_id"]),
                private_dialog_id=str(row["private_dialog_id"]),
                bot_installation_identity=str(row["candidate_installation"]),
                become_space_ref=str(row["candidate_space"]),
            )
            if (
                recomputed_binding != row["learner_binding_digest"]
                or row["slot_binding"] != row["learner_binding_digest"]
                or row["pinned_by_subject_ref"] != row["expert_subject_ref"]
                or row["candidate_installation"] != row["bot_installation_identity"]
                or row["candidate_space"] != row["become_space_ref"]
            ):
                raise Slice3Error("slice3_candidate_binding_mutated")
            if (
                str(row["program_id"]) != PROGRAM_ID
                or row["program_state"] != "approved"
                or row["latest_version"] != PROGRAM_VERSION
                or row["program_approved_version"] != PROGRAM_VERSION
                or row["approved_version"] != PROGRAM_VERSION
                or row["program_approved_digest"] != PROGRAM_DIGEST
                or row["approved_digest"] != PROGRAM_DIGEST
                or row["program_digest"] != PROGRAM_DIGEST
                or row["program_body"] is None
                or row["purged_at"] is not None
                or row["invalidated_at"] is not None
                or row["program_expires_at"] <= now
            ):
                raise Slice3Error("slice3_program_not_exact_approved")
            if (
                row["program_expert"] != row["expert_subject_ref"]
                or str(row["expert_dialog"]) == private_dialog_id
                or row["program_expert"] == row["subject_ref"]
            ):
                raise Slice3Error("slice3_participant_binding_mutated")
            tuple_value = {
                "approved_digest": row["approved_digest"],
                "approved_version": row["approved_version"],
                "become_space_ref": row["become_space_ref"],
                "bot_installation_identity": row["bot_installation_identity"],
                "consent_policy_digest": row["consent_policy_digest"],
                "consent_policy_version": row["consent_policy_version"],
                "expert_subject_ref": row["expert_subject_ref"],
                "learner_binding_digest": row["learner_binding_digest"],
                "program_id": str(row["program_id"]),
            }
            if (
                canonical_digest(tuple_value) != row["invitation_digest"]
                or row["consent_policy_version"] != CONSENT_POLICY_VERSION
                or row["consent_policy_digest"] != CONSENT_POLICY_DIGEST
                or callback_digest != row["callback_digest"]
            ):
                raise Slice3Error("slice3_invitation_tuple_mutated")
            invitation_id = str(row["id"])
            if action == DECLINE_ACTION:
                if row["state"] == "declined":
                    return AcceptanceReceipt(
                        None, invitation_id, Slice3Disposition.DECLINED_DUPLICATE
                    )
                if row["state"] == "accepted":
                    raise Slice3Error("slice3_accepted_invitation_immutable")
                if row["expires_at"] <= now:
                    raise Slice3Error("slice3_invitation_expired")
                await session.execute(
                    text(
                        "UPDATE become_slice3_invitations SET state='declined',callback_ref=NULL,"
                        "updated_at=:now WHERE id=:id"
                    ),
                    {"id": row["id"], "now": now},
                )
                return AcceptanceReceipt(None, invitation_id, Slice3Disposition.DECLINED)
            existing = (
                (
                    await session.execute(
                        text(
                            "SELECT id,acceptance_digest FROM become_slice3_acceptances "
                            "WHERE invitation_id=:id"
                        ),
                        {"id": row["id"]},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                return AcceptanceReceipt(
                    str(existing["id"]),
                    invitation_id,
                    Slice3Disposition.ACCEPTED_DUPLICATE,
                )
            if row["state"] != "delivered" or row["expires_at"] <= now:
                raise Slice3Error("slice3_invitation_not_accepting")
            acceptance_value = {
                "invitation_digest": row["invitation_digest"],
                "learner_binding_digest": row["learner_binding_digest"],
                "program_digest": row["approved_digest"],
                "source_receipt_id": source_receipt_id,
                "consent_policy_digest": consent_policy_digest,
            }
            acceptance_digest = canonical_digest(acceptance_value)
            acceptance_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO become_slice3_acceptances "
                    "(id,invitation_id,source_receipt_id,consent_policy_version,"
                    "consent_policy_digest,acceptance_digest,accepted_at) VALUES "
                    "(:id,:invitation_id,:receipt,:version,:digest,:acceptance,:now)"
                ),
                {
                    "id": acceptance_id,
                    "invitation_id": row["id"],
                    "receipt": uuid.UUID(source_receipt_id),
                    "version": consent_policy_version,
                    "digest": consent_policy_digest,
                    "acceptance": acceptance_digest,
                    "now": now,
                },
            )
            await session.execute(
                text(
                    "UPDATE become_slice3_invitations SET state='accepted',callback_ref=NULL,"
                    "accepted_at=:now,updated_at=:now WHERE id=:id"
                ),
                {"id": row["id"], "now": now},
            )
            await _insert_delivery(
                session,
                invitation_id=row["id"],
                kind="confirmation",
                request_digest=canonical_digest(
                    {"acceptance_digest": acceptance_digest, "kind": "confirmation"}
                ),
                now=now,
            )
            command = _relations_command(row, acceptance_digest)
            command_digest = canonical_digest(command)
            outbox_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO become_slice3_relations_outbox "
                    "(id,invitation_id,acceptance_id,idempotency_key,command,command_digest,"
                    "outcome,created_at,updated_at) VALUES "
                    "(:id,:invitation_id,:acceptance_id,:key,CAST(:command AS jsonb),:digest,"
                    "'pending',:now,:now)"
                ),
                {
                    "id": outbox_id,
                    "invitation_id": row["id"],
                    "acceptance_id": acceptance_id,
                    "key": f"become-slice3:{acceptance_id}",
                    "command": __import__("json").dumps(command, separators=(",", ":")),
                    "digest": command_digest,
                    "now": now,
                },
            )
            return AcceptanceReceipt(
                str(acceptance_id),
                invitation_id,
                Slice3Disposition.ACCEPTED,
                command_digest,
            )

    async def claim_delivery(self, *, now: Any, max_sends: int) -> DeliveryJob | None:
        if max_sends < 1 or max_sends > 2:
            return None
        async with self._sessions() as session, session.begin():
            used = await session.scalar(
                text(
                    "SELECT count(*) FROM become_slice3_deliveries "
                    "WHERE outcome IN ('reserved','sent','unknown')"
                )
            )
            if int(used or 0) >= max_sends:
                return None
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT d.id,d.kind,i.id invitation_id,i.invitation_digest,"
                            "i.callback_ref,c.private_dialog_id FROM become_slice3_deliveries d "
                            "JOIN become_slice3_invitations i ON i.id=d.invitation_id "
                            "JOIN become_slice3_learner_candidates c ON c.id=i.candidate_id "
                            "WHERE d.outcome='pending' AND ((d.kind='invitation' "
                            "AND i.state='pending_delivery' AND i.expires_at>:now) "
                            "OR (d.kind='confirmation' AND i.state='accepted')) "
                            "ORDER BY d.created_at "
                            "FOR UPDATE OF d SKIP LOCKED LIMIT 1"
                        ),
                        {"now": now},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            await session.execute(
                text(
                    "UPDATE become_slice3_deliveries SET outcome='reserved',reserved_at=:now,"
                    "updated_at=:now WHERE id=:id"
                ),
                {"id": row["id"], "now": now},
            )
            return DeliveryJob(
                delivery_id=str(row["id"]),
                kind=row["kind"],
                provider_dialog_id=row["private_dialog_id"],
                invitation_id=str(row["invitation_id"]),
                invitation_digest=row["invitation_digest"],
                callback_ref=row["callback_ref"].hex if row["callback_ref"] else "0" * 32,
            )

    async def settle_delivery(
        self,
        *,
        delivery_id: str,
        outcome: str,
        telegram_message_id: str | None,
        now: Any,
    ) -> None:
        if outcome not in {"sent", "failed", "unknown"}:
            raise Slice3Error("slice3_delivery_outcome_invalid")
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        text(
                            "UPDATE become_slice3_deliveries SET outcome=:outcome,"
                            "telegram_message_id=:message_id,settled_at=:now,updated_at=:now "
                            "WHERE id=:id AND outcome='reserved' RETURNING invitation_id,kind"
                        ),
                        {
                            "id": uuid.UUID(delivery_id),
                            "outcome": outcome,
                            "message_id": telegram_message_id,
                            "now": now,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise Slice3Error("slice3_delivery_not_reserved")
            if outcome == "sent" and row["kind"] == "invitation":
                await session.execute(
                    text(
                        "UPDATE become_slice3_invitations SET state='delivered',delivered_at=:now,"
                        "updated_at=:now WHERE id=:id AND state='pending_delivery'"
                    ),
                    {"id": row["invitation_id"], "now": now},
                )

    async def claim_relations_post(self, *, now: Any, max_posts: int) -> RelationsJob | None:
        if max_posts != 1:
            return None
        async with self._sessions() as session, session.begin():
            used = await session.scalar(
                text(
                    "SELECT count(*) FROM become_slice3_relations_outbox "
                    "WHERE outcome IN ('reserved','executed','unknown')"
                )
            )
            if int(used or 0) >= 1:
                return None
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id,idempotency_key,command,command_digest FROM "
                            "become_slice3_relations_outbox "
                            "WHERE outcome='pending' ORDER BY created_at "
                            "FOR UPDATE SKIP LOCKED LIMIT 1"
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            await session.execute(
                text(
                    "UPDATE become_slice3_relations_outbox SET outcome='reserved',reserved_at=:now,"
                    "updated_at=:now WHERE id=:id"
                ),
                {"id": row["id"], "now": now},
            )
            return RelationsJob(
                str(row["id"]),
                row["idempotency_key"],
                dict(row["command"]),
                row["command_digest"],
            )

    async def settle_relations_post(
        self, *, outbox_id: str, outcome: str, receipt_digest: str | None, now: Any
    ) -> None:
        if outcome not in {"executed", "failed", "unknown"}:
            raise Slice3Error("slice3_relations_outcome_invalid")
        async with self._sessions() as session, session.begin():
            settled = (
                await session.execute(
                    text(
                        "UPDATE become_slice3_relations_outbox SET outcome=:outcome,"
                        "receipt_digest=:receipt,settled_at=:now,updated_at=:now "
                        "WHERE id=:id AND outcome='reserved' RETURNING id"
                    ),
                    {
                        "id": uuid.UUID(outbox_id),
                        "outcome": outcome,
                        "receipt": receipt_digest,
                        "now": now,
                    },
                )
            ).scalar_one_or_none()
            if settled is None:
                raise Slice3Error("slice3_relations_not_reserved")

    async def status(self, *, now: Any) -> dict[str, Any]:
        """Return bounded content-free owner state without provider identifiers."""

        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM become_slice3_learner_candidates "
                        " WHERE state='candidate') AS candidate_count,"
                        "(SELECT count(*) FROM become_slice3_pilot_slots) AS slot_count,"
                        "(SELECT count(*) FROM become_slice3_invitations) AS invitation_count,"
                        "(SELECT count(*) FROM become_slice3_invitations "
                        " WHERE state='accepted') AS accepted_count,"
                        "(SELECT count(*) FROM become_slice3_invitations "
                        " WHERE state IN ('pending_delivery','delivered') "
                        " AND expires_at<=:now) AS expiry_due_count,"
                        "(SELECT count(*) FROM become_slice3_acceptances) AS acceptance_count,"
                        "(SELECT count(*) FROM become_slice3_deliveries "
                        " WHERE outcome='unknown') AS delivery_unknown_count,"
                        "(SELECT count(*) FROM become_slice3_relations_outbox "
                        " WHERE outcome='unknown') AS relations_unknown_count,"
                        "(SELECT count(*) FROM become_slice3_relations_outbox "
                        " WHERE outcome='executed') AS relations_executed_count,"
                        "(SELECT count(*) FROM become_slice3_learner_candidates "
                        " WHERE state='tombstoned') AS candidate_tombstone_count"
                    ),
                    {"now": now},
                )
            ).mappings().one()
        return {
            "schema": "gate.become-pilot-slice3-status@1",
            **{key: int(value or 0) for key, value in row.items()},
            "content_exposed": False,
            "provider_identifiers_exposed": False,
        }

    async def rollback_preflight(self) -> dict[str, Any]:
        async with self._sessions() as session:
            accepted = int(
                await session.scalar(text("SELECT count(*) FROM become_slice3_acceptances"))
                or 0
            )
            unresolved_delivery = int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM become_slice3_deliveries "
                        "WHERE outcome IN ('reserved','unknown')"
                    )
                )
                or 0
            )
            unresolved_relations = int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM become_slice3_relations_outbox "
                        "WHERE outcome IN ('reserved','unknown')"
                    )
                )
                or 0
            )
        return {
            "schema": "gate.become-pilot-slice3-rollback-preflight@1",
            "accepted_count": accepted,
            "unresolved_delivery_count": unresolved_delivery,
            "unresolved_relations_count": unresolved_relations,
            "schema_downgrade_allowed": (
                accepted == 0
                and unresolved_delivery == 0
                and unresolved_relations == 0
            ),
            "application_rollback_allowed": (
                unresolved_delivery == 0 and unresolved_relations == 0
            ),
            "content_exposed": False,
        }

    async def purge_expired(self, *, now: Any) -> dict[str, Any]:
        """Expire callbacks and tombstone raw candidate identity after seven days."""

        async with self._sessions() as session, session.begin():
            expired = await session.execute(
                text(
                    "UPDATE become_slice3_invitations SET state='expired',"
                    "callback_ref=NULL,updated_at=:now "
                    "WHERE expires_at<=:now AND state IN ('pending_delivery','delivered') "
                    "RETURNING candidate_id"
                ),
                {"now": now},
            )
            candidate_ids = [row[0] for row in expired.all()]
            terminal_callbacks = await session.execute(
                text(
                    "UPDATE become_slice3_invitations SET callback_ref=NULL,updated_at=:now "
                    "WHERE callback_ref IS NOT NULL "
                    "AND state IN ('accepted','declined','expired') RETURNING id"
                ),
                {"now": now},
            )
            tombstoned = 0
            if candidate_ids:
                result = await session.execute(
                    text(
                        "UPDATE become_slice3_learner_candidates c SET "
                        "telegram_actor_id=NULL,private_dialog_id=NULL,"
                        "bot_installation_identity=NULL,become_space_ref=NULL,"
                        "state='tombstoned',updated_at=:now "
                        "WHERE EXISTS (SELECT 1 FROM become_slice3_invitations due "
                        "WHERE due.candidate_id=c.id AND due.state='expired' "
                        "AND due.expires_at<=:now) AND NOT EXISTS ("
                        "SELECT 1 FROM become_slice3_invitations i "
                        "JOIN become_slice3_acceptances a ON a.invitation_id=i.id "
                        "WHERE i.candidate_id=c.id) RETURNING c.id"
                    ),
                    {"now": now},
                )
                tombstoned = len(result.all())
            return {
                "schema": "gate.become-pilot-slice3-purge@1",
                "expired_invitation_count": len(candidate_ids),
                "callback_cleanup_count": len(terminal_callbacks.all()),
                "candidate_tombstone_count": tombstoned,
                "retention_days": 7,
                "content_exposed": False,
                "provider_identifiers_exposed": False,
            }


class HttpSlice3RelationsClient:
    """Strict purpose-limited Relations adapter with no fallback effects."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        endpoint: str,
        enabled: bool,
    ) -> None:
        self._http = http_client
        self._endpoint = endpoint.strip()
        self._enabled = enabled

    async def post(self, *, command: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not self._enabled or not self._endpoint:
            raise Slice3Error("slice3_relations_disabled")
        response = await self._http.post(
            self._endpoint,
            json={
                "schema": "gate.become-slice3-relations-post@1",
                "idempotency_key": idempotency_key,
                "command": command,
                "command_digest": canonical_digest(command),
            },
            headers={"Idempotency-Key": idempotency_key},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise Slice3Error("slice3_relations_receipt_invalid")
        return payload


async def _workspace_id(session: AsyncSession, key: str) -> Any:
    value = await session.scalar(text("SELECT id FROM workspaces WHERE key=:key"), {"key": key})
    if value is None:
        raise Slice3Error("slice3_workspace_missing")
    return value


async def _insert_delivery(
    session: AsyncSession,
    *,
    invitation_id: Any,
    kind: str,
    request_digest: str,
    now: Any,
) -> None:
    await session.execute(
        text(
            "INSERT INTO become_slice3_deliveries "
            "(id,invitation_id,kind,request_digest,outcome,created_at,updated_at) VALUES "
            "(:id,:invitation_id,:kind,:digest,'pending',:now,:now) ON CONFLICT DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "invitation_id": invitation_id,
            "kind": kind,
            "digest": request_digest,
            "now": now,
        },
    )


def _relations_command(row: Any, acceptance_digest: str) -> dict[str, Any]:
    return {
        "schema": "relations.become-slice3-learner-link-command@1",
        "operation": "APPEND_EXACT_PROGRAM_LEARNER_LINK",
        "invitation": {
            "invitation_ref": f"invitation://become/{row['id']}",
            "invitation_digest": row["invitation_digest"],
        },
        "program": {
            "program_ref": f"program://become/{PROGRAM_ID}",
            "version": PROGRAM_VERSION,
            "digest": PROGRAM_DIGEST,
        },
        "participants": {
            "expert": {"role": "EXPERT", "person_ref": row["expert_subject_ref"]},
            "learner": {
                "role": "LEARNER",
                "person_ref": row["subject_ref"],
                "binding_digest": row["learner_binding_digest"],
            },
        },
        "consent": {
            "policy_version": CONSENT_POLICY_VERSION,
            "policy_digest": CONSENT_POLICY_DIGEST,
            "acceptance_digest": acceptance_digest,
        },
        "scope": {
            "become_space_ref": row["become_space_ref"],
            "provider_ai_calls": 0,
            "exercise_progress_result": 0,
            "publication_payment": 0,
            "mayaki_oko_take": 0,
        },
    }
