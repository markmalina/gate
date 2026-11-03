"""Execute the real production shell command with a local uvicorn stub."""

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="production command requires POSIX")


def start_command(tmp_path, mode=None, worker=None, actions=None):
    config = tomllib.loads((Path(__file__).parents[1] / "railway.toml").read_text())
    stub = tmp_path / "uvicorn"
    stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$GATE_WATCH_VOICE_ENABLED" '
        '"$GATE_WATCH_VOICE_MARK_OIDC_ENABLED" "$@"\n'
    )
    stub.chmod(0o700)
    environment = {"PATH": str(tmp_path) + os.pathsep + "/usr/bin:/bin", "PORT": "8123"}
    for name, value in (
        ("GATE_WATCH_VOICE_DRIVER_MODE", mode),
        ("GATE_WATCH_VOICE_ENABLED", worker),
        ("GATE_WATCH_VOICE_MARK_OIDC_ENABLED", actions),
    ):
        if value is not None:
            environment[name] = value
    return subprocess.run(
        config["deploy"]["startCommand"],
        shell=True,
        executable="/bin/sh",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


@pytest.mark.parametrize(
    ("mode", "worker", "actions", "expected"),
    [
        (None, None, None, ["false", "true"]),
        (None, "true", "false", ["false", "true"]),
        (None, "true", "true", ["false", "true"]),
        (None, "false", "false", ["false", "true"]),
        ("", "true", "false", ["false", "true"]),
        ("", "true", "true", ["false", "true"]),
        ("", "false", "false", ["false", "true"]),
        ("actions", "true", "false", ["false", "true"]),
        ("persistent", None, None, ["true", "false"]),
        ("persistent", "false", "true", ["true", "false"]),
        ("disabled", "true", "true", ["false", "false"]),
    ],
)
def test_production_mode_ignores_legacy_flags(tmp_path, mode, worker, actions, expected):
    result = start_command(tmp_path, mode, worker, actions)
    assert result.returncode == 0
    assert result.stdout.splitlines() == expected + [
        "app.main:app", "--host", "0.0.0.0", "--port", "8123"
    ]


@pytest.mark.parametrize("mode", ["unknown", "PERSISTENT", "persistent "])
def test_invalid_mode_stops_before_application_start(tmp_path, mode):
    result = start_command(tmp_path, mode=mode)
    assert result.returncode == 64
    assert result.stdout == ""
    assert result.stderr.strip() == "invalid watch voice driver mode"
