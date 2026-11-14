"""Inspect the prepared activation startup without starting a live server."""

import os
import subprocess
import tomllib
from pathlib import Path


def test_recovery_overrides_activation_flags():
    config = tomllib.loads(Path("railway.toml").read_text())
    command = config["deploy"]["startCommand"]
    launch = "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
    assert command.count(launch) == 1
    command = command.replace(
        launch,
        'printf "%s,%s,%s,%s" "$GATE_WATCH_VOICE_ENABLED" '
        '"$GATE_WATCH_VOICE_MARK_OIDC_ENABLED" "$GATE_WATCH_VOICE_EARLY_CARDS_ONLY" '
        '"$GATE_WATCH_VOICE_EARLY_BATCH_SIZE"',
    )
    result = subprocess.run(
        command,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GATE_WATCH_VOICE_ENABLED": "true",
            "GATE_WATCH_VOICE_MARK_OIDC_ENABLED": "false",
            "GATE_WATCH_VOICE_EARLY_CARDS_ONLY": "false",
        },
    )
    assert result.stdout == "false,false,false,5"
