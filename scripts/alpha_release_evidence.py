"""Build deterministic CI evidence for Alpha's safe pre-billing baseline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from app.application.alpha_model_readiness import AlphaModelReadinessFacts
from app.application.alpha_release_evidence import alpha_release_evidence_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = alpha_release_evidence_payload(
        AlphaModelReadinessFacts(
            runtime_enabled=True,
            data_gate_approved=True,
            api_key_configured=True,
            staged_deployments=1,
            monthly_budget_microusd=50_000_000,
            reserved_microusd=0,
            model_turns=0,
            proposed_turns=0,
            failed_turns=0,
        )
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
