"""Print provider-free, version-pinned C01-C23 method/argument evidence as JSON."""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.personal_telegram.capability_catalog import CAPABILITIES  # noqa: E402
from app.personal_telegram.command_scope import CommandRuntimeError  # noqa: E402
from app.personal_telegram.session_persistence import SUPPORTED_TELETHON  # noqa: E402


def inventory() -> dict[str, Any]:
    import telethon
    from telethon.tl import alltlobjects, functions

    if telethon.__version__ not in SUPPORTED_TELETHON or alltlobjects.LAYER != 227:
        raise CommandRuntimeError("telethon_method_contract_unverified")
    methods = {}
    for path in sorted({path for record in CAPABILITIES for path in record.methods}):
        namespace, name = path.split(".")
        constructor = getattr(getattr(functions, namespace), name, None)
        if constructor is None:
            raise CommandRuntimeError("telethon_method_contract_missing")
        parameters = inspect.signature(constructor).parameters
        methods[path] = {
            "constructor_id": hex(constructor.CONSTRUCTOR_ID),
            "required_arguments": [
                name
                for name, parameter in parameters.items()
                if parameter.default is inspect.Parameter.empty
            ],
            "optional_arguments": [
                name
                for name, parameter in parameters.items()
                if parameter.default is not inspect.Parameter.empty
            ],
            "library_method_present": True,
            "gate_dispatcher_present": False,
            "deployed_runtime_verified": False,
            "actual_work_verified": False,
        }
    return {
        "schema": "gate.personal-telegram.contract-inventory/v1",
        "telethon_version": telethon.__version__,
        "telegram_layer": alltlobjects.LAYER,
        "account_boundary": "ru_only",
        "live_provider_calls": 0,
        "capabilities": [{**asdict(record), "scope": record.scope} for record in CAPABILITIES],
        "methods": methods,
        "excluded": [
            "by_account",
            "raw_rpc",
            "secret_chats",
            "calls",
            "payments",
            "paid_messages",
            "paid_reactions",
            "paid_join",
            "account_security_changes",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = json.dumps(inventory(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
