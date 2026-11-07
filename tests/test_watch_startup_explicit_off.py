"""Exercise Railway's production shell offline; never import or start the app."""

import json
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OIDC = "GATE_WATCH_VOICE_MARK_OIDC_ENABLED"
ORDINARY = "GATE_WATCH_VOICE_ENABLED"
RICH = "GATE_RICH_SEND_ENABLED"
WORKFLOW_PIN = "6a5b4db5e428ca13524eb2140e86298447bb520fa33e24a067aa8f8d7a026d34"


def _run_startup(tmp_path: Path, inputs: dict[str, str]) -> subprocess.CompletedProcess[str]:
    with (ROOT / "railway.toml").open("rb") as source:
        command = tomllib.load(source)["deploy"]["startCommand"]
    stub = tmp_path / "uvicorn"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "keys = (\n"
        "    'GATE_WATCH_VOICE_MARK_OIDC_ENABLED', 'GATE_WATCH_VOICE_ENABLED',\n"
        "    'GATE_WATCH_VOICE_ACTIONS_WORKFLOW_SHA256', 'GATE_DOCUMENT_SEND_ENABLED',\n"
        "    'GATE_RICH_SEND_ENABLED',\n"
        ")\n"
        "print(json.dumps({'args': sys.argv[1:],\n"
        "                  'exports': {key: os.environ.get(key) for key in keys}}))\n"
        "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n",
        encoding="utf-8",
    )
    stub.chmod(0o700)
    # No inherited credentials, installed uvicorn, application, or provider environment.
    return subprocess.run(
        shlex.split(command),
        cwd=tmp_path,
        env={"PATH": str(tmp_path), **inputs},
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )


@pytest.mark.parametrize("oidc", [None, "", "false", "true"])
@pytest.mark.parametrize("ordinary", [None, "true"])
@pytest.mark.parametrize("port", [None, "8123"])
@pytest.mark.parametrize("rich", [None, "false"])
def test_production_startup_defaults_off_and_preserves_explicit_value(
    tmp_path: Path,
    oidc: str | None,
    ordinary: str | None,
    port: str | None,
    rich: str | None,
) -> None:
    inputs = {
        key: value
        for key, value in ((OIDC, oidc), (ORDINARY, ordinary), ("PORT", port), (RICH, rich))
        if value is not None
    }
    result = _run_startup(tmp_path, inputs)
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == {
        "args": ["app.main:app", "--host", "0.0.0.0", "--port", port or "8000"],
        "exports": {
            OIDC: oidc or "false",
            ORDINARY: "false",
            "GATE_WATCH_VOICE_ACTIONS_WORKFLOW_SHA256": WORKFLOW_PIN,
            "GATE_DOCUMENT_SEND_ENABLED": "true",
            RICH: rich or "true",
        },
    }


def test_production_startup_propagates_subprocess_failure(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError) as raised:
        _run_startup(tmp_path, {OIDC: "false", "STUB_EXIT": "17"})
    assert raised.value.returncode == 17
