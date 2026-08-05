from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from app.application.become_learner_invitation import (
    CONSENT_POLICY_DIGEST,
    CONSENT_POLICY_VERSION,
    CONSENT_TEXT,
    PROGRAM_DIGEST,
    RELATIONS_EVENT,
    RELATIONS_RECEIPT_SCHEMA,
    AcceptanceReceipt,
    CandidateBinding,
    DeliverSlice3,
    DeliveryJob,
    InvitationReceipt,
    PinInvitationCommand,
    PostSlice3Relations,
    ProcessSlice3Update,
    RelationsJob,
    Slice3Disposition,
    Slice3Error,
    canonical_digest,
    delivery_body,
    learner_binding_digest,
    parse_slice3_update,
    validate_pin_command,
)

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
SPACE = "space://become/quest-owner-pilot"
INSTALLATION = "installation://telegram/BecomeNowBot"


class MemoryStore:
    def __init__(self) -> None:
        self.candidates: dict[str, CandidateBinding] = {}
        self.selected: str | None = None
        self.invitation: InvitationReceipt | None = None
        self.invitation_actor: tuple[str, str] | None = None
        self.state = "delivered"
        self.acceptance: AcceptanceReceipt | None = None

    async def register_candidate(self, **kw):
        digest = kw["learner_binding_digest"]
        found = self.candidates.get(digest)
        if found:
            return replace(found, replayed=True)
        result = CandidateBinding(
            candidate_id=f"candidate-{len(self.candidates) + 1}",
            subject_ref=kw["subject_ref"],
            learner_binding_digest=digest,
            provider_dialog_id=kw["private_dialog_id"],
        )
        self.candidates[digest] = result
        return result

    async def pin_and_create_invitation(self, *, command, now):
        validate_pin_command(command)
        candidate = self.candidates.get(command.learner_binding_digest)
        if candidate is None:
            raise Slice3Error("slice3_candidate_unpinned_or_missing")
        if candidate.subject_ref == command.expert_subject_ref:
            raise Slice3Error("slice3_participants_not_distinct")
        if self.selected not in {None, command.learner_binding_digest}:
            raise Slice3Error("slice3_pilot_slot_occupied")
        self.selected = command.learner_binding_digest
        if self.invitation:
            return replace(self.invitation, replayed=True)
        invitation_digest = canonical_digest(
            {
                "program": PROGRAM_DIGEST,
                "learner": command.learner_binding_digest,
                "expert": command.expert_subject_ref,
                "space": command.become_space_ref,
            }
        )
        self.invitation = InvitationReceipt(
            "invitation-1", invitation_digest, command.learner_binding_digest, False
        )
        self.invitation_actor = (candidate.subject_ref, candidate.provider_dialog_id)
        return self.invitation

    async def apply_learner_callback(self, **kw):
        if self.invitation is None or self.invitation_actor != (
            kw["subject_ref"],
            kw["private_dialog_id"],
        ):
            raise Slice3Error("slice3_acceptance_actor_mismatch")
        if (
            kw["consent_policy_version"] != CONSENT_POLICY_VERSION
            or kw["consent_policy_digest"] != CONSENT_POLICY_DIGEST
        ):
            raise Slice3Error("slice3_consent_policy_mismatch")
        if kw["action"] == "d":
            if self.state == "accepted":
                raise Slice3Error("slice3_accepted_invitation_immutable")
            duplicate = self.state == "declined"
            self.state = "declined"
            return AcceptanceReceipt(
                None,
                self.invitation.invitation_id,
                Slice3Disposition.DECLINED_DUPLICATE
                if duplicate
                else Slice3Disposition.DECLINED,
            )
        if self.state == "declined":
            raise Slice3Error("slice3_invitation_not_accepting")
        if self.acceptance:
            return replace(
                self.acceptance, disposition=Slice3Disposition.ACCEPTED_DUPLICATE
            )
        self.state = "accepted"
        self.acceptance = AcceptanceReceipt(
            "acceptance-1",
            self.invitation.invitation_id,
            Slice3Disposition.ACCEPTED,
            "f" * 64,
        )
        return self.acceptance

    async def claim_delivery(self, **kw):
        return None

    async def settle_delivery(self, **kw):
        return None

    async def claim_relations_post(self, **kw):
        return None

    async def settle_relations_post(self, **kw):
        return None


