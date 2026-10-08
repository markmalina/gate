"""Local Work bridge; its private stdout is passed directly to Gate's upload tool."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from app.application.document_formats import DOCUMENT_TYPES, MAX_DOCUMENT_BYTES


def upload_arguments(
    path: Path, task_id: UUID, *, caption: str = "", caption_format: str = "plain",
    follow_up: bool = False,
) -> dict[str, object]:
    # Work supplies one owner-selected local path. No URL or server-local path fetch.
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("An absolute selected regular file is required")
    mime = DOCUMENT_TYPES.get(path.suffix.lower())
    if mime is None:
        raise ValueError("Unsupported document extension")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as selected:
        before = os.fstat(selected.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_DOCUMENT_BYTES:
            raise ValueError("File must be regular and between 1 byte and 8 MiB")
        data = selected.read(MAX_DOCUMENT_BYTES + 1)
        after = os.fstat(selected.fileno())
    if (len(data) != before.st_size or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns):
        raise ValueError("Selected file changed while reading")
    return {
        "task_id": str(task_id), "filename": path.name, "media_type": mime,
        "content_base64": base64.b64encode(data).decode("ascii"),
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "caption": caption, "caption_format": caption_format, "follow_up": follow_up,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--task-id", type=UUID, required=True)
    parser.add_argument("--caption", default="")
    parser.add_argument("--caption-format", choices=["plain", "markdown"], default="plain")
    parser.add_argument("--follow-up", action="store_true")
    args = parser.parse_args()
    print(json.dumps(upload_arguments(
        args.file, args.task_id, caption=args.caption, caption_format=args.caption_format,
        follow_up=args.follow_up,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
