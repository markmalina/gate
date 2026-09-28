"""Run the bounded reply-sequence review against disposable PostgreSQL 17.

This is execution evidence, never an independent acceptance verdict. The reviewer
must start their own job and interpret its evidence against the exact candidate.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "test_reply_sequence_review_postgres": 15,
    "test_reply_sequence_dispatch_postgres": 23,
    "test_reply_sequence_continuity_postgres": 3,
    "test_reply_sequence_canary_postgres": 3,
    "test_reply_sequence_acceptance_postgres": 37,
    "test_reply_sequence_context": 17,
    "test_reply_sequence_supervisor": 16,
    "test_reply_sequence_cooldown": 16,
    "test_reply_sequences": 20,
    "test_telegram_retry_after": 29,
    "test_telegram_client": 19,
}


class ReviewBlocked(Exception):
    """A safe, fixed reason code; never include a DSN or provider payload."""


def disposable_dsn(value: str) -> str:
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "postgresql+asyncpg"
            and url.hostname in {"127.0.0.1", "localhost"}
            and url.port is not None
            and url.username == "postgres"
            and url.path == "/gate_reply_sequence_review"
            and not url.query
            and not url.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ReviewBlocked("disposable_loopback_database_required")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


async def database_preflight(dsn: str) -> str:
    import asyncpg

    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        version = await connection.fetchval("SHOW server_version_num")
        if not 170000 <= int(version) < 180000:
            raise ReviewBlocked("postgresql_17_required")
        # Never point this runner at an existing application database, even locally.
        tables = await connection.fetchval(
            "SELECT count(*) FROM pg_catalog.pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
        )
        if tables:
            raise ReviewBlocked("empty_disposable_database_required")
        return str(version)
    finally:
        await connection.close()


def validate_junit(path: Path) -> dict[str, int]:
    root = ElementTree.parse(path).getroot()
    cases = list(root.iter("testcase"))
    if next(root.iter("skipped"), None) is not None:
        raise ReviewBlocked("skipped_cases")
    if next(root.iter("failure"), None) is not None or next(root.iter("error"), None) is not None:
        raise ReviewBlocked("failed_cases")
    counts: Counter[str] = Counter()
    identities = set()
    for case in cases:
        identity = (case.get("classname", ""), case.get("name", ""))
        if not all(identity) or identity in identities:
            raise ReviewBlocked("invalid_or_duplicate_case")
        identities.add(identity)
        counts[identity[0].removeprefix("tests.")] += 1
    if dict(counts) != CASES:
        raise ReviewBlocked("test_inventory_mismatch")
    return dict(counts)


def verify_source(expected: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ReviewBlocked("exact_candidate_sha_required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != expected:
        raise ReviewBlocked("candidate_sha_mismatch")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
    ).strip():
        raise ReviewBlocked("tracked_source_modified")
    return head


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disposable-database", action="store_true", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    # A stale report from a previous run must never be mistaken for this run.
    output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema": "gate.reply-sequence-review/v1",
        "candidate_sha": args.candidate_sha,
        "started_at": datetime.now(UTC).isoformat(),
        "execution_status": "blocked",
        "acceptance": "not_issued",
        "python": sys.version.split()[0],
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "actor": os.environ.get("GITHUB_ACTOR"),
        "triggering_actor": os.environ.get("GITHUB_TRIGGERING_ACTOR"),
        "expected_cases": CASES,
    }
    result = 1
    try:
        receipt["verified_head"] = verify_source(args.candidate_sha)
        dsn = disposable_dsn(os.environ.get("DATABASE_URL", ""))
        receipt["postgres_version_num"] = asyncio.run(database_preflight(dsn))
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "-rs",
            f"--junitxml={output / 'junit.xml'}",
            *(f"tests/{name}.py" for name in CASES),
        ]
        receipt["command"] = command
        env = dict(os.environ)
        # Do not permit caller flags/plugins to narrow or replace the selected suite.
        for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            env.pop(key, None)
        with (output / "pytest.log").open("w") as log:
            run = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=360,
                check=False,
            )
        print((output / "pytest.log").read_text(), end="")
        receipt["pytest_exit_code"] = run.returncode
        if run.returncode:
            raise ReviewBlocked("pytest_failed")
        receipt["passed_cases"] = validate_junit(output / "junit.xml")
        receipt["passed"] = sum(CASES.values())
        receipt["skipped"] = 0
        receipt["execution_status"] = "succeeded"
        result = 0
    except ReviewBlocked as exc:
        receipt["reason"] = str(exc)
    except Exception as exc:
        # Connection exceptions can contain credentials; record only the class.
        receipt["reason"] = "execution_error"
        receipt["error_type"] = type(exc).__name__
    finally:
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