def update_message(
    actor: int, dialog: int, chat_type: str, text: str, update_id: int = 1
):
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": actor, "is_bot": False, "username": "not-authority"},
            "chat": {"id": dialog, "type": chat_type},
            "text": text,
        },
    }


def callback(actor: int, dialog: int, data: str, update_id: int = 2):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": actor, "is_bot": False},
            "message": {"chat": {"id": dialog, "type": "private"}},
            "data": data,
        },
    }


def processor(store: MemoryStore, *, enabled: bool = True, data: bool = True):
    return ProcessSlice3Update(
        store=store,
        identity_pepper="test-pepper",
        workspace_key="mark-personal",
        become_space_ref=SPACE,
        bot_installation_identity=INSTALLATION,
        enabled=enabled,
        data_gate_approved=data,
    )


def test_literal_contract_values_and_consent_scope():
    assert (
        PROGRAM_DIGEST
        == "710c580c210fa687af57962bc1f5011dbc88341f20ee1b9d3ae83ad352d0581d"
    )
    assert CONSENT_POLICY_VERSION == "become.slice3.learner-link-consent.ru@1"
    assert sha256(CONSENT_TEXT.encode()).hexdigest() == CONSENT_POLICY_DIGEST
    forbidden = (
        "упражнен",
        "прогресс",
        "результат",
        "публикац",
        "платеж",
        "MAYAKI",
        "Oko",
        "TAKE",
    )
    assert all(value not in CONSENT_TEXT for value in forbidden)


def test_candidate_digest_exact_tuple_and_identity_fields_only():
    base = dict(
        telegram_actor_id="20",
        private_dialog_id="20",
        bot_installation_identity=INSTALLATION,
        become_space_ref=SPACE,
    )
    digest = learner_binding_digest(**base)
    assert digest != learner_binding_digest(**{**base, "telegram_actor_id": "21"})
    assert digest != learner_binding_digest(**{**base, "private_dialog_id": "22"})
    assert digest != learner_binding_digest(
        **{**base, "bot_installation_identity": "other"}
    )
    assert digest != learner_binding_digest(**{**base, "become_space_ref": "other"})


def test_default_off_preflight_creates_nothing():
    store = MemoryStore()
    with pytest.raises(Slice3Error, match="slice3_disabled"):
        asyncio.run(
            processor(store, enabled=False).execute(
                update=update_message(20, 20, "private", "/become learner"),
                source_receipt_id="00000000-0000-0000-0000-000000000001",
                now=NOW,
            )
        )
    assert (
        store.candidates == {} and store.selected is None and store.invitation is None
    )


def test_preflight_does_not_occupy_slot_and_foreign_candidate_cannot_capture_it():
    store = MemoryStore()
    p = processor(store)
    first = asyncio.run(
        p.execute(
            update=update_message(20, 20, "private", "/become learner"),
            source_receipt_id="00000000-0000-0000-0000-000000000001",
            now=NOW,
        )
    )
    asyncio.run(
        p.execute(
            update=update_message(30, 30, "private", "/become learner", 2),
            source_receipt_id="00000000-0000-0000-0000-000000000002",
            now=NOW,
        )
    )
    assert first is Slice3Disposition.CANDIDATE_REGISTERED
    assert (
        len(store.candidates) == 2
        and store.selected is None
        and store.invitation is None
    )


