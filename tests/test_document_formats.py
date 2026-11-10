import io
import struct
import zipfile

import pytest
from app.application.document_formats import (
    DOCUMENT_MIME_TYPES,
    DOCUMENT_TYPES,
    _check_ooxml,
    check_document_content,
)
from app.application.reply_documents import PrepareReplyDocumentService
from app.application.reply_drafts import ReplyDraftError
from PIL import Image

from test_reply_documents import CapturingWriter, _command


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def ooxml(extension: str) -> bytes:
    from app.application.document_formats import _OOXML

    path, suffix, root = _OOXML[extension]
    namespace = {
        ".xlsx": "spreadsheetml", ".docx": "wordprocessingml", ".pptx": "presentationml"
    }[extension]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/{path}" ContentType="application/vnd.openxmlformats-'
            f'officedocument.{suffix}"/></Types>'
        ))
        archive.writestr("_rels/.rels", (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/officeDocument" Target="{path}"/>'
            '</Relationships>'
        ))
        archive.writestr(path, (
            f'<{root} xmlns="http://schemas.openxmlformats.org/{namespace}/2006/main"/>'
        ))
    return output.getvalue()


def legacy_office(extension: str) -> bytes:
    # Synthetic CFB, deliberately only a container fixture, not an Office-open claim.
    name = {".xls": "Workbook", ".doc": "WordDocument", ".ppt": "PowerPoint Document"}[extension]
    free, end, fat = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    fat_sector = struct.pack("<128I", fat, end, *range(3, 10), end, *([free] * 118))

    def entry(label: str, kind: int, start: int, size: int, child: int = free) -> bytes:
        result = bytearray(128)
        encoded = (label + "\0").encode("utf-16-le")
        result[:len(encoded)] = encoded
        struct.pack_into("<HBBIII", result, 64, len(encoded), kind, 1, free, free, child)
        struct.pack_into("<IQ", result, 116, start, size)
        return bytes(result)

    directory = entry("Root Entry", 5, end, 0, 1) + entry(name, 2, 2, 4096) + bytes(256)
    return bytes(header) + fat_sector + directory + b"Gate synthetic container".ljust(4096, b"\0")


def image_bytes(extension: str) -> bytes:
    format_name = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                   ".webp": "WEBP", ".gif": "GIF"}[extension]
    output = io.BytesIO()
    Image.new("RGB", (16, 12), "green").save(output, format=format_name)
    return output.getvalue()


@pytest.mark.anyio
@pytest.mark.parametrize("extension", [".xlsx", ".docx", ".pptx", ".xls", ".doc", ".ppt",
                                      ".jpg", ".jpeg", ".png", ".webp", ".gif"])
