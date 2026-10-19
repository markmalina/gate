"""Execute the real production shell command with a local uvicorn stub."""

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="production command requires POSIX")


@pytest.mark.parametrize(
    ("worker", "actions", "expected"),
    [
        (None, None, ["false", "true"]),
        ("", "", ["false", "true"]),
        ("true", "false", ["true", "false"]),
        ("false", "true", ["false", "true"]),
        ("false", "false", ["false", "false"]),
    ],
)
def test_production_start_honours_explicit_driver_flags(tmp_path, worker, actions, expected):
    config = tomllib.loads((Path(__file__).parents[1] / "railway.toml").read_text())
    stub = tmp_path / "uvicorn"
    stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$GATE_WATCH_VOICE_ENABLED" '
        '"$GATE_WATCH_VOICE_MARK_OIDC_ENABLED" "$@"\n'
    )
    stub.chmod(0o700)
    environment = {"PATH": str(tmp_path) + os.pathsep + "/usr/bin:/bin", "PORT": "8123"}
    if worker is not None:
        environment["GATE_WATCH_VOICE_ENABLED"] = worker
    if actions is not None:
        environment["GATE_WATCH_VOICE_MARK_OIDC_ENABLED"] = actions
    result = subprocess.run(
        config["deploy"]["startCommand"],
        shell=True,
        executable="/bin/sh",
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert result.stdout.splitlines() == expected + [
        "app.main:app", "--host", "0.0.0.0", "--port", "8123"
    ]
