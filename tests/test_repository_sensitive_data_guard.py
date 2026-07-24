"""Synthetic current-tree sensitive-data guard tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.repository_sensitive_data_guard import scan_text  # noqa: E402


def _codes(content: str) -> set[str]:
    return {
        finding.code
        for finding in scan_text(Path("app/synthetic_runtime.py"), content)
    }


def test_guard_detects_synthetic_analog_of_temporary_personal_route_material() -> None:
    content = """
_TMP_TRANSCRIPTION_TOKEN = "synthetic-access-material-12345"
_TMP_AUDIO_URL = "https://drive.usercontent.google.com/download"
_TMP_PROMPT = "synthetic personal context"
"""

    assert _codes(content) == {
        "temporary_sensitive_symbol",
        "literal_sensitive_assignment",
        "fixed_private_media_host",
    }


def test_guard_detects_embedded_bearer_without_echoing_match() -> None:
    findings = scan_text(
        Path("app/synthetic_runtime.py"),
        '{"Authorization": "Bearer synthetic-credential-material-12345"}',
    )

    assert [(finding.line, finding.code) for finding in findings] == [
        (1, "embedded_bearer_literal")
    ]
    assert all("synthetic-credential" not in repr(finding) for finding in findings)


def test_guard_detects_high_confidence_provider_token_shapes() -> None:
    content = """
OPENAI = "sk-proj-abcdefghijklmnopqrstuvwxyz012345"
GITHUB = "ghp_abcdefghijklmnopqrstuvwxyz012345"
TELEGRAM = "1234567890:abcdefghijklmnopqrstuvwxyzABCDE"
"""

    assert _codes(content) == {"provider_token_literal"}


def test_guard_detects_private_key_header() -> None:
    content = """
-----BEGIN PRIVATE KEY-----
synthetic-test-material
-----END PRIVATE KEY-----
"""

    assert _codes(content) == {"private_key_material"}


def test_guard_allows_secret_references_empty_defaults_and_public_urls() -> None:
    content = """
OPENAI_API_KEY = SecretStr("")
TOKEN_SECRET_REF = "OPENAI_API_KEY"
PUBLIC_DOCS = "https://docs.railway.com/deployments/healthchecks"
"""

    assert scan_text(Path("app/synthetic_runtime.py"), content) == ()