async def test_original_formats_preserve_exact_bytes_and_metadata(extension: str) -> None:
    if extension.endswith("x"):
        data = ooxml(extension)
    elif extension in {".xls", ".doc", ".ppt"}:
        data = legacy_office(extension)
    else:
        data = image_bytes(extension)
    writer = CapturingWriter()
    command = _command(
        filename="Gate-тест" + extension, media_type=DOCUMENT_TYPES[extension], data=data
    )
    result = await PrepareReplyDocumentService(writer).execute(command)
    assert writer.received == [data]
    assert (result.filename, result.media_type, result.sha256, result.size_bytes) == (
        command.filename, command.media_type, command.expected_sha256, len(data)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(("extension", "data"), [
    (".xlsx", ooxml(".docx")), (".docx", ooxml(".pptx")),
    (".xlsx", b"PK\x03\x04not an Office file"),
    (".doc", legacy_office(".xls")), (".ppt", b"not OLE"),
    (".png", image_bytes(".jpg")), (".jpg", image_bytes(".jpg")[:-40]),
    (".webp", image_bytes(".webp")[:30]),
])
async def test_false_extension_or_corruption_never_stages(extension: str, data: bytes) -> None:
    writer = CapturingWriter()
    with pytest.raises(ReplyDraftError, match="structure mismatch"):
        await PrepareReplyDocumentService(writer).execute(_command(
            filename="fixture" + extension, media_type=DOCUMENT_TYPES[extension], data=data
        ))
    assert not writer.received


@pytest.mark.anyio
async def test_unknown_format_has_actionable_error() -> None:
    with pytest.raises(ReplyDraftError) as error:
        await PrepareReplyDocumentService(CapturingWriter()).execute(_command(
            filename="file.exe", media_type="application/octet-stream"
        ))
    assert error.value.code == "unsupported_document_type"


def test_ooxml_inflation_duplicate_traversal_and_dtd_rejected() -> None:
    for name, payload in [("../file", "unsafe"), ("[Content_Types].xml", "duplicate"),
                          ("huge", "A" * (65 * 1024 * 1024))]:
        output = io.BytesIO(ooxml(".xlsx"))
        with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            with pytest.warns(UserWarning) if name == "[Content_Types].xml" else _no_warning():
                archive.writestr(name, payload)
        with pytest.raises(ValueError):
            _check_ooxml(output.getvalue(), ".xlsx")
    from app.application.document_formats import _xml
    from defusedxml.common import DTDForbidden
    with pytest.raises(DTDForbidden):
        _xml(b'<!DOCTYPE r [<!ENTITY x "boom">]><r>&x;</r>')
    with pytest.raises(DTDForbidden):
        _xml('<!DOCTYPE r [<!ENTITY x "boom">]><r>&x;</r>'.encode("utf-16"))


def _no_warning():
    from contextlib import nullcontext
    return nullcontext()


@pytest.mark.anyio
async def test_worker_timeout_kills_child_and_does_not_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from app.application import document_formats

    class StalledProcess:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, data: bytes) -> None:
            await asyncio.Event().wait()

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> None:
            self.waited = True

    process = StalledProcess()

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(document_formats, "VALIDATION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ReplyDraftError) as error:
        await check_document_content("file.png", b"fixture")
    assert error.value.code == "document_validation_timeout"
    assert process.killed and process.waited
    assert document_formats._VALIDATION_SLOTS._value == 2


def test_actual_mcp_input_output_share_supported_mimes() -> None:
    from app.api.mcp_routes import _gate_prepare_reply_document_tool
    from app.api.mcp_schemas import (
        GATE_DOCUMENT_PREPARE_OUTPUT_SCHEMA,
        GATE_DOCUMENT_SEND_OUTPUT_SCHEMA,
    )
    from app.core.config import Settings

    schema = _gate_prepare_reply_document_tool(Settings())["inputSchema"]
    assert schema["properties"]["media_type"]["enum"] == DOCUMENT_MIME_TYPES
    for output in [GATE_DOCUMENT_PREPARE_OUTPUT_SCHEMA, GATE_DOCUMENT_SEND_OUTPUT_SCHEMA]:
        assert output["oneOf"][0]["properties"]["media_type"]["enum"] == DOCUMENT_MIME_TYPES


@pytest.mark.anyio
@pytest.mark.parametrize("extension", [".xlsx", ".docx", ".pptx", ".jpg", ".png", ".webp", ".gif"])
async def test_actual_multipart_original_bytes_mime_and_document_method(extension: str) -> None:
    import httpx
    from app.services.telegram import TelegramClient

    data = ooxml(extension) if extension.endswith("x") else image_bytes(extension)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await TelegramClient("synthetic-token", http_client=client).send_business_document(
            chat_id="123", business_connection_id="synthetic-connection",
            filename="original" + extension, media_type=DOCUMENT_TYPES[extension], data=data,
        )
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/sendDocument")
    body = requests[0].content
    assert data in body
    assert ('filename="original' + extension + '"').encode() in body
    assert ("Content-Type: " + DOCUMENT_TYPES[extension]).encode() in body
    assert b'name="disable_content_type_detection"\r\n\r\ntrue' in body


@pytest.mark.anyio
async def test_decode_bomb_is_rejected_in_bounded_worker() -> None:
    output = io.BytesIO()
    Image.new("RGB", (4096, 4097), "green").save(output, format="PNG")
    with pytest.raises(ReplyDraftError):
        await check_document_content("large.png", output.getvalue())


@pytest.mark.anyio
async def test_queue_bound_and_cancellation_reap_children(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.application import document_formats

    processes = []
    started = asyncio.Event()

    class StalledProcess:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, data: bytes) -> None:
            if len(processes) == 2:
                started.set()
            await asyncio.Event().wait()

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> None:
            self.waited = True

    async def create(*args, **kwargs):
        process = StalledProcess()
        processes.append(process)
        return process

    slots = asyncio.Semaphore(2)
    monkeypatch.setattr(document_formats, "_VALIDATION_SLOTS", slots)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    running = [
        asyncio.create_task(check_document_content("test.png", b"fixture")) for _ in range(2)
    ]
    await started.wait()
    with pytest.raises(ReplyDraftError) as error:
        await check_document_content("overflow.png", b"fixture")
    assert error.value.code == "document_validation_busy"
    assert len(processes) == 2
    for task in running:
        task.cancel()
    await asyncio.gather(*running, return_exceptions=True)
    assert slots._value == 2
    assert all(process.killed and process.waited for process in processes)
