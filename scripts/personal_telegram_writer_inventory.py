"""Enumerate Telegram writer candidates for V2 review; not an exhaustive proof.

AST avoids executing application imports. Frozen/disabled historical paths are
included so review can explicitly prove exclusion rather than miss a bypass.
"""

from __future__ import annotations

import ast
import json
import re
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
        "send_business_photo",
        "send_business_album",
        "send_business_rich_media",
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
    bot_endpoints = []
    session_clients = []
    paths = sorted(
        path for directory in ("app", "src") for path in (root / directory).rglob("*.py")
    )
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path.relative_to(root)))
        constructors = {
            name.asname or name.name
            for imp in ast.walk(tree)
            if isinstance(imp, ast.ImportFrom) and imp.module == "telethon"
            for name in imp.names
            if name.name == "TelegramClient"
        }
        location = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in constructors
            ):
                session_clients.append({"path": location, "line": node.lineno})
            if isinstance(node, ast.JoinedStr):
                fragments = [
                    v.value
                    for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
                if any(value.endswith("/bot") for value in fragments) and fragments:
                    match = re.fullmatch(
                        r"/((?:send|edit|delete|forward|copy|answer)[A-Z][A-Za-z]+)", fragments[-1]
                    )
                    if match:
                        bot_endpoints.append(
                            {"path": location, "line": node.lineno, "method": match[1]}
                        )
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
        "personal_session_constructors": session_clients,
        "bot_api_effect_endpoints": bot_endpoints,
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
