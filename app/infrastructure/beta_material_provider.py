"""Bounded Telegram download, extraction, OCR, transcription and structuring."""

from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import zipfile
from collections.abc import Mapping
from typing import Any, cast

import httpx
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader  # type: ignore[import-untyped]

from app.application.beta_material import BetaMaterialJob

MAX_MATERIAL_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARS = 100_000
MAX_ZIP_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_ZIP_ENTRIES = 2_048
_ALLOWED_TEXT_MIME = {
    "text/plain",
    "text/csv",
    "application/csv",
}
_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF_MIME = "application/pdf"
_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp"}


class BetaMaterialProviderError(RuntimeError):
    pass


class TelegramMaterialDownloadAdapter:
    """Download one exact Telegram file after metadata and path validation."""

    def __init__(
        self, *, credential: str, api_base_url: str, client: httpx.AsyncClient
    ) -> None:
        if not credential or not api_base_url.startswith("https://"):
            raise BetaMaterialProviderError(
                "beta_material_telegram_configuration_invalid"
            )
        self._credential = credential
        self._api_base_url = api_base_url.rstrip("/")
        self._client = client

    async def download_file(self, *, file_id: str) -> bytes:
        if not file_id or len(file_id) > 255:
            raise BetaMaterialProviderError("beta_material_file_id_invalid")
        response = await self._client.post(
            f"{self._api_base_url}/bot{self._credential}/getFile",
            json={"file_id": file_id},
        )
        payload = _object(response)
        result = payload.get("result")
        path = result.get("file_path") if isinstance(result, dict) else None
        size = result.get("file_size") if isinstance(result, dict) else None
        if (
            not isinstance(path, str)
            or not path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise BetaMaterialProviderError("beta_material_file_path_invalid")
        if type(size) is not int or size < 1 or size > MAX_MATERIAL_BYTES:
            raise BetaMaterialProviderError("beta_material_file_size_invalid")
        download = await self._client.get(
            f"{self._api_base_url}/file/bot{self._credential}/{path}",
            timeout=30,
        )
        download.raise_for_status()
        if (
            not download.content
            or len(download.content) > MAX_MATERIAL_BYTES
            or len(download.content) != size
        ):
            raise BetaMaterialProviderError("beta_material_download_size_invalid")
        return bytes(download.content)


class OpenAIBetaMaterialExtractor:
    """Use deterministic document readers first and provider calls only as needed."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base_url: str,
        client: httpx.AsyncClient,
        transcription_model: str = "gpt-4o-mini-transcribe",
        vision_model: str,
    ) -> None:
        if (
            not api_key
            or api_base_url.rstrip("/") != "https://api.openai.com/v1"
            or not transcription_model
            or not vision_model
        ):
            raise BetaMaterialProviderError(
                "beta_material_extractor_configuration_invalid"
            )
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._client = client
        self._transcription_model = transcription_model
        self._vision_model = vision_model

    async def extract(self, *, job: BetaMaterialJob, content: bytes) -> str:
        _validate_source(content)
        if job.media_kind == "voice":
            return await self._transcribe(content)
        mime_type = _resolved_mime(job)
        deterministic = extract_document_text(
            content=content,
            mime_type=mime_type,
            file_name=job.file_name,
        )
        if deterministic is not None:
            return deterministic
        if mime_type in _IMAGE_MIME or mime_type == _PDF_MIME:
            return await self._ocr(
                content=content,
                mime_type=mime_type,
                file_name=job.file_name,
            )
        raise BetaMaterialProviderError("beta_material_format_unsupported")

    async def _transcribe(self, content: bytes) -> str:
        response = await self._client.post(
            f"{self._api_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            files={"file": ("voice.ogg", content, "audio/ogg")},
            data={
                "model": self._transcription_model,
                "language": "ru",
                "response_format": "json",
            },
            timeout=60,
        )
        payload = _object(response)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BetaMaterialProviderError(
                "beta_material_transcription_contract_invalid"
            )
        return _bounded(text)

    async def _ocr(
        self, *, content: bytes, mime_type: str, file_name: str | None
    ) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        if mime_type in _IMAGE_MIME:
            source: dict[str, str] = {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{encoded}",
            }
        else:
            source = {
                "type": "input_file",
                "filename": file_name or "document.pdf",
                "file_data": f"data:{mime_type};base64,{encoded}",
            }
        response = await self._client.post(
            f"{self._api_base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._vision_model,
                "store": False,
                "tools": [],
                "max_output_tokens": 4_096,
                "instructions": (
                    "Извлеки весь читаемый текст из файла. Сохрани порядок, "
                    "заголовки и строки таблиц. Не анализируй и не добавляй факты."
                ),
                "input": [
                    {
                        "role": "user",
                        "content": [
                            source,
                            {
                                "type": "input_text",
                                "text": "Верни только извлечённый текст.",
                            },
                        ],
                    }
                ],
            },
            timeout=60,
        )
        text = _output_text(_object(response))
        if not text.strip():
            raise BetaMaterialProviderError("beta_material_ocr_empty")
        return _bounded(text)


class OpenAIBetaMaterialStructurer:
    """Produce the same domain-neutral structure for every material kind."""

    def __init__(
        self, *, api_key: str, api_base_url: str, model: str, client: httpx.AsyncClient
    ) -> None:
        if (
            not api_key
            or api_base_url.rstrip("/") != "https://api.openai.com/v1"
            or not model
        ):
            raise BetaMaterialProviderError(
                "beta_material_structurer_configuration_invalid"
            )
        self._api_key = api_key
        self._api_base_url = api_base_url.rstrip("/")
        self._model = model
        self._client = client

    async def structure(
        self, *, media_kind: str, extracted_text: str
    ) -> str:
        response = await self._client.post(
            f"{self._api_base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "store": False,
                "tools": [],
                "max_output_tokens": 1_024,
                "instructions": (
                    "Ты Бета, универсальная помощница по сбору и использованию "
                    "информации. Не привязывай структуру к свадьбе или иной теме. "
                    "Верни по-русски «Я поняла так:» и разделы: Кратко, Факты, "
                    "Решения, Задачи, Вопросы, Даты и суммы, Риски. "
                    "Пропускай пустые разделы и никогда не добавляй фактов."
                ),
                "input": (
                    f"Тип материала: {media_kind}\n"
                    f"Извлечённое содержание:\n{_bounded(extracted_text)}"
                ),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "beta_material_structure",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 4096,
                                }
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            timeout=30,
        )
        raw = _output_text(_object(response))
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BetaMaterialProviderError(
                "beta_material_structurer_contract_invalid"
            ) from exc
        text = value.get("text") if isinstance(value, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise BetaMaterialProviderError(
                "beta_material_structurer_contract_invalid"
            )
        return text.strip()


def extract_document_text(
    *, content: bytes, mime_type: str, file_name: str | None
) -> str | None:
    """Return deterministic text, or None when bounded OCR is required."""

    _validate_source(content)
    if mime_type in _ALLOWED_TEXT_MIME:
        return _extract_text(content, csv_mode=mime_type != "text/plain")
    if mime_type == _DOCX_MIME:
        _validate_zip(content)
        return _extract_docx(content)
    if mime_type == _XLSX_MIME:
        _validate_zip(content)
        return _extract_xlsx(content)
    if mime_type == _PDF_MIME:
        return _extract_pdf(content)
    if mime_type in _IMAGE_MIME:
        return None
    suffix = (file_name or "").lower().rsplit(".", maxsplit=1)[-1]
    if suffix in {"txt", "csv"}:
        return _extract_text(content, csv_mode=suffix == "csv")
    raise BetaMaterialProviderError("beta_material_format_unsupported")


def _extract_text(content: bytes, *, csv_mode: bool) -> str:
    try:
        value = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BetaMaterialProviderError("beta_material_text_encoding_invalid") from exc
    if not csv_mode:
        return _bounded(value)
    rows = csv.reader(io.StringIO(value))
    rendered = ["\t".join(cell.strip() for cell in row) for row in rows]
    return _bounded("\n".join(rendered))


def _extract_docx(content: bytes) -> str:
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise BetaMaterialProviderError("beta_material_docx_invalid") from exc
    lines = [paragraph.text.strip() for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append("\t".join(cell.text.strip() for cell in row.cells))
    return _bounded("\n".join(line for line in lines if line))


def _extract_xlsx(content: bytes) -> str:
    try:
        workbook = load_workbook(
            io.BytesIO(content), read_only=True, data_only=True
        )
    except Exception as exc:
        raise BetaMaterialProviderError("beta_material_xlsx_invalid") from exc
    lines: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            lines.append(f"[Лист: {worksheet.title}]")
            for row in worksheet.iter_rows(values_only=True):
                rendered = "\t".join(
                    "" if value is None else str(value) for value in row
                ).rstrip()
                if rendered:
                    lines.append(rendered)
                if sum(len(item) + 1 for item in lines) > MAX_EXTRACTED_CHARS:
                    return _bounded("\n".join(lines))
    finally:
        workbook.close()
    return _bounded("\n".join(lines))


def _extract_pdf(content: bytes) -> str | None:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise BetaMaterialProviderError("beta_material_pdf_encrypted")
        if len(reader.pages) > 500:
            raise BetaMaterialProviderError("beta_material_pdf_page_limit")
        lines = [
            (page.extract_text() or "").strip()
            for page in reader.pages
        ]
    except BetaMaterialProviderError:
        raise
    except Exception as exc:
        raise BetaMaterialProviderError("beta_material_pdf_invalid") from exc
    value = "\n\n".join(line for line in lines if line).strip()
    return _bounded(value) if value else None


def _validate_zip(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_ZIP_ENTRIES
                or sum(item.file_size for item in entries)
                > MAX_ZIP_EXPANDED_BYTES
            ):
                raise BetaMaterialProviderError(
                    "beta_material_archive_expansion_limit"
                )
    except BetaMaterialProviderError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise BetaMaterialProviderError("beta_material_archive_invalid") from exc


def _resolved_mime(job: BetaMaterialJob) -> str:
    if job.media_kind == "photo":
        return job.mime_type if job.mime_type in _IMAGE_MIME else "image/jpeg"
    if isinstance(job.mime_type, str) and job.mime_type:
        return job.mime_type.lower().split(";", maxsplit=1)[0].strip()
    guessed, _ = mimetypes.guess_type(job.file_name or "")
    return guessed or "application/octet-stream"


def _validate_source(content: bytes) -> None:
    if not content or len(content) > MAX_MATERIAL_BYTES:
        raise BetaMaterialProviderError("beta_material_source_size_invalid")


def _bounded(value: str) -> str:
    result = value.strip()
    if not result:
        raise BetaMaterialProviderError("beta_material_extracted_text_empty")
    if len(result) > MAX_EXTRACTED_CHARS:
        return result[: MAX_EXTRACTED_CHARS - 1].rstrip() + "…"
    return result


def _object(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    try:
        value = response.json()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BetaMaterialProviderError(
            "beta_material_provider_response_invalid"
        ) from exc
    if not isinstance(value, dict):
        raise BetaMaterialProviderError("beta_material_provider_response_invalid")
    return cast(dict[str, Any], value)


def _output_text(provider: Mapping[str, Any]) -> str:
    direct = provider.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    output = provider.get("output")
    if not isinstance(output, list):
        raise BetaMaterialProviderError("beta_material_output_missing")
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        parts = item.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if (
                isinstance(part, Mapping)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                return cast(str, part["text"])
    raise BetaMaterialProviderError("beta_material_output_missing")
