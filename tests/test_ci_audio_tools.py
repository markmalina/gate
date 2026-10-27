"""Execute the workflow's real shell block with no system/package/network effects."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    os.name != "posix" or BASH is None, reason="CI audio setup uses Linux Bash"
)
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
APT_OPTIONS = [
    "-o", "Acquire::Retries=1",
    "-o", "Acquire::http::Timeout=20",
    "-o", "Acquire::https::Timeout=20",
    "-o", "DPkg::Lock::Timeout=30",
]
STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TRACE"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
if name == "sudo":
    assert args[0] == "-n"
    os.execvp(args[1], args[1:])
if name == "timeout":
    assert args[0] == "--kill-after=10s"
    phase = "UPDATE" if args[-1] == "update" else "INSTALL"
    assert args[1] == ("90s" if phase == "UPDATE" else "180s")
    if os.environ.get(phase + "_TIMEOUT"):
        sys.exit(124)
    os.execvp(args[2], args[2:])
if name == "apt-get":
    phase = "UPDATE" if args[-1] == "update" else "INSTALL"
    code = int(os.environ.get(phase + "_EXIT", "0"))
    if code:
        sys.exit(code)
    if phase == "INSTALL":
        for binary in ("ffmpeg", "ffprobe"):
            path = Path(os.environ["PATH"]) / binary
            if not path.exists() and binary != os.environ.get("OMIT_BINARY"):
                path.symlink_to(Path(__file__).resolve())
    sys.exit(0)
if name in {"ffmpeg", "ffprobe"}:
    assert args == ["-version"]
    sys.exit(9 if name == os.environ.get("BROKEN_BINARY") else 0)
raise AssertionError(name)
'''


def _audio_script() -> str:
    """Extract this literal run block without interpreting unrelated workflow YAML."""
    text = WORKFLOW.read_text()
    marker = "      - name: Ensure real audio regression tools\n"
    assert text.count(marker) == 1
    step = text.split(marker, 1)[1].split("      - name:", 1)[0]
    assert step.startswith("        timeout-minutes: 5\n        run: |\n")
    lines = step.split("        run: |\n", 1)[1].splitlines()
    assert lines and all(line.startswith("          ") for line in lines)
    return textwrap.dedent("\n".join(lines))


def _run_setup(tmp_path: Path, installed: tuple[str, ...], **settings: str):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    driver = tmp_path / "stub"
    driver.write_text(f"#!{sys.executable}\n" + textwrap.dedent(STUB))
    driver.chmod(0o755)
    for name in ("sudo", "timeout", "apt-get", *installed):
        (binaries / name).symlink_to(driver)
    trace = tmp_path / "trace.jsonl"
    result = subprocess.run(
        [BASH, "-e", "-o", "pipefail", "-c", _audio_script()],
        env={"PATH": str(binaries), "TRACE": str(trace), **settings},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    for event in events:
        if event[0] == "apt-get":
            ending = (
                ["-o", "APT::Update::Error-Mode=any", "update"]
                if event[-1] == "update"
                else ["install", "-y", "--no-install-recommends", "ffmpeg"]
            )
            assert event == ["apt-get", *APT_OPTIONS, *ending]
    return result, events


def test_preinstalled_audio_tools_avoid_package_operations(tmp_path):
    result, events = _run_setup(tmp_path, ("ffmpeg", "ffprobe"))
    assert result.returncode == 0
    assert events == [["ffmpeg", "-version"], ["ffprobe", "-version"]]


@pytest.mark.parametrize("installed", [(), ("ffmpeg",), ("ffprobe",)])
def test_audio_tools_install_then_validate_both_binaries(tmp_path, installed):
    result, events = _run_setup(tmp_path, installed)
    assert result.returncode == 0
    assert [event[0] for event in events] == [
        "sudo", "timeout", "apt-get", "sudo", "timeout", "apt-get", "ffmpeg", "ffprobe"
    ]


@pytest.mark.parametrize(
    ("settings", "code", "names"),
    [
        ({"UPDATE_EXIT": "100"}, 100, ["sudo", "timeout", "apt-get"]),
        ({"UPDATE_TIMEOUT": "1"}, 124, ["sudo", "timeout"]),
        ({"INSTALL_EXIT": "100"}, 100,
         ["sudo", "timeout", "apt-get", "sudo", "timeout", "apt-get"]),
        ({"INSTALL_TIMEOUT": "1"}, 124,
         ["sudo", "timeout", "apt-get", "sudo", "timeout"]),
        ({"OMIT_BINARY": "ffprobe"}, 127,
         ["sudo", "timeout", "apt-get", "sudo", "timeout", "apt-get", "ffmpeg"]),
        ({"BROKEN_BINARY": "ffmpeg"}, 9,
         ["sudo", "timeout", "apt-get", "sudo", "timeout", "apt-get", "ffmpeg"]),
        ({"BROKEN_BINARY": "ffprobe"}, 9,
         ["sudo", "timeout", "apt-get", "sudo", "timeout", "apt-get", "ffmpeg", "ffprobe"]),
    ],
)
def test_audio_setup_fails_closed_and_keeps_original_exit_status(tmp_path, settings, code, names):
    result, events = _run_setup(tmp_path, (), **settings)
    assert result.returncode == code
    assert [event[0] for event in events] == names
    assert f"::error::Real audio regression tools setup failed (exit {code})." in result.stdout
