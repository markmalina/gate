"""Scan the current tracked runtime tree for high-confidence sensitive literals."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCANNED_DIRECTORIES = frozenset({"app", "scripts", ".github", "agent_bundles"})
SCANNED_ROOT_FILES = frozenset(
    {
        "Dockerfile",
        "Dockerfile.contact-sync",
        "railway.toml",
        "pyproject.toml",
    }
)
SCANNED_SUFFIXES = frozenset(
    {".py", ".json", ".md", ".toml", ".txt", ".yaml", ".yml"}
)
SELF_PATH = Path("scripts/repository_sensitive_data_guard.py")


@dataclass(frozen=True, slots=True)
class SensitivePattern:
    code: str
    expression: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line: int
    code: str


PATTERNS = (
    SensitivePattern(
        code="temporary_sensitive_symbol",
        expression=re.compile(
            r"\b_TMP_(?:PROMPT|AUDIO_URL|[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY))\b"
        ),
    ),
    SensitivePattern(
        code="literal_sensitive_assignment",
        expression=re.compile(
            r"""(?ix)
            \b[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY)\b
            \s*=\s*
            ["'][A-Za-z0-9._~+/=-]{16,}["']
            """
        ),
    ),
    SensitivePattern(
        code="fixed_private_media_host",
        expression=re.compile(r"drive[.]usercontent[.]google[.]com/download", re.IGNORECASE),
    ),
    SensitivePattern(
        code="embedded_bearer_literal",
        expression=re.compile(
            r"""(?ix)
            ["']Authorization["']\s*:\s*
            ["']Bearer\s+[A-Za-z0-9._~+/=-]{16,}["']
            """
        ),
    ),
    SensitivePattern(
        code="provider_token_literal",
        expression=re.compile(
            r"""(?x)
            \b(?:
                sk-(?:proj-)?[A-Za-z0-9_-]{20,}
                |
                gh[pousr]_[A-Za-z0-9]{20,}
                |
                [0-9]{8,12}:[A-Za-z0-9_-]{30,}
            )\b
            """
        ),
    ),
    SensitivePattern(
        code="private_key_material",
        expression=re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
    ),
)


def scan_text(path: Path, content: str) -> tuple[Finding, ...]:
    """Return redacted finding coordinates without retaining matched content."""

    findings: list[Finding] = []
    for pattern in PATTERNS:
        for match in pattern.expression.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            findings.append(Finding(path=path, line=line, code=pattern.code))
    return tuple(findings)


def tracked_runtime_files(root: Path) -> tuple[Path, ...]:
    """List current tracked runtime files; never inspect Git history."""

    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=False,
    )
    candidates: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        if relative == SELF_PATH:
            continue
        if relative.suffix not in SCANNED_SUFFIXES:
            continue
        if (
            relative.name in SCANNED_ROOT_FILES
            or relative.parts[0] in SCANNED_DIRECTORIES
        ):
            candidates.append(relative)
    return tuple(sorted(candidates))


def scan_current_tree(root: Path) -> tuple[Finding, ...]:
    """Scan only current tracked text files from bounded runtime paths."""

    findings: list[Finding] = []
    for relative in tracked_runtime_files(root):
        absolute = root / relative
        try:
            content = absolute.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append(Finding(path=relative, line=0, code="unreadable_tracked_text"))
            continue
        findings.extend(scan_text(relative, content))
    return tuple(findings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = scan_current_tree(args.root.resolve())
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.code}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
