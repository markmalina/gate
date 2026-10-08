import base64
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

_path = Path(__file__).resolve().parents[1] / "scripts/gate_document_upload.py"
_spec = importlib.util.spec_from_file_location("gate_document_upload", _path)
assert _spec is not None and _spec.loader is not None
_bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bridge)
upload_arguments = _bridge.upload_arguments


def test_selected_local_file_becomes_exact_upload_arguments(tmp_path: Path) -> None:
    selected = tmp_path / "chosen.xlsx"
    selected.write_bytes(b"selected fixture bytes")
    arguments = upload_arguments(selected, uuid4(), caption="Ready", follow_up=True)
    assert base64.b64decode(arguments["content_base64"]) == selected.read_bytes()
    assert arguments["filename"] == "chosen.xlsx"
    assert arguments["media_type"].endswith("spreadsheetml.sheet")
    assert arguments["caption_format"] == "plain"
    assert arguments["follow_up"] is True


def test_relative_symlink_directory_and_unknown_type_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "chosen.pdf"
    source.write_bytes(b"fixture")
    linked = tmp_path / "linked.pdf"
    linked.symlink_to(source)
    for path in [Path("chosen.pdf"), linked, tmp_path, tmp_path / "program.exe"]:
        with pytest.raises(ValueError):
            upload_arguments(path, uuid4())