def test_owner_exact_digest_pins_once_and_other_digest_fails():
    store = MemoryStore()
    p = processor(store)
    for actor in (20, 30):
        asyncio.run(
            p.execute(
                update=update_message(
                    actor, actor, "private", "/become learner", actor
                ),
                source_receipt_id=f"00000000-0000-0000-0000-{actor:012d}",
                now=NOW,
            )
        )
    selected = next(iter(store.candidates))
    command = update_message(10, -100, "group", f"/become invite {selected}", 40)
    assert (
        asyncio.run(
            p.execute(
                update=command,
                source_receipt_id="00000000-0000-0000-0000-000000000040",
                now=NOW,
            )
        )
        is Slice3Disposition.INVITATION_CREATED
    )
    assert (
        asyncio.run(
            p.execute(
                update=command,
                source_receipt_id="00000000-0000-0000-0000-000000000040",
                now=NOW,
            )
        )
        is Slice3Disposition.INVITATION_REPLAYED
    )
    other = list(store.candidates)[1]
    with pytest.raises(Slice3Error, match="slice3_pilot_slot_occupied"):
        asyncio.run(
            p.execute(
                update=update_message(10, -100, "group", f"/become invite {other}", 41),
                source_receipt_id="00000000-0000-0000-0000-000000000041",
                now=NOW,
            )
        )


def test_unpinned_and_tampered_digest_fail_closed():
    store = MemoryStore()
    p = processor(store)
    unknown = "e" * 64
    with pytest.raises(Slice3Error, match="candidate_unpinned_or_missing"):
        asyncio.run(
            p.execute(
                update=update_message(10, -100, "group", f"/become invite {unknown}"),
                source_receipt_id="00000000-0000-0000-0000-000000000001",
                now=NOW,
            )
        )
    with pytest.raises(Slice3Error, match="learner_binding_digest_invalid"):
        parse_slice3_update(update_message(10, -100, "group", "/become invite nope"))


def test_foreign_actor_and_other_dialog_cannot_accept_then_exact_accept_is_idempotent():
    store = MemoryStore()
    p = processor(store)
    asyncio.run(
        p.execute(
            update=update_message(20, 20, "private", "/become learner"),
            source_receipt_id="00000000-0000-0000-0000-000000000001",
            now=NOW,
        )
    )
    digest = next(iter(store.candidates))
    asyncio.run(
        p.execute(
            update=update_message(10, -100, "group", f"/become invite {digest}", 2),
            source_receipt_id="00000000-0000-0000-0000-000000000002",
            now=NOW,
        )
    )
    for actor, dialog in ((21, 20), (20, 21)):
        with pytest.raises(Slice3Error, match="acceptance_actor_mismatch"):
            asyncio.run(
                p.execute(
                    update=callback(actor, dialog, "b3:a:" + "1" * 32, 3),
                    source_receipt_id="00000000-0000-0000-0000-000000000003",
                    now=NOW,
                )
            )
    exact = callback(20, 20, "b3:a:" + "1" * 32, 4)
    assert (
        asyncio.run(
            p.execute(
                update=exact,
                source_receipt_id="00000000-0000-0000-0000-000000000004",
                now=NOW,
            )
        )
        is Slice3Disposition.ACCEPTED
    )
    assert (
        asyncio.run(
            p.execute(
                update=exact,
                source_receipt_id="00000000-0000-0000-0000-000000000004",
                now=NOW,
            )
        )
        is Slice3Disposition.ACCEPTED_DUPLICATE
    )


def test_decline_is_terminal_and_callback_is_private_only():
    store = MemoryStore()
    p = processor(store)
    asyncio.run(
        p.execute(
            update=update_message(20, 20, "private", "/become learner"),
            source_receipt_id="00000000-0000-0000-0000-000000000001",
            now=NOW,
        )
    )
    digest = next(iter(store.candidates))
    asyncio.run(
        p.execute(
            update=update_message(10, -100, "group", f"/become invite {digest}", 2),
            source_receipt_id="00000000-0000-0000-0000-000000000002",
            now=NOW,
        )
    )
    assert (
        asyncio.run(
            p.execute(
                update=callback(20, 20, "b3:d:" + "1" * 32, 3),
                source_receipt_id="00000000-0000-0000-0000-000000000003",
                now=NOW,
            )
        )
        is Slice3Disposition.DECLINED
    )
    with pytest.raises(Slice3Error, match="invitation_not_accepting"):
        asyncio.run(
            p.execute(
                update=callback(20, 20, "b3:a:" + "1" * 32, 4),
                source_receipt_id="00000000-0000-0000-0000-000000000004",
                now=NOW,
            )
        )


