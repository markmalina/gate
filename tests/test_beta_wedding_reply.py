from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from app.application.beta_wedding_reply import (
    BetaWeddingContextMessage,
    BetaWeddingReplyDisposition,
    BetaWeddingReplyReservation,
    RespondToBetaWeddingMessage,
)
from app.infrastructure.beta_wedding_reply import (
    OpenAIResponsesBetaWeddingModel,
    SqlBetaWeddingReplyStore,
    _query_terms,
    _select_context,
)


class FakeStore:
    def __init__(
        self,
        reservation: BetaWeddingReplyReservation | None,
    ) -> None:
        self.reservation = reservation
        self.staged: list[tuple[str, str, str]] = []
        self.settled: list[tuple[str, str, str | None, str | None]] = []

    async def reserve_reply(
        self, *, receipt_id: str, now: datetime
    ) -> BetaWeddingReplyReservation | None:
        del receipt_id, now
        return self.reservation

    async def stage_candidate(
        self,
        *,
        reply_id: str,
        text: str,
        result_code: str,
        now: datetime,
    ) -> bool:
        del now
        self.staged.append((reply_id, text, result_code))
        return True

    async def settle_reply(
        self,
        *,
        reply_id: str,
        outcome: str,
        telegram_message_id: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None:
        del now
        self.settled.append(
            (reply_id, outcome, telegram_message_id, error_code)
        )


class FakeModel:
    def __init__(
        self,
        response: str = "Приняла. Площадка нужна на 40 гостей.",
        *,
        fails: bool = False,
    ) -> None:
        self.response = response
        self.fails = fails
        self.calls: list[
            tuple[
                BetaWeddingContextMessage,
                tuple[BetaWeddingContextMessage, ...],
            ]
        ] = []

    async def propose(
        self,
        *,
        current: BetaWeddingContextMessage,
        context: tuple[BetaWeddingContextMessage, ...],
    ) -> str:
        self.calls.append((current, context))
        if self.fails:
            raise RuntimeError("provider unavailable")
        return self.response


class FakeTelegram:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.messages: list[tuple[str, str]] = []

    async def send_plain_message(
        self, *, chat_id: str, text: str
    ) -> dict[str, object]:
        self.messages.append((chat_id, text))
        if self.fails:
            raise RuntimeError("ambiguous delivery")
        return {"ok": True, "result": {"message_id": 77}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _reservation(
    *,
    media_kind: str = "text",
    current_text: str | None = "Нужна площадка на 40 гостей.",
    created: bool = True,
) -> BetaWeddingReplyReservation:
    return BetaWeddingReplyReservation(
        created=created,
        reply_id="00000000-0000-0000-0000-000000000501",
        telegram_chat_id="-100496001",
        media_kind=media_kind,
        sender_label="Марк",
        current_text=current_text,
        context=(
            BetaWeddingContextMessage(
                sender_label="Марк-Маяк",
                text="Свадьба планируется в Минске.",
            ),
        ),
    )


@pytest.mark.anyio
async def test_text_event_gets_one_contextual_reply() -> None:
    store = FakeStore(_reservation())
    model = FakeModel()
    telegram = FakeTelegram()

    result = await RespondToBetaWeddingMessage(
        store=store,
        model=model,
        telegram=telegram,
    ).execute(
        receipt_id="00000000-0000-0000-0000-000000000401",
        now=datetime(2026, 8, 7, 7, 0, tzinfo=UTC),
    )

    assert result.disposition is BetaWeddingReplyDisposition.SENT
    assert len(model.calls) == 1
    assert model.calls[0][0].text == "Нужна площадка на 40 гостей."
    assert model.calls[0][1][0].text == "Свадьба планируется в Минске."
    assert telegram.messages == [
        ("-100496001", "Приняла. Площадка нужна на 40 гостей.")
    ]
    assert store.settled == [
        (
            "00000000-0000-0000-0000-000000000501",
            "sent",
            "77",
            None,
        )
    ]


@pytest.mark.anyio
async def test_media_event_is_always_acknowledged_without_model() -> None:
    store = FakeStore(
        _reservation(media_kind="photo", current_text=None)
    )
    model = FakeModel()
    telegram = FakeTelegram()

    result = await RespondToBetaWeddingMessage(
        store=store,
        model=model,
        telegram=telegram,
    ).execute(
        receipt_id="00000000-0000-0000-0000-000000000402",
        now=datetime(2026, 8, 7, 7, 1, tzinfo=UTC),
    )

    assert result.disposition is BetaWeddingReplyDisposition.SENT
    assert model.calls == []
    assert "Приняла фотографию" in telegram.messages[0][1]


@pytest.mark.anyio
async def test_duplicate_event_creates_no_model_call_or_send() -> None:
    store = FakeStore(_reservation(created=False))
    model = FakeModel()
    telegram = FakeTelegram()

    result = await RespondToBetaWeddingMessage(
        store=store,
        model=model,
        telegram=telegram,
    ).execute(
        receipt_id="00000000-0000-0000-0000-000000000403",
        now=datetime(2026, 8, 7, 7, 2, tzinfo=UTC),
    )

    assert result.disposition is BetaWeddingReplyDisposition.DUPLICATE
    assert model.calls == []
    assert telegram.messages == []
    assert store.staged == []


@pytest.mark.anyio
async def test_model_failure_still_sends_bounded_acceptance() -> None:
    store = FakeStore(_reservation())
    model = FakeModel(fails=True)
    telegram = FakeTelegram()

    result = await RespondToBetaWeddingMessage(
        store=store,
        model=model,
        telegram=telegram,
    ).execute(
        receipt_id="00000000-0000-0000-0000-000000000404",
        now=datetime(2026, 8, 7, 7, 3, tzinfo=UTC),
    )

    assert result.disposition is BetaWeddingReplyDisposition.SENT
    assert telegram.messages[0][1].startswith(
        "Я поняла так:"
    )
    assert store.staged[0][2] == "beta_wedding_model_fallback"


@pytest.mark.anyio
async def test_ambiguous_send_is_terminal_unknown() -> None:
    store = FakeStore(_reservation())
    telegram = FakeTelegram(fails=True)

    result = await RespondToBetaWeddingMessage(
        store=store,
        model=FakeModel(),
        telegram=telegram,
    ).execute(
        receipt_id="00000000-0000-0000-0000-000000000405",
        now=datetime(2026, 8, 7, 7, 4, tzinfo=UTC),
    )

    assert result.disposition is BetaWeddingReplyDisposition.UNKNOWN
    assert store.settled == [
        (
            "00000000-0000-0000-0000-000000000501",
            "unknown",
            None,
            "beta_wedding_delivery_unknown",
        )
    ]



def _context_row(
    index: int,
    text: str,
    *,
    event_id: str | None = None,
    chat_id: str = "-100496001",
) -> tuple[object, object]:
    event = SimpleNamespace(id=event_id or f"event-{index:03d}")
    receipt = SimpleNamespace(
        payload={
            "message": {
                "chat": {"id": chat_id},
                "from": {
                    "is_bot": False,
                    "first_name": "Участник",
                },
                "text": text,
            }
        }
    )
    return event, receipt


def test_full_space_retrieval_keeps_recent_and_selects_old_relevant_fact() -> None:
    rows = [
        _context_row(index, f"Недавняя заметка {index}")
        for index in range(8)
    ]
    rows.extend(
        _context_row(index, f"Старая нерелевантная заметка {index}")
        for index in range(8, 25)
    )
    rows.append(
        _context_row(
            25,
            "Бюджет площадки утверждён: 120 000 рублей.",
        )
    )

    context = _select_context(
        rows,
        current_text="Какой бюджет площадки мы утвердили?",
        expected_chat_id="-100496001",
    )

    texts = [item.text for item in context]
    assert "Бюджет площадки утверждён: 120 000 рублей." in texts
    assert "Старая нерелевантная заметка 24" not in texts
    assert all(f"Недавняя заметка {index}" in texts for index in range(8))
    assert len(texts) == 9


def test_full_space_retrieval_deduplicates_recent_and_relevant_identity() -> None:
    rows = [
        _context_row(0, "Площадка рядом с озером.", event_id="same-event"),
        _context_row(1, "Свежая заметка"),
    ]
    rows.extend(
        _context_row(index, f"Ещё заметка {index}")
        for index in range(2, 8)
    )
    rows.append(
        _context_row(
            8,
            "Площадка рядом с озером.",
            event_id="same-event",
        )
    )

    context = _select_context(
        rows,
        current_text="Какая площадка рядом с озером?",
        expected_chat_id="-100496001",
    )

    assert sum(item.text == "Площадка рядом с озером." for item in context) == 1


def test_full_space_retrieval_stopword_query_uses_recent_only() -> None:
    rows = [
        _context_row(index, f"Недавняя заметка {index}")
        for index in range(8)
    ]
    rows.append(_context_row(8, "Старый бюджет площадки"))

    context = _select_context(
        rows,
        current_text="Что это и как?",
        expected_chat_id="-100496001",
    )

    assert _query_terms("Что это и как?") == frozenset()
    assert len(context) == 8
    assert all("Недавняя заметка" in item.text for item in context)


def test_full_space_retrieval_is_chat_scoped_and_prompt_bounded() -> None:
    rows = [
        _context_row(0, "а" * 13_000),
        _context_row(
            1,
            "Бюджет другого Space — 999.",
            chat_id="-100999999",
        ),
    ]

    context = _select_context(
        rows,
        current_text="Какой бюджет?",
        expected_chat_id="-100496001",
    )

    assert sum(len(item.text) for item in context) == 12_000
    assert "999" not in "".join(item.text for item in context)


def test_full_space_retrieval_source_keeps_exact_dual_scope() -> None:
    import inspect

    source = inspect.getsource(SqlBetaWeddingReplyStore._context)

    assert "BetaSpaceEventModel.workspace_id == binding.workspace_id" in source
    assert "BetaSpaceEventModel.binding_id == binding.id" in source
    assert ".limit(_MAX_FULL_SPACE_CANDIDATES)" in source


@pytest.mark.anyio
async def test_openai_request_contains_identity_and_bounded_group_memory() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.read() and __import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": (
                    '{"text":"Я Бета — помощница по сбору и '
                    'структурированию данных."}'
                ),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        model = OpenAIResponsesBetaWeddingModel(
            http_client=client,
            api_key="fixture-key",
            api_base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            max_output_tokens=512,
            timeout_seconds=30,
        )
        result = await model.propose(
            current=BetaWeddingContextMessage(
                sender_label="Марк",
                text="Бета, кто ты?",
            ),
            context=(
                BetaWeddingContextMessage(
                    sender_label="Марк-Маяк",
                    text="Собираем свадьбу в Минске.",
                ),
            ),
        )

    assert result.startswith("Я Бета")
    assert captured["store"] is False
    assert captured["tools"] == []
    assert "общая помощница" in str(captured["instructions"])
    assert "Собираем свадьбу в Минске." in str(captured["input"])
