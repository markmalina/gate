"""Enumerate Telegram writer candidates for V2 review; not an exhaustive proof.

AST avoids executing application imports. Frozen/disabled historical paths are
included so review can explicitly prove exclusion rather than miss a bypass.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITE_METHODS = frozenset(
    {
        "send_message",
        "send_text",
        "send_plain_message",
        "send_formatted_message",
        "send_rich_message",
        "send_business_document",
        "send_file",
        "send_exact",
        "edit_message",
        "edit_message_text",
        "edit_message_reply_markup",
        "delete_messages",
        "forward_messages",
        "SendMessageRequest",
        "SendMediaRequest",
        "SendMultiMediaRequest",
        "EditMessageRequest",
        "DeleteMessagesRequest",
        "ForwardMessagesRequest",
        "CreateChannelRequest",
        "CreateChatRequest",
    }
)


def inventory(root: Path = ROOT) -> dict[str, object]:
    calls = []
    for path in sorted((root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path.relative_to(root)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in WRITE_METHODS:
                continue
            calls.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "line": node.lineno,
                    "method": node.func.attr,
                    "receiver": ast.unparse(node.func.value),
                    "business_connection_argument_present": any(
                        keyword.arg == "business_connection_id" for keyword in node.keywords
                    ),
                    "shared_admission_verified": False,
                }
            )
    return {
        "schema": "gate.personal-telegram.writer-candidates/v1",
        "provider_calls": 0,
        "classification": "candidate_call_sites_not_unique_provider_effects",
        "exhaustive_writer_closure_verified": False,
        "calls": calls,
        "additional_review_required": [
            "dynamic_http_calls_and_raw_rpc",
            "bot_api_wrappers_and_abstract_adapters",
            "business_and_personal_actor_identity",
            "frozen_disabled_routes",
            "shared_admission_at_actual_dispatch",
            "unknown_historical_attempts",
            "outbound_echo_causal_task_ids",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(inventory(), indent=2, sort_keys=True))
