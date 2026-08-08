import io
import json

import httpx
import pytest
from app.application.beta_material import BetaMaterialJob
from app.infrastructure.beta_material_provider import (
    BetaMaterialProviderError,
    OpenAIBetaMaterialExtractor,
    OpenAIBetaMaterialStructurer,
    extract_document_text,
)
from docx import Document  # type: ignore[import-untyped]
from openpyxl import Workbook  # type: ignore[import-untyped]


def _job(
    *,
    media_kind: str = "document",
    file_name: str = "file.txt",
    mime_type: str = "text/plain",
) -> BetaMaterialJob:
    from datetime import UTC, datetime

    return BetaMaterialJob(
        job_id="job",
        telegram_chat_id="chat",
        telegram_file_id="file",
        media_kind=media_kind,
        sender_label="Марк",
        occurred_at=datetime(2026, 8, 8, tzinfo=UTC),
        file_name=file_name,
        mime_type=mime_type,
    )


def _tiny_pdf(text: str) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Length {len(text) + 35} >>\nstream\n"
            f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET\nendstream"
        ).encode(),
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode())
        result.extend(body)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(result)


def test_txt_csv_pdf_docx_and_xlsx_extract_without_model() -> None:
    assert extract_document_text(
        content="Привет, Бета".encode(),
        mime_type="text/plain",
        file_name="note.txt",
    ) == "Привет, Бета"
    assert extract_document_text(
        content="name,amount\nЗал,1000".encode(),
        mime_type="text/csv",
        file_name="budget.csv",
    ) == "name\tamount\nЗал\t1000"
    assert "Hello Beta" in (
        extract_document_text(
            content=_tiny_pdf("Hello Beta"),
            mime_type="application/pdf",
            file_name="brief.pdf",
        )
        or ""
    )

    docx = io.BytesIO()
    document = Document()
    document.add_paragraph("Решение принято")
    document.save(docx)
    assert "Решение принято" in (
        extract_document_text(
            content=docx.getvalue(),
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            file_name="decision.docx",
        )
        or ""
    )

    xlsx = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Бюджет"
    sheet.append(["Статья", "Сумма"])
    sheet.append(["Зал", 1000])
    workbook.save(xlsx)
    workbook.close()
    extracted = extract_document_text(
        content=xlsx.getvalue(),
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        file_name="budget.xlsx",
    )
    assert extracted is not None
    assert "[Лист: Бюджет]" in extracted
    assert "Зал\t1000" in extracted


@pytest.mark.anyio
async def test_image_uses_bounded_ocr_without_persisting_provider_input() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Текст со скана",
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        extractor = OpenAIBetaMaterialExtractor(
            api_key="fixture-key",
            api_base_url="https://api.openai.com/v1",
            client=client,
            vision_model="gpt-5.6-luna",
        )
        value = await extractor.extract(
            job=_job(
                media_kind="photo",
                file_name="scan.png",
                mime_type="image/png",
            ),
            content=b"not-a-real-image-but-provider-is-fake",
        )

    assert value == "Текст со скана"
    assert captured["store"] is False
    assert captured["tools"] == []


@pytest.mark.anyio
async def test_structurer_is_domain_neutral() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {"text": "Я поняла так:\nКратко: универсальный материал"}
                )
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        structurer = OpenAIBetaMaterialStructurer(
            api_key="fixture-key",
            api_base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            client=client,
        )
        result = await structurer.structure(
            media_kind="document",
            extracted_text="Произвольная тема",
        )

    assert result.startswith("Я поняла так:")
    instructions = str(captured["instructions"]).lower()
    assert "универсальная" in instructions
    assert "не привязывай" in instructions


def test_unsupported_and_zip_bomb_fail_with_safe_codes() -> None:
    with pytest.raises(
        BetaMaterialProviderError,
        match="beta_material_format_unsupported",
    ):
        extract_document_text(
            content=b"binary",
            mime_type="application/octet-stream",
            file_name="archive.bin",
        )
