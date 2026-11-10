"""Boundary checks of decoded immutable artifacts before any SQL effect."""

import base64
import hashlib
import io
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.reply_drafts import ReplyDraftError
from app.application.reply_media import (
    PreparedReplyMedia,
    PrepareReplyMediaCommand,
    PrepareReplyMediaService,
    ReplyMediaInput,
    ValidatedMediaItem,
)
from app.core.mcp_security import AccessScope, Principal
from PIL import Image


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def image_bytes(
    *,
    fmt: str = "PNG",
    size: tuple[int, int] = (32, 24),
    mode: str = "RGB",
    orientation: int | None = None,
) -> bytes:
    image = Image.new(mode, size, color=0)
    output = io.BytesIO()
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        exif[315] = "Private author"
        options["exif"] = exif
    image.save(output, format=fmt, **options)
    return output.getvalue()


def media_input(data: bytes | None = None, *, jpeg: bool = False, **kwargs) -> ReplyMediaInput:
    data = data if data is not None else image_bytes(fmt="JPEG" if jpeg else "PNG")
    return ReplyMediaInput(
        filename="image.jpg" if jpeg else "image.png",
        media_type="image/jpeg" if jpeg else "image/png",
        content_base64=base64.b64encode(data).decode("ascii"),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        **kwargs,
    )


def principal() -> Principal:
    return Principal(
        subject="test-subject",
        auth_method="oauth2",
        scopes=frozenset({"gate:drafts:write"}),
        access_scope=AccessScope(
            workspace_id="workspace-test",
            account_ids=frozenset({uuid4()}),
            project_ids=frozenset({uuid4()}),
        ),
    )


def command(*items: ReplyMediaInput, mode: str = "photo") -> PrepareReplyMediaCommand:
    return PrepareReplyMediaCommand(
        principal=principal(),
        task_id=uuid4(),
        mode=mode,
        items=items or (media_input(),),
        staging_key="stage-1",
        execution_id="exec-742-synthetic",
        execution_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


class CapturingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[ValidatedMediaItem, ...]] = []
        self.input_hashes: list[str] = []

    async def save(self, cmd, items, input_sha256) -> PreparedReplyMedia:
        self.calls.append(items)
        self.input_hashes.append(input_sha256)
        return PreparedReplyMedia(
            draft_id=uuid4(),
            package_id=uuid4(),
            task_id=cmd.task_id,
            account_label="RU",
            recipient_chat_id="synthetic",
            mode=cmd.mode,
            package_sha256="0" * 64,
            expected_message_count=len(items) if cmd.mode == "album" else 1,
            expires_at=datetime.now(UTC),
            items=(),
            rich_content=None,
            execution_id=cmd.execution_id,
            execution_expires_at=cmd.execution_expires_at,
        )


@pytest.mark.anyio
async def test_real_decode_and_exif_rotation_before_approval_strips_private_metadata() -> None:
    writer = CapturingWriter()
    original = image_bytes(fmt="JPEG", orientation=6)
    await PrepareReplyMediaService(writer).execute(command(media_input(original, jpeg=True)))
    item = writer.calls[0][0]
    assert (item.width, item.height) == (24, 32)
    assert item.source_sha256 == hashlib.sha256(original).hexdigest()
    assert item.output_sha256 == hashlib.sha256(item.data).hexdigest()
    assert item.source_sha256 != item.output_sha256
    assert "orientation=6" in item.transform
    with Image.open(io.BytesIO(item.data)) as prepared:
        assert not prepared.getexif()
        assert b"Private author" not in item.data


@pytest.mark.anyio
async def test_png_alpha_and_deterministic_prepared_bytes_survive() -> None:
    writer = CapturingWriter()
    cmd = command(media_input(image_bytes(mode="RGBA")))
    service = PrepareReplyMediaService(writer)
    await service.execute(cmd)
    await service.execute(cmd)
    assert writer.calls[0][0].data == writer.calls[1][0].data
    assert writer.input_hashes[0] == writer.input_hashes[1]
    with Image.open(io.BytesIO(writer.calls[0][0].data)) as prepared:
        assert prepared.mode == "RGBA"
        assert prepared.size == (32, 24)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad",
    [
        replace(media_input(), filename="../image.png"),
        replace(media_input(), filename="x\x7f.png"),
        replace(media_input(), media_type="image/jpeg"),
        replace(media_input(), expected_sha256="0" * 64),
        replace(media_input(), content_base64="https://example.test/file.png"),
        media_input(image_bytes() + b"PK\x03\x04polyglot"),
        media_input(image_bytes()[:-1]),
        media_input(image_bytes(fmt="JPEG") + b"trailing", jpeg=True),
        media_input(image_bytes(fmt="JPEG")[:-4], jpeg=True),
        media_input(b"not a png"),
        media_input(image_bytes(size=(4097, 24))),
        media_input(image_bytes(size=(50, 1))),
        media_input(caption="😀" * 513),
        media_input(caption_format="html"),
        media_input(caption="x" * 4097, caption_format="markdown"),
    ],
)
async def test_malformed_mismatched_oversized_artifacts_never_reach_sql(bad) -> None:
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError):
        await PrepareReplyMediaService(writer).execute(command(bad))
    assert not writer.calls


@pytest.mark.anyio
async def test_last_invalid_item_does_not_stage_a_partial_album() -> None:
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError):
        await PrepareReplyMediaService(writer).execute(
            command(media_input(), media_input(b"bad"), mode="album")
        )
    assert not writer.calls


@pytest.mark.anyio
async def test_utf16_caption_limit_after_markdown_conversion_and_no_mode_downgrade() -> None:
    writer = CapturingWriter()
    await PrepareReplyMediaService(writer).execute(
        command(media_input(caption="😀" * 512, caption_format="plain"))
    )
    assert writer.calls[0][0].caption == "😀" * 512
    with pytest.raises(ReplyDraftError):
        await PrepareReplyMediaService(writer).execute(
            replace(command(), mode="rich_photo", rich_content=None)
        )
    assert len(writer.calls) == 1


