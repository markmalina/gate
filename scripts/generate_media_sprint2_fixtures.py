"""Regenerate #742 synthetic PNGs and operator examples without external calls.

Run from the repository with its pinned Python environment and PYTHONPATH=.
The exact font/renderer versions are pinned to make byte drift explicit.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zlib
from pathlib import Path

from app.application.reply_media import ReplyMediaInput, _validate_image
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin, features
from PIL import __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
FONT_SHA256 = "ae7b7855e115a5966d8b1b3f80f254ccc117ec86f9965e202ee2940453837280"
EXECUTION_ID = "gate-media-742-20261007T1041Z"
DEADLINE = "2026-10-09T10:37:34Z"
TASK_ID = "74200000-0000-4000-8000-000000000001"
COLORS = ("#075985", "#9A3412", "#065F46", "#6B21A8")


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render(number: int, font_path: Path, destination: Path) -> None:
    image = Image.new("RGB", (800, 600), COLORS[number - 1])
    draw = ImageDraw.Draw(image)
    label = f"Gate — тестовое изображение {number}"
    label_font = ImageFont.truetype(str(font_path), 30)
    number_font = ImageFont.truetype(str(font_path), 240)
    bounds = draw.textbbox((0, 0), label, font=label_font)
    width = bounds[2] - bounds[0]
    if width > 704:
        raise RuntimeError("Label would touch the safe margin")
    draw.text((400 - width // 2, 510), label, fill="white", font=label_font)
    draw.text((95, 135), str(number), fill="white", font=number_font)
    if number == 1:
        draw.ellipse((460, 160, 680, 380), outline="white", width=14)
    elif number == 2:
        draw.polygon([(570, 150), (700, 380), (440, 380)], outline="white", width=14)
    elif number == 3:
        draw.rectangle((460, 160, 680, 380), outline="white", width=14)
    else:
        draw.polygon([(570, 140), (700, 270), (570, 400), (440, 270)], outline="white", width=14)
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b"sRGB", b"\x00")  # IEC sRGB intent; no timestamps, EXIF or ICC blob.
    image.save(destination, format="PNG", pnginfo=metadata, compress_level=9, optimize=False)


def heading(text: str) -> dict[str, object]:
    return {"type": "heading", "size": 1, "text": text}


def photo(number: int, caption: str) -> dict[str, object]:
    return {"type": "photo", "media_ref": f"item:{number}", "caption": {"text": caption}}


def operator_calls(items: list[dict[str, str]], task_id: str, follow_up: bool) -> dict[str, object]:
    definitions: list[tuple[str, str, int, dict[str, object] | None]] = [
        ("P", "photo", 1, None),
        ("A", "album", 4, None),
        (
            "R",
            "rich_photo",
            1,
            {
                "schema_version": "gate.rich.v2",
                "blocks": [
                    heading("Gate Media — фото в Rich"),
                    {
                        "type": "paragraph",
                        "text": "Синтетическая проверка изображения внутри Rich.",
                    },
                    photo(1, "Кадр 1."),
                ],
            },
        ),
        (
            "S",
            "rich_slideshow",
            4,
            {
                "schema_version": "gate.rich.v2",
                "blocks": [
                    heading("Gate Media — слайд-шоу"),
                    {
                        "type": "slideshow",
                        "photos": [photo(i, f"Кадр {i} из 4.") for i in range(1, 5)],
                        "caption": {"text": "Синтетическая проверка листания и порядка."},
                    },
                ],
            },
        ),
        (
            "C",
            "rich_collage",
            4,
            {
                "schema_version": "gate.rich.v2",
                "blocks": [
                    heading("Gate Media — коллаж"),
                    {
                        "type": "collage",
                        "photos": [photo(i, f"Кадр {i} из 4.") for i in range(1, 5)],
                        "caption": {"text": "Синтетическая проверка совместного отображения."},
                    },
                ],
            },
        ),
    ]
    fixtures: dict[str, object] = {}
    for key, mode, count, rich in definitions:
        selected = [dict(item) for item in items[:count]]
        for index, item in enumerate(selected, 1):
            item["caption"] = (
                "Gate Media Sprint 2 — одиночное фото 1."
                if key == "P"
                else f"Gate Media Sprint 2 — кадр {index} из 4."
                if key == "A"
                else ""
            )
            item["caption_format"] = "plain"
        arguments: dict[str, object] = {
            "task_id": task_id,
            "mode": mode,
            "items": selected,
            "staging_key": f"{EXECUTION_ID}-{key}",
            "execution_id": EXECUTION_ID,
            "execution_expires_at": DEADLINE,
            "follow_up": follow_up,
        }
        if rich:
            arguments["rich_content"] = rich
        fixtures[key] = {
            "planned_provider_writes": 1,
            "planned_visible_messages": count if mode == "album" else 1,
            "prepare": {"name": "gate_prepare_reply_media", "arguments": arguments},
            "preview": {
                "name": "gate_preview_reply_media",
                "arguments": {"package_id": f"${{{key}.package_id}}"},
            },
            "send_after_exact_preview_approval": {
                "name": "gate_send_approved_reply",
                "arguments": {
                    "draft_id": f"${{{key}.draft_id}}",
                    "confirmed": True,
                    "expected_account": "RU",
                    "expected_recipient_chat_id": (
                        f"${{{key}.confirmation.expected_recipient_chat_id}}"
                    ),
                    "expected_media_package_sha256": f"${{{key}.package_sha256}}",
                    "execution_id": EXECUTION_ID,
                },
            },
        }
    fixtures["T"] = {
        "planned_provider_writes": 1,
        "planned_visible_messages": 1,
        "prepare": {
            "name": "gate_save_reply_draft",
            "arguments": {
                "task_id": task_id,
                "text_format": "rich",
                "follow_up": follow_up,
                "rich_content": {
                    "schema_version": "gate.rich.v1",
                    "blocks": [
                        heading("Gate Media — контроль текста"),
                        {
                            "type": "paragraph",
                            "text": "Синтетическая проверка прежнего текстового Rich.",
                        },
                    ],
                },
            },
        },
        "send_after_exact_preview_approval": {
            "name": "gate_send_approved_reply",
            "arguments": {
                "draft_id": "${T.draft_id}",
                "confirmed": True,
                "expected_account": "RU",
                "expected_recipient_chat_id": "${T.confirmation.expected_recipient_chat_id}",
                "expected_payload_sha256": "${T.formatting.payload_sha256}",
                "execution_id": EXECUTION_ID,
            },
        },
    }
    return {
        "issue": 742,
        "synthetic_only": True,
        "execution_id": EXECUTION_ID,
        "execution_expires_at": DEADLINE,
        "task_id_is_example": task_id == TASK_ID,
        "instructions": [
            "Replace the example task_id with the currently authorized own RU→BY task UUID.",
            "Read current readiness; set follow_up=true only for a justified "
            "replied-task follow-up.",
            "Prepare never sends. Render all image content blocks and the complete "
            "snapshot before approval.",
            "Resolve ${X...} from the same real prepare/preview response; "
            "never submit placeholders.",
            "Recipient must equal the protected own BY recipient and preview confirmation; "
            "account must be RU.",
            "The original deadline cannot be renewed; expired staging/send must stop.",
            "Do not copy synthetic base64 fields into logs, comments or text drafts.",
        ],
        "planned_provider_writes": 6,
        "planned_visible_messages": 9,
        "hard_provider_write_cap": 12,
        "hard_visible_message_cap": 20,
        "fixtures": fixtures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--font", type=Path, default=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    parser.add_argument("--task-id", default=TASK_ID)
    parser.add_argument("--follow-up", action="store_true")
    args = parser.parse_args()
    versions = (pillow_version, features.version("freetype2"), zlib.ZLIB_VERSION)
    if versions != ("12.3.0", "2.14.3", "1.3.2"):
        raise RuntimeError(
            f"Pinned renderer differs: {versions!r}; do not silently replace fixtures"
        )
    if hashlib.sha256(args.font.read_bytes()).hexdigest() != FONT_SHA256:
        raise RuntimeError("Pinned DejaVu Sans font differs")
    destination = ROOT / "tests/fixtures/gate_media_sprint2"
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    items = []
    for number in range(1, 5):
        path = destination / f"image-{number}.png"
        render(number, args.font, path)
        data = path.read_bytes()
        item = {
            "filename": path.name,
            "media_type": "image/png",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "expected_sha256": hashlib.sha256(data).hexdigest(),
        }
        validated = _validate_image(ReplyMediaInput(**item))
        with Image.open(path) as image:
            if (
                image.size != (800, 600)
                or image.mode != "RGB"
                or image.getexif()
                or image.info != {"srgb": 0}
            ):
                raise RuntimeError("Generated PNG does not meet the source fixture contract")
        entries.append(
            {
                "ordinal": number,
                "filename": path.name,
                "label": f"Gate — тестовое изображение {number}",
                "background_srgb": COLORS[number - 1],
                "width": validated.width,
                "height": validated.height,
                "source_size_bytes": validated.source_size_bytes,
                "source_sha256": validated.source_sha256,
                "normalized_size_bytes": validated.size_bytes,
                "normalized_output_sha256": validated.output_sha256,
                "transform": validated.transform,
                "transform_version": validated.transform_version,
            }
        )
        items.append(item)
    dump(
        destination / "manifest.json",
        {
            "schema_version": "gate.synthetic-media-fixtures.v1",
            "issue": 742,
            "generator": "scripts/generate_media_sprint2_fixtures.py",
            "image_source": (
                "deterministic Pillow graphics; synthetic only; no model/provider calls"
            ),
            "font": "DejaVuSans.ttf",
            "font_sha256": FONT_SHA256,
            "pillow_version": versions[0],
            "freetype_version": versions[1],
            "zlib_version": versions[2],
            "source_color_space": "sRGB (PNG sRGB rendering intent 0)",
            "source_exif": False,
            "normalized_hash_provenance": (
                "app.application.reply_media._validate_image, local pinned environment"
            ),
            "runtime_hash_rule": (
                "Real prepare/preview transform version and output hash remain authoritative; "
                "compare before approval"
            ),
            "items": entries,
        },
    )
    dump(
        ROOT / "docs/specs/ISSUE-742-MEDIA-OPERATOR-EXAMPLES.json",
        operator_calls(items, args.task_id, args.follow_up),
    )
    print(
        f"Generated {len(entries)} synthetic images, manifest and six operator fixtures; "
        "no external calls"
    )


if __name__ == "__main__":
    main()
