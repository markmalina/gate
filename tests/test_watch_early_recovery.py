"""No live services: verify recovery's real startup command overrides enabled flags."""

import os
import subprocess
import tomllib
from pathlib import Path


def test_recovery_startup_hard_disables_watch_writers():
    config = tomllib.loads(Path("railway.toml").read_text())
    command = config["deploy"]["startCommand"]
    assert command.count("exec uvicorn ") == 1
    # Retain the exact startup prefix and replace only the final server launch
    # with a synthetic print of effective flags.
    command = command.replace(
        "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}",
        'printf "%s,%s,%s" "$GATE_WATCH_VOICE_ENABLED" '
        '"$GATE_WATCH_VOICE_MARK_OIDC_ENABLED" "$GATE_WATCH_VOICE_EARLY_CARDS_ONLY"',
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
            "GATE_WATCH_VOICE_MARK_OIDC_ENABLED": "true",
            "GATE_WATCH_VOICE_EARLY_CARDS_ONLY": "true",
        },
    )
    assert result.stdout == "false,false,false"