@pytest.mark.anyio
async def test_source_package_budget_checked_before_writer(monkeypatch) -> None:
    from app.application import reply_media

    monkeypatch.setattr(reply_media, "MAX_MEDIA_PACKAGE_BYTES", 1)
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError, match="package exceeds"):
        await PrepareReplyMediaService(writer).execute(command())
    assert not writer.calls


@pytest.mark.anyio
async def test_compressed_header_bomb_rejected_before_pixel_decode() -> None:
    import struct
    import zlib

    source = bytearray(image_bytes())
    # A valid IHDR with a tiny body but dimensions beyond the allowed pixel budget.
    source[16:24] = struct.pack(">II", 4096, 4096)
    source[29:33] = struct.pack(">I", zlib.crc32(source[12:29]) & 0xFFFFFFFF)
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError, match="pixels"):
        await PrepareReplyMediaService(writer).execute(command(media_input(bytes(source))))
    assert not writer.calls


@pytest.mark.anyio
async def test_animated_png_is_not_silently_flattened() -> None:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (32, 24), "blue")],
        duration=100,
        loop=0,
    )
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError, match="animated"):
        await PrepareReplyMediaService(writer).execute(command(media_input(output.getvalue())))
    assert not writer.calls


@pytest.mark.anyio
async def test_progressive_jpeg_is_supported_and_c1_filename_control_is_denied() -> None:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(output, format="JPEG", progressive=True)
    writer = CapturingWriter()
    service = PrepareReplyMediaService(writer)
    await service.execute(command(media_input(output.getvalue(), jpeg=True)))
    assert len(writer.calls) == 1
    with pytest.raises(ReplyDraftError, match="filename"):
        await service.execute(command(replace(media_input(), filename="x\x85.png")))
    assert len(writer.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["past", "naive", "over48h"])
async def test_invalid_execution_deadline_is_rejected_before_writer(kind) -> None:
    cmd = command()
    deadline = {
        "past": datetime.now(UTC) - timedelta(seconds=1),
        "naive": datetime.now().replace(tzinfo=None) + timedelta(hours=1),
        "over48h": datetime.now(UTC) + timedelta(hours=49),
    }[kind]
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError) as error:
        await PrepareReplyMediaService(writer).execute(replace(cmd, execution_expires_at=deadline))
    assert error.value.code == "invalid_execution_expiry"
    assert not writer.calls


@pytest.mark.anyio
async def test_existing_budget_deadline_cannot_extend_and_reservations_are_preserved() -> None:
    from app.infrastructure.reply_media import _ensure_budget
    from app.models.reply_media import ReplyMediaExecutionBudget

    deadline = datetime.now(UTC) + timedelta(hours=1)
    budget = ReplyMediaExecutionBudget(
        id=uuid4(),
        workspace_id="test",
        principal_subject="test",
        execution_id="same",
        expires_at=deadline,
        write_cap=12,
        message_cap=20,
        writes_reserved=3,
        messages_reserved=7,
    )

    class ExistingBudgetSession:
        async def scalar(self, statement):
            return budget

    session = ExistingBudgetSession()
    assert await _ensure_budget(session, principal(), "same", deadline) is budget
    with pytest.raises(ReplyDraftError) as error:
        await _ensure_budget(session, principal(), "same", deadline + timedelta(minutes=1))
    assert error.value.code == "media_execution_conflict"
    assert (budget.expires_at, budget.writes_reserved, budget.messages_reserved) == (deadline, 3, 7)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "transparent", "opaque"),
    [("RGB", (8, 16, 32), (20, 30, 40)), ("L", 12, 100)],
)
async def test_png_transparency_key_survives_normalization(mode, transparent, opaque) -> None:
    writer = CapturingWriter()
    image = Image.new(mode, (2, 1))
    image.putpixel((0, 0), transparent)
    image.putpixel((1, 0), opaque)
    output = io.BytesIO()
    image.save(output, format="PNG", transparency=transparent)
    await PrepareReplyMediaService(writer).execute(command(media_input(output.getvalue())))
    with Image.open(io.BytesIO(writer.calls[0][0].data)) as prepared:
        assert prepared.mode == "RGBA"
        alpha = prepared.getchannel("A")
        assert alpha.getpixel((0, 0)) == 0
        assert alpha.getpixel((1, 0)) == 255


@pytest.mark.anyio
async def test_source_package_budget_uses_exact_padding_before_any_decode(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app.application import reply_media

    decoder = AsyncMock()
    monkeypatch.setattr(reply_media, "decode_image", decoder)
    monkeypatch.setattr(reply_media, "MAX_MEDIA_PACKAGE_BYTES", 6)
    writer = CapturingWriter()
    service = PrepareReplyMediaService(writer)
    inputs = tuple(media_input(b"x" * length) for length in (1, 2, 3))

    # These fixtures are deliberately invalid PNGs. At exactly six source
    # bytes the mocked decoder must still be reached after preflight.
    decoder.side_effect = reply_media.ImageDecodeError("test decoder reached")
    with pytest.raises(ReplyDraftError, match="test decoder reached"):
        await service.execute(command(*inputs, mode="album"))
    decoder.assert_awaited_once()
    decoder.reset_mock()

    monkeypatch.setattr(reply_media, "MAX_MEDIA_PACKAGE_BYTES", 5)
    with pytest.raises(ReplyDraftError, match="Source package exceeds"):
        await service.execute(command(*inputs, mode="album"))
    decoder.assert_not_awaited()
    assert not writer.calls