def test_pin_command_rejects_any_program_or_consent_mutation():
    base = PinInvitationCommand(
        workspace_key="mark-personal",
        source_receipt_id="receipt",
        expert_subject_ref="subject://expert",
        expert_dialog_id="-100",
        learner_binding_digest="a" * 64,
        become_space_ref=SPACE,
        bot_installation_identity=INSTALLATION,
    )
    validate_pin_command(base)
    for mutated in (
        replace(base, approved_digest="b" * 64),
        replace(base, approved_version=3),
        replace(base, consent_policy_digest="b" * 64),
    ):
        with pytest.raises(Slice3Error):
            validate_pin_command(mutated)


def test_invitation_card_has_only_explicit_accept_and_decline():
    job = DeliveryJob("delivery", "invitation", "20", "invitation", "a" * 64, "1" * 32)
    text, markup = delivery_body(job)
    assert text == CONSENT_TEXT
    buttons = markup["inline_keyboard"][0]
    assert [button["text"] for button in buttons] == ["Принять", "Отказаться"]
    assert [button["callback_data"].split(":")[1] for button in buttons] == ["a", "d"]


def test_two_candidates_race_for_one_slot_and_only_one_invitation_wins():
    store = MemoryStore()
    p = processor(store)
    for actor in (20, 30):
        asyncio.run(
            p.execute(
                update=update_message(
                    actor, actor, "private", "/become learner", actor
                ),
                source_receipt_id=f"00000000-0000-0000-0000-{actor:012d}",
                now=NOW,
            )
        )
    first, second = list(store.candidates)

    async def race():
        return await asyncio.gather(
            p.execute(
                update=update_message(10, -100, "group", f"/become invite {first}", 50),
                source_receipt_id="00000000-0000-0000-0000-000000000050",
                now=NOW,
            ),
            p.execute(
                update=update_message(
                    10, -100, "group", f"/become invite {second}", 51
                ),
                source_receipt_id="00000000-0000-0000-0000-000000000051",
                now=NOW,
            ),
            return_exceptions=True,
        )

    outcomes = asyncio.run(race())
    assert sum(value is Slice3Disposition.INVITATION_CREATED for value in outcomes) == 1
    assert sum(isinstance(value, Slice3Error) for value in outcomes) == 1
    assert store.invitation is not None


class EffectStore:
    def __init__(self) -> None:
        self.delivery = DeliveryJob(
            "delivery-1", "confirmation", "20", "invitation-1", "a" * 64, "0" * 32
        )
        command = {
            "schema": "relations.become-slice3-learner-link-command@1",
            "operation": "APPEND_EXACT_PROGRAM_LEARNER_LINK",
            "invitation": {
                "invitation_ref": "invitation://become/invitation-1",
                "invitation_digest": "a" * 64,
            },
            "program": {
                "program_ref": "program://become/60a45dfd-ffe8-4576-acff-cf75cf0c3c9c",
                "version": 2,
                "digest": PROGRAM_DIGEST,
            },
            "participants": {
                "expert": {"role": "EXPERT", "person_ref": "subject://expert"},
                "learner": {
                    "role": "LEARNER",
                    "person_ref": "subject://learner",
                    "binding_digest": "b" * 64,
                },
            },
            "consent": {
                "policy_version": CONSENT_POLICY_VERSION,
                "policy_digest": CONSENT_POLICY_DIGEST,
                "acceptance_digest": "c" * 64,
            },
            "scope": {
                "become_space_ref": SPACE,
                "provider_ai_calls": 0,
                "exercise_progress_result": 0,
                "publication_payment": 0,
                "mayaki_oko_take": 0,
            },
        }
        self.relations = RelationsJob(
            "outbox-1", "idem://slice3/1", command, canonical_digest(command)
        )
        self.delivery_settled = 0
        self.relations_settled = 0

    async def claim_delivery(self, **kw):
        job, self.delivery = self.delivery, None
        return job

    async def settle_delivery(self, **kw):
        self.delivery_settled += 1

    async def claim_relations_post(self, **kw):
        job, self.relations = self.relations, None
        return job

    async def settle_relations_post(self, **kw):
        self.relations_settled += 1


