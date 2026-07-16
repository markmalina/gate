"""Inspect a disposable restored PostgreSQL database without reading row content."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import asyncpg


async def _inspect(database_url: str) -> dict[str, object]:
    connection = await asyncpg.connect(database_url)
    try:
        table_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            """
        )
        revision_count = await connection.fetchval(
            "SELECT count(*) FROM alembic_version"
        )
    finally:
        await connection.close()
    return {
        "schema_version": "postgres-restore-evidence-v1",
        "environment": "disposable_ci",
        "restore_completed": True,
        "table_count": int(table_count),
        "alembic_revision_rows": int(revision_count),
        "row_content_inspected": False,
        "production_database_used": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(_inspect(args.database_url))
    if report["table_count"] == 0 or report["alembic_revision_rows"] != 1:
        raise RuntimeError("postgres_restore_evidence_invalid")
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
