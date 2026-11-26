"""Project gitleaks JSON to a bounded, value-free publication-readiness receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

KNOWN_CLASSES = frozenset({
    "generic-api-key", "github-pat", "github-fine-grained-pat", "private-key",
    "telegram-bot-api-token", "openai-api-key", "aws-access-token", "gcp-api-key",
})
SHA = re.compile(r"[0-9a-f]{40}")
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_FINDINGS = 200000


def project(
    raw: object, *, exit_code: int, source_sha: str, complete_history: bool,
    ref_count: int, commit_count: int, refs_digest: str,
) -> dict:
    """Never copy scanner strings into output except a fixed class allowlist."""
    safe_source = source_sha if SHA.fullmatch(source_sha) else None
    out = {
        "schema_version": "gate.history-secret-scan.v1",
        "source_sha": safe_source,
        "scanner": "gitleaks/8.30.1",
        "scope": "fetched-git-refs-only; not issues, comments, attachments or runtime",
        "scan_complete": False,
        "ref_count": None,
        "commit_count": None,
        "refs_digest": None,
        "result": "INCOMPLETE",
        "count": 0,
        "findings": [],
    }
    if not safe_source or complete_history is not True or exit_code not in (0, 23):
        return out
    if (type(ref_count) is not int or type(commit_count) is not int
            or not 0 < ref_count < 1000000 or not 0 < commit_count < 100000000
            or not re.fullmatch(r"[0-9a-f]{64}", refs_digest)):
        return out
    out.update(ref_count=ref_count, commit_count=commit_count, refs_digest=refs_digest)
    if not isinstance(raw, list) or len(raw) > MAX_FINDINGS:
        return out
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            return out
        commit = item.get("Commit")
        start = item.get("StartLine")
        end = item.get("EndLine")
        path = item.get("File")
        rule = item.get("RuleID")
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            return out
        if type(start) is not int or type(end) is not int or not 0 < start <= end < 100000000:
            return out
        if not isinstance(path, str) or not path or len(path) > 4096 or not isinstance(rule, str):
            return out
        findings.append({
            "commit": commit,
            "path_sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
            "start_line": start,
            "end_line": end,
            "class": rule if rule in KNOWN_CLASSES else "other",
        })
    if (exit_code == 0 and findings) or (exit_code == 23 and not findings):
        return out
    out.update(
        scan_complete=True, result="FINDINGS" if findings else "NO_FINDINGS",
        count=len(findings), findings=findings,
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--complete-history", choices=("true", "false"), required=True)
    parser.add_argument("--ref-count", type=int, required=True)
    parser.add_argument("--commit-count", type=int, required=True)
    parser.add_argument("--refs-digest", required=True)
    args = parser.parse_args()
    raw = None
    try:
        p = Path(args.raw)
        if p.stat().st_size <= MAX_REPORT_BYTES:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if raw is None and args.exit_code == 0:
                raw = []  # gitleaks JSON may encode a nil findings slice as null.
    except (OSError, ValueError, UnicodeError):
        pass  # No raw error, scanner output, path or exception reaches logs.
    receipt = project(
        raw, exit_code=args.exit_code, source_sha=args.source_sha,
        complete_history=args.complete_history == "true",
        ref_count=args.ref_count, commit_count=args.commit_count, refs_digest=args.refs_digest,
    )
    Path(args.output).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"history_scan result={receipt['result']} count={receipt['count']}")
    return 0 if receipt["result"] == "NO_FINDINGS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