class FakeTelegram:
    def __init__(self) -> None:
        self.sends = 0

    async def send_message(self, **kw):
        self.sends += 1
        return {"ok": True, "result": {"message_id": 1}}


class FakeRelations:
    def __init__(self, mutation: dict[str, object] | None = None) -> None:
        self.posts = 0
        self.mutation = mutation or {}

    async def post(self, *, command, idempotency_key):
        self.posts += 1
        unsigned = {
            "schema": RELATIONS_RECEIPT_SCHEMA,
            "service": "relations",
            "source_sha": "1" * 40,
            "idempotency_key": idempotency_key,
            "command_digest": canonical_digest(command),
            "event": RELATIONS_EVENT,
            "invitation_ref": command["invitation"]["invitation_ref"],
            "invitation_digest": command["invitation"]["invitation_digest"],
            "program_ref": command["program"]["program_ref"],
            "program_version": command["program"]["version"],
            "program_digest": command["program"]["digest"],
            "expert_person_ref": command["participants"]["expert"]["person_ref"],
            "learner_person_ref": command["participants"]["learner"]["person_ref"],
            "learner_binding_digest": command["participants"]["learner"]["binding_digest"],
            "become_space_ref": command["scope"]["become_space_ref"],
            "consent_policy_version": command["consent"]["policy_version"],
            "consent_policy_digest": command["consent"]["policy_digest"],
            "acceptance_digest": command["consent"]["acceptance_digest"],
            "status": "executed",
            "domain_events": 1,
            "external_effects": 0,
            "provider_calls": 0,
            "messages_or_publications": 0,
            "exercises": 0,
            "progress_or_results": 0,
            "payments": 0,
            "mayaki_oko_take_effects": 0,
            "contains_provider_identifiers": False,
        }
        unsigned.update(self.mutation)
        return {**unsigned, "receipt_digest": canonical_digest(unsigned)}


def test_delivery_and_relations_ledgers_prevent_duplicate_effects():
    store = EffectStore()
    telegram = FakeTelegram()
    relations = FakeRelations()
    delivery = DeliverSlice3(store=store, telegram=telegram, enabled=True, max_sends=2)
    post = PostSlice3Relations(
        store=store, relations=relations, enabled=True, max_posts=1
    )
    assert asyncio.run(delivery.run_once(now=NOW)) == "sent"
    assert asyncio.run(delivery.run_once(now=NOW)) == "idle"
    assert asyncio.run(post.run_once(now=NOW)) == "executed"
    assert asyncio.run(post.run_once(now=NOW)) == "idle"
    assert telegram.sends == 1
    assert relations.posts == 1
    assert store.delivery_settled == 1
    assert store.relations_settled == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "relations.wrong@1"),
        ("status", "accepted"),
        ("idempotency_key", "idem://other"),
        ("command_digest", "d" * 64),
        ("invitation_digest", "d" * 64),
        ("program_digest", "d" * 64),
        ("program_version", 3),
        ("expert_person_ref", "subject://other"),
        ("learner_person_ref", "subject://other"),
        ("learner_binding_digest", "d" * 64),
        ("consent_policy_digest", "d" * 64),
        ("external_effects", 1),
        ("provider_calls", 1),
    ],
)
def test_relations_receipt_binding_mismatch_never_executes_outbox(field, value):
    store = EffectStore()
    relations = FakeRelations({field: value})
    post = PostSlice3Relations(
        store=store, relations=relations, enabled=True, max_posts=1
    )
    assert asyncio.run(post.run_once(now=NOW)) == "unknown"
    assert relations.posts == 1
    assert store.relations_settled == 1
