from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.infrastructure.beta_wedding_binding import (
    BETA_WEDDING_ACTIVATION_TEXT,
    BETA_WEDDING_GROUP_TITLE,
    BETA_WEDDING_INSTALLATION_REF,
    BETA_WEDDING_SPACE_REF,
    BetaWeddingActivationCandidate,
    BetaWeddingBindingError,
    extract_beta_wedding_activation_candidate,
    extract_beta_wedding_creator,
    extract_beta_wedding_group_candidate,
    prepare_beta_wedding_binding,
)


def _activation(*, title: str = BETA_WEDDING_GROUP_TITLE, first_name: str = "Марк"):
    return {
        "update_id": 496001,
        "message": {
            "message_id": 1,
            "date": 1786040000,
            "text": BETA_WEDDING_ACTIVATION_TEXT,
            "chat": {
                "id": -100496001,
                "type": "supergroup",
                "title": title,
            },
            "from": {
                "id": 496002,
                "is_bot": False,
                "first_name": first_name,
            },
        },
    }


class BindingSession:
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, _statement: object) -> object:
        return next(self.values)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_exact_wedding_activation_returns_private_candidate():
    candidate = extract_beta_wedding_activation_candidate(_activation())

    assert candidate is not None
    assert "-100496001" not in repr(candidate)
    assert "496002" not in repr(candidate)


def test_wedding_activation_rejects_wrong_group():
    assert (
        extract_beta_wedding_activation_candidate(
            _activation(title="Зубы Кати")
        )
        is None
    )


@pytest.mark.parametrize("first_name", ["Катя", "", "   "])
def test_wedding_activation_rejects_wrong_actor(first_name: str):
    assert (
        extract_beta_wedding_activation_candidate(
            _activation(first_name=first_name)
        )
        is None
    )


def test_wedding_activation_rejects_near_match_text():
    payload = _activation()
    payload["message"]["text"] = BETA_WEDDING_ACTIVATION_TEXT.rstrip(".")

    assert extract_beta_wedding_activation_candidate(payload) is None


def test_wedding_activation_rejects_bot_sender():
    payload = _activation()
    payload["message"]["from"]["is_bot"] = True

    assert extract_beta_wedding_activation_candidate(payload) is None


@pytest.mark.anyio
async def test_wedding_binding_is_created_as_owner_consented():
    workspace = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001")
    )
    connector = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000002")
    )
    session = BindingSession([workspace, connector, None, None])
    candidate = BetaWeddingActivationCandidate("-100496001", "496002")
    now = datetime(2026, 8, 6, 18, 50, tzinfo=UTC)

    result = await prepare_beta_wedding_binding(
        session,  # type: ignore[arg-type]
        workspace_key="workspace",
        now=now,
        candidate=candidate,
    )

    assert result == "created"
    assert session.commits == 1
    assert len(session.added) == 1
    binding = session.added[0]
    assert binding.space_ref == BETA_WEDDING_SPACE_REF
    assert binding.installation_ref == BETA_WEDDING_INSTALLATION_REF
    assert binding.state == "consented"
    assert binding.ready_at == now
    assert binding.consented_at == now


@pytest.mark.anyio
async def test_wedding_binding_exact_retry_is_idempotent():
    workspace = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001")
    )
    connector = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000002")
    )
    existing = SimpleNamespace(
        state="consented",
        telegram_chat_id="-100496001",
        subject_telegram_user_id="496002",
    )
    session = BindingSession([workspace, connector, existing])

    result = await prepare_beta_wedding_binding(
        session,  # type: ignore[arg-type]
        workspace_key="workspace",
        now=datetime(2026, 8, 6, 18, 50, tzinfo=UTC),
        candidate=BetaWeddingActivationCandidate("-100496001", "496002"),
    )

    assert result == "already_bound"
    assert session.added == []
    assert session.commits == 0


@pytest.mark.anyio
async def test_wedding_binding_conflicting_retry_fails_closed():
    workspace = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001")
    )
    connector = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000002")
    )
    existing = SimpleNamespace(
        state="consented",
        telegram_chat_id="-100496001",
        subject_telegram_user_id="496999",
    )
    session = BindingSession([workspace, connector, existing])

    with pytest.raises(
        BetaWeddingBindingError,
        match="beta_wedding_binding_conflict",
    ):
        await prepare_beta_wedding_binding(
            session,  # type: ignore[arg-type]
            workspace_key="workspace",
            now=datetime(2026, 8, 6, 18, 50, tzinfo=UTC),
            candidate=BetaWeddingActivationCandidate("-100496001", "496002"),
        )

    assert session.added == []
    assert session.commits == 0


def test_wedding_membership_selects_exact_group_without_exposing_chat() -> None:
    candidate = extract_beta_wedding_group_candidate(
        payload={
            "my_chat_member": {
                "chat": {
                    "id": -100496001,
                    "type": "supergroup",
                    "title": BETA_WEDDING_GROUP_TITLE,
                },
                "new_chat_member": {
                    "status": "administrator",
                    "user": {"id": 496003, "is_bot": True},
                },
            }
        },
        received_at=datetime(2026, 8, 6, 19, 0, tzinfo=UTC),
    )

    assert candidate is not None
    assert "-100496001" not in repr(candidate)


def test_wedding_membership_rejects_other_group() -> None:
    candidate = extract_beta_wedding_group_candidate(
        payload={
            "my_chat_member": {
                "chat": {
                    "id": -100496001,
                    "type": "supergroup",
                    "title": "Зубы Кати",
                },
                "new_chat_member": {
                    "status": "member",
                    "user": {"id": 496003, "is_bot": True},
                },
            }
        },
        received_at=datetime(2026, 8, 6, 19, 0, tzinfo=UTC),
    )

    assert candidate is None


def test_wedding_creator_accepts_only_one_exact_mark() -> None:
    creator = extract_beta_wedding_creator(
        [
            {
                "status": "creator",
                "user": {
                    "id": 496002,
                    "is_bot": False,
                    "first_name": "Марк",
                },
            },
            {
                "status": "administrator",
                "user": {
                    "id": 496004,
                    "is_bot": False,
                    "first_name": "Марк-Маяк",
                },
            },
        ]
    )

    assert creator == "496002"


def test_wedding_creator_rejects_mayak_as_source() -> None:
    creator = extract_beta_wedding_creator(
        [
            {
                "status": "creator",
                "user": {
                    "id": 496004,
                    "is_bot": False,
                    "first_name": "Марк-Маяк",
                },
            }
        ]
    )

    assert creator is None


def test_wedding_message_selects_exact_group_without_membership_receipt() -> None:
    candidate = extract_beta_wedding_group_candidate(
        payload={
            "message": {
                "message_id": 2,
                "chat": {
                    "id": -100496001,
                    "type": "supergroup",
                    "title": BETA_WEDDING_GROUP_TITLE,
                },
                "from": {
                    "id": 496002,
                    "is_bot": False,
                    "first_name": "Марк",
                },
                "text": "Первая вводная по свадьбе.",
            }
        },
        received_at=datetime(2026, 8, 6, 19, 5, tzinfo=UTC),
    )

    assert candidate is not None
    assert "-100496001" not in repr(candidate)
