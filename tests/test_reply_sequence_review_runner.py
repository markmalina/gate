"""The independent-review runner must never convert missing PG/skips into PASS."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

spec = importlib.util.spec_from_file_location(
    "reply_sequence_review",
    Path(__file__).resolve().parents[1] / "scripts/reply_sequence_review.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def report(path, *, alteration=None):
    root = ElementTree.Element("testsuites")
    suite = ElementTree.SubElement(root, "testsuite")
    for module, count in runner.CASES.items():
        for index in range(count):
            ElementTree.SubElement(suite, "testcase", classname=f"tests.{module}", name=str(index))
    if alteration:
        alteration(root, suite)
    ElementTree.ElementTree(root).write(path)
    return path


def test_complete_inventory_passes(tmp_path):
    counts = runner.validate_junit(report(tmp_path / "result.xml"))
    assert counts == runner.CASES
    assert sum(counts.values()) == 192


@pytest.mark.parametrize("tag", ["skipped", "failure", "error"])
def test_any_nonpassing_result_fails(tmp_path, tag):
    path = report(
        tmp_path / "result.xml",
        alteration=lambda root, suite: ElementTree.SubElement(suite[0], tag),
    )
    with pytest.raises(runner.ReviewBlocked):
        runner.validate_junit(path)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unexpected", "empty"])
def test_incomplete_or_changed_inventory_fails(tmp_path, mutation):
    def alter(root, suite):
        if mutation == "missing":
            suite.remove(suite[0])
        elif mutation == "duplicate":
            suite[1].set("name", suite[0].get("name"))
        elif mutation == "unexpected":
            suite[0].set("classname", "unrelated_test")
        else:
            suite.clear()

    with pytest.raises(runner.ReviewBlocked):
        runner.validate_junit(report(tmp_path / "result.xml", alteration=alter))


@pytest.mark.parametrize(
    "dsn",
    [
        "",
        "postgresql+asyncpg://postgres:pw@prod.example:5432/gate_reply_sequence_review",
        "postgresql+asyncpg://postgres:pw@localhost:5432/gate",
        "postgresql+asyncpg://postgres:pw@localhost:5432/gate_reply_sequence_review?host=prod.example",
        "postgresql+asyncpg://postgres:pw@localhost:bad/gate_reply_sequence_review",
        "postgresql+asyncpg://postgres:pw@localhost/gate_reply_sequence_review",
    ],
)
def test_missing_or_non_disposable_database_fails(dsn):
    with pytest.raises(runner.ReviewBlocked, match="disposable_loopback_database_required"):
        runner.disposable_dsn(dsn)


def test_loopback_disposable_database_allowed():
    value = "postgresql+asyncpg://postgres:synthetic@127.0.0.1:5432/gate_reply_sequence_review"
    assert runner.disposable_dsn(value) == value.replace("+asyncpg", "")


@pytest.mark.parametrize("head,dirty", [("b" * 40, ""), ("a" * 40, " M app/runtime.py")])
def test_source_drift_fails(monkeypatch, head, dirty):
    outputs = iter([head, dirty])
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **k: next(outputs))
    with pytest.raises(runner.ReviewBlocked):
        runner.verify_source("a" * 40)


@pytest.mark.parametrize("version,tables", [("160000", 0), ("180000", 0), ("170005", 1)])
def test_wrong_postgres_or_existing_database_fails(monkeypatch, version, tables):
    import asyncpg

    values = iter([version, tables])
    closed = []

    async def fetchval(query):
        return next(values)

    async def close():
        closed.append(True)

    async def connect(*args, **kwargs):
        return SimpleNamespace(fetchval=fetchval, close=close)

    monkeypatch.setattr(asyncpg, "connect", connect)
    with pytest.raises(runner.ReviewBlocked):
        runner.asyncio.run(runner.database_preflight("unused"))
    assert closed == [True]


def test_preflight_failure_writes_blocked_receipt_without_credentials(tmp_path, monkeypatch):
    output = tmp_path / "evidence"
    monkeypatch.setattr(
        sys,
        "argv",
        ["review", "--candidate-sha", "a" * 40, "--disposable-database", "--output", str(output)],
    )
    monkeypatch.setattr(runner, "verify_source", lambda sha: sha)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://secret@remote:5432/production")
    assert runner.main() == 1
    receipt = (output / "receipt.json").read_text()
    assert "secret" not in receipt and "production" not in receipt
    assert json.loads(receipt)["execution_status"] == "blocked"
    assert json.loads(receipt)["acceptance"] == "not_issued"


@pytest.mark.parametrize("exit_code,skipped,expected", [(0, False, 0), (1, False, 1), (0, True, 1)])
def test_execution_requires_both_pytest_success_and_complete_report(
    tmp_path, monkeypatch, exit_code, skipped, expected
):
    output = tmp_path / "evidence"
    monkeypatch.setattr(
        sys,
        "argv",
        ["review", "--candidate-sha", "a" * 40, "--disposable-database", "--output", str(output)],
    )
    monkeypatch.setattr(runner, "verify_source", lambda sha: sha)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:pw@localhost:5432/gate_reply_sequence_review"
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nonexistent")

    async def preflight(dsn):
        return "170006"

    def run(command, **kwargs):
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert len([arg for arg in command if arg.startswith("tests/")]) == 9

        def alteration(root, suite):
            ElementTree.SubElement(suite[0], "skipped")

        report(output / "junit.xml", alteration=alteration if skipped else None)
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(runner, "database_preflight", preflight)
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == expected
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["acceptance"] == "not_issued"
    assert (receipt["execution_status"] == "succeeded") is (expected == 0)


def test_stale_output_cannot_be_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["review", "--candidate-sha", "a" * 40, "--disposable-database", "--output", str(tmp_path)],
    )
    with pytest.raises(FileExistsError):
        runner.main()
