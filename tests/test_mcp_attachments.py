"""Attachment discovery and explicit retrieval contracts for Gate M."""

from __future__ import annotations

import base64
from uuid import UUID

from app.api.mcp_routes import (
    _attachment_content_tool_result,
    _parse_reply_context_selector,
)
from app.application.telegram_attachments import (
    TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES,
    extract_telegram_attachments,
    serialize_telegram_attachments,
)

TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = UUID("22222222-2222-2222-2222-222222222222")


def _document(
    *,
    unique: str,
    filename: str,
    mime_type: str,
    size: int,
) -> dict[str, object]:
    return {
        "update_id": 10,
        "business_message": {
            "message_id": 20,
            "chat": {"id": 30},
            "document": {
                "file_id": f"provider-{unique}",
                "file_unique_id": unique,
                "file_name": filename,
                "mime_type": mime_type,
                "file_size": size,
            },
        },
    }


def test_document_metadata_is_lazy_and_provider_ids_stay_private() -> None:
    payload = _document(
        unique="logo-ai",
        filename="../DREAME-LOGO.ai",
        mime_type="application/postscript",
        size=228_300,
    )

    attachments = extract_telegram_attachments(payload)
    public = serialize_telegram_attachments(
        payload,
        task_id=TASK_ID,
        message_id=MESSAGE_ID,
    )

    assert len(attachments) == 1
    assert attachments[0].filename == "DREAME-LOGO.ai"
    assert attachments[0].download_available is True
    assert public == [
        {
            "index": 0,
            "kind": "document",
            "filename": "DREAME-LOGO.ai",
            "mime_type": "application/postscript",
            "size_bytes": 228_300,
            "download_available": True,
            "download_state": "available_on_request",
            "selector": f"{TASK_ID}:attachment:{MESSAGE_ID}:0",
        }
    ]
    rendered = repr(public)
    assert "provider-logo-ai" not in rendered
    assert "file_id" not in rendered
    assert "file_unique_id" not in rendered


def test_large_archive_is_visible_but_fails_closed_before_download() -> None:
    payload = _document(
        unique="fonts-zip",
        filename="Brand_Fonts.zip",
        mime_type="application/zip",
        size=51_300_000,
    )

    attachment = serialize_telegram_attachments(
        payload,
        task_id=TASK_ID,
        message_id=MESSAGE_ID,
    )[0]

    assert attachment["size_bytes"] > TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES
    assert attachment["download_available"] is False
    assert attachment["download_state"] == "telegram_cloud_limit_exceeded"


def test_photo_album_selects_one_largest_downloadable_photo() -> None:
    payload = {
        "business_message": {
            "photo": [
                {
                    "file_id": "small",
                    "file_unique_id": "same-small",
                    "file_size": 100,
                    "width": 100,
                    "height": 100,
                },
                {
                    "file_id": "large",
                    "file_unique_id": "same-large",
                    "file_size": 2_000,
                    "width": 1000,
                    "height": 1000,
                },
            ]
        }
    }

    attachments = extract_telegram_attachments(payload)

    assert len(attachments) == 1
    assert attachments[0].kind == "photo"
    assert attachments[0].file_id == "large"
    assert attachments[0].mime_type == "image/jpeg"


def test_polinalike_four_messages_produce_four_opaque_selectors() -> None:
    payloads = [
        _document(
            unique="fonts",
            filename="Brand_Fonts.zip",
            mime_type="application/zip",
            size=51_300_000,
        ),
        _document(
            unique="logo",
            filename="DREAME-LOGO.ai",
            mime_type="application/postscript",
            size=228_300,
        ),
        _document(
            unique="png-color",
            filename="color.png",
            mime_type="image/png",
            size=40_300,
        ),
        _document(
            unique="png-mono",
            filename="mono.png",
            mime_type="image/png",
            size=38_700,
        ),
    ]

    public = [
        serialize_telegram_attachments(
            payload,
            task_id=TASK_ID,
            message_id=UUID(int=index + 100),
        )[0]
        for index, payload in enumerate(payloads)
    ]

    assert [item["filename"] for item in public] == [
        "Brand_Fonts.zip",
        "DREAME-LOGO.ai",
        "color.png",
        "mono.png",
    ]
    assert len({item["selector"] for item in public}) == 4
    assert [item["download_available"] for item in public] == [
        False,
        True,
        True,
        True,
    ]


def test_attachment_selector_is_exact_and_voice_compatible() -> None:
    selector = f"{TASK_ID}:attachment:{MESSAGE_ID}:0"
    assert _parse_reply_context_selector(selector) == (
        TASK_ID,
        None,
        (MESSAGE_ID, 0),
    )
    assert _parse_reply_context_selector(
        f"{TASK_ID}:voice:{MESSAGE_ID}"
    ) == (TASK_ID, MESSAGE_ID, None)


def test_generic_attachment_is_returned_as_embedded_mcp_resource() -> None:
    attachment = extract_telegram_attachments(
        _document(
            unique="logo",
            filename="DREAME-LOGO.ai",
            mime_type="application/postscript",
            size=3,
        )
    )[0]
    context = {"task_id": str(TASK_ID)}
    payload = _attachment_content_tool_result(
        request_id="download",
        context=context,
        message_id=MESSAGE_ID,
        attachment_index=0,
        attachment=attachment,
        data=b"ai!",
        media_type="application/postscript",
    )
    result = payload["result"]

    assert result["structuredContent"] is context
    assert result["content"][1]["type"] == "resource"
    assert result["content"][1]["resource"]["blob"] == base64.b64encode(
        b"ai!"
    ).decode("ascii")
    assert "DREAME-LOGO.ai" in result["content"][1]["resource"]["uri"]
    assert "provider-logo" not in repr(result)


def test_png_attachment_is_returned_as_mcp_image() -> None:
    attachment = extract_telegram_attachments(
        _document(
            unique="png",
            filename="logo.png",
            mime_type="image/png",
            size=3,
        )
    )[0]
    payload = _attachment_content_tool_result(
        request_id="download",
        context={"task_id": str(TASK_ID)},
        message_id=MESSAGE_ID,
        attachment_index=0,
        attachment=attachment,
        data=b"png",
        media_type="image/png",
    )

    assert payload["result"]["content"][1] == {
        "type": "image",
        "data": base64.b64encode(b"png").decode("ascii"),
        "mimeType": "image/png",
    }
