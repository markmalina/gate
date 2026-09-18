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
    validated_telegram_receipt_payload,
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


def _captioned_document(
    *,
    unique: str = "captioned-logo",
    size: int = 228_300,
) -> dict[str, object]:
    payload = _document(
        unique=unique,
        filename="DREAME-CAPTIONED.ai",
        mime_type="application/postscript",
        size=size,
    )
    message = payload["business_message"]
    assert isinstance(message, dict)
    message["business_connection_id"] = "ru-connection"
    message["caption"] = "Logo source"
    return payload


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


def test_captioned_document_is_discovered_from_exact_durable_receipt() -> None:
    payload = _captioned_document()

    receipt_payload = validated_telegram_receipt_payload(
        payload,
        receipt_update_id=10,
        message_update_id=10,
        event_type="business_message",
        telegram_message_id=20,
        chat_id="30",
        business_connection_id="ru-connection",
    )
    public = serialize_telegram_attachments(
        {"canonical_source": True},
        receipt_payload,
        task_id=TASK_ID,
        message_id=MESSAGE_ID,
    )

    assert receipt_payload is payload
    assert public == [
        {
            "index": 0,
            "kind": "document",
            "filename": "DREAME-CAPTIONED.ai",
            "mime_type": "application/postscript",
            "size_bytes": 228_300,
            "download_available": True,
            "download_state": "available_on_request",
            "selector": f"{TASK_ID}:attachment:{MESSAGE_ID}:0",
        }
    ]
    assert "provider-captioned-logo" not in repr(public)
    assert "Logo source" not in repr(public)


def test_captioned_receipt_fails_closed_on_stale_or_mismatched_identity() -> None:
    payload = _captioned_document()
    exact = {
        "receipt_update_id": 10,
        "message_update_id": 10,
        "event_type": "business_message",
        "telegram_message_id": 20,
        "chat_id": "30",
        "business_connection_id": "ru-connection",
    }

    mismatches = [
        {"receipt_update_id": 11},
        {"message_update_id": 11},
        {"event_type": "message"},
        {"telegram_message_id": 21},
        {"chat_id": "31"},
        {"business_connection_id": "by-connection"},
    ]
    for mismatch in mismatches:
        arguments = {**exact, **mismatch}
        assert validated_telegram_receipt_payload(payload, **arguments) is None

    invalid_payload = {**payload, "update_id": True}
    assert validated_telegram_receipt_payload(invalid_payload, **exact) is None


def test_captioned_receipt_deduplicates_against_other_payload_sources() -> None:
    payload = _captioned_document()

    attachments = extract_telegram_attachments(payload, payload, payload)

    assert len(attachments) == 1
    assert attachments[0].filename == "DREAME-CAPTIONED.ai"


def test_large_captioned_receipt_stays_visible_but_not_downloadable() -> None:
    payload = _captioned_document(
        unique="captioned-fonts",
        size=TELEGRAM_CLOUD_ATTACHMENT_LIMIT_BYTES + 1,
    )
    receipt_payload = validated_telegram_receipt_payload(
        payload,
        receipt_update_id=10,
        message_update_id=10,
        event_type="business_message",
        telegram_message_id=20,
        chat_id="30",
        business_connection_id="ru-connection",
    )

    public = serialize_telegram_attachments(
        receipt_payload,
        task_id=TASK_ID,
        message_id=MESSAGE_ID,
    )

    assert public[0]["download_available"] is False
    assert public[0]["download_state"] == "telegram_cloud_limit_exceeded"


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
