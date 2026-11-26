"""MCP read tools for Alpha and Beta."""

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.mcp_results import JsonObject
from app.api.mcp_results import tool_error_result as _tool_error_result
from app.api.mcp_results import tool_result as _tool_result
from app.application.alpha_group import AlphaInstallationState
from app.application.alpha_lifecycle import (
    AlphaLifecycleError,
    AlphaLifecycleService,
)
from app.application.alpha_owner_control import alpha_installations_payload
from app.application.alpha_shadow_reviews import alpha_shadow_reviews_payload
from app.core.config import Settings
from app.core.mcp_security import Principal
from app.infrastructure.alpha_lifecycle import SqlAlphaLifecycleRepository
from app.infrastructure.alpha_owner_test_runtime import (
    AlphaOwnerTestRuntimeError,
    observe_owner_test_presence,
    verify_owner_test_review_replay,
)
from app.infrastructure.alpha_shadow_reviews import list_alpha_shadow_reviews
from app.infrastructure.beta_checkpoint_export import (
    BetaCheckpointExportError,
    export_beta_space_checkpoint,
)
from app.infrastructure.telegram_webhook_status import (
    TelegramWebhookStatusError,
    get_alpha_webhook_status,
    get_beta_provider_probe,
)
from app.services.telegram import build_telegram_http_client


async def _beta_export_space_checkpoint_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    if (
        not isinstance(raw_ref, str)
        or not raw_ref
        or len(raw_ref) > 128
        or arguments.get("confirmed") is not True
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="installation_ref and confirmed=true are required",
        )

    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installations = await service.list_status(
            principal.access_scope.workspace_id
        )
    except AlphaLifecycleError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
    installation = next(
        (
            item
            for item in installations
            if item.installation_key == raw_ref
        ),
        None,
    )
    if (
        installation is None
        or installation.state != AlphaInstallationState.ACTIVE
    ):
        return _tool_error_result(
            request_id=request_id,
            code="beta_checkpoint_installation_not_active",
            message="The exact Beta Space is not active",
        )

    try:
        payload = await export_beta_space_checkpoint(
            session,
            installation_ref=raw_ref,
        )
    except BetaCheckpointExportError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message="Beta checkpoint export failed closed",
        )
    return _tool_result(
        payload,
        (
            f"Encrypted Beta checkpoint: {payload['sequence_start']}-"
            f"{payload['sequence_end']}; events={payload['event_count']}"
        ),
        request_id,
    )


async def _beta_get_provider_probe_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    del principal, session
    if set(arguments) != {"confirmed"} or arguments.get("confirmed") is not True:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="confirmed=true is required",
        )
    if (
        settings.gate_env != "production"
        or not settings.gate_beta_connector_enabled
    ):
        return _tool_error_result(
            request_id=request_id,
            code="beta_probe_not_production_ready",
            message="Beta provider probe is not production-ready",
        )
    try:
        async with build_telegram_http_client() as http_client:
            probe = await get_beta_provider_probe(
                settings=settings,
                http_client=http_client,
            )
            if (
                not probe["webhook"]["exact_match"]
                or not probe["webhook"]["actual_origin_match"]
                or "callback_query" not in probe["webhook"]["allowed_updates"]
                or not probe["identity"]["ordinary_group_messages_available"]
                or probe["identity"]["privacy_mode_enabled"]
            ):
                return _tool_error_result(
                    request_id=request_id,
                    code="beta_binding_provider_not_exact",
                    message="Beta provider state is not exact",
                )
            wedding_activation = {
                "state": "binding_creation_disabled",
                "candidate_count": 0,
                "binding_created": False,
                "prompt_required": False,
                "prompt_delivery": "not_attempted",
                "private_identifiers_exposed": False,
                "private_payload_exposed": False,
                "consent_digest_exposed": False,
            }
            return _tool_result(
                {"probe": probe, "activation": wedding_activation},
                (
                    "Beta provider probe: "
                    f"activation={wedding_activation['state']}; "
                    "candidate_count=0; binding_creation=disabled; "
                    "prompt_delivery=not_attempted"
                ),
                request_id,
            )
    except TelegramWebhookStatusError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.code,
        )


async def _alpha_list_installations_tool_result(
    *,
    request_id: Any,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installations = await service.list_status(
            principal.access_scope.workspace_id
        )
    except AlphaLifecycleError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
    payload: JsonObject = alpha_installations_payload(installations)
    lines = [f"Alpha installations: {payload['count']}"]
    for item in payload["items"]:
        lines.append(
            f"- {item['group_label']}: {item['state']}; "
            f"actions={','.join(item['available_actions']) or 'none'}"
        )
    return _tool_result(payload, "\n".join(lines), request_id)


async def _alpha_get_webhook_status_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    if (
        not isinstance(raw_ref, str)
        or not raw_ref
        or len(raw_ref) > 128
        or arguments.get("confirmed") is not True
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="installation_ref and confirmed=true are required",
        )

    service = AlphaLifecycleService(SqlAlphaLifecycleRepository(session))
    try:
        installations = await service.list_status(
            principal.access_scope.workspace_id
        )
    except AlphaLifecycleError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
        )
    installation = next(
        (
            item
            for item in installations
            if item.installation_key == raw_ref
        ),
        None,
    )
    if installation is None:
        return _tool_error_result(
            request_id=request_id,
            code="alpha_installation_not_found",
            message="Alpha installation not found in the allowed scope",
        )
    if (
        installation.state is not AlphaInstallationState.ACTIVE
        or not installation.connector_enabled
    ):
        return _tool_error_result(
            request_id=request_id,
            code="alpha_webhook_installation_not_ready",
            message="Alpha installation is not active and connector-ready",
        )

    try:
        async with httpx.AsyncClient() as http_client:
            webhook = await get_alpha_webhook_status(
                settings=settings,
                http_client=http_client,
            )
    except TelegramWebhookStatusError as exc:
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message=exc.code,
        )

    payload = {
        "installation_ref": raw_ref,
        "webhook": webhook,
    }
    match = "exact" if webhook["exact_match"] else "mismatch"
    return _tool_result(
        payload,
        f"Alpha webhook: {match}; pending="
        f"{webhook['pending_update_count']}",
        request_id,
    )


async def _alpha_list_shadow_reviews_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_limit = arguments.get("limit", 20)
    if (
        not isinstance(raw_limit, int)
        or isinstance(raw_limit, bool)
        or not 1 <= raw_limit <= 50
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="limit must be an integer between 1 and 50",
        )
    reviews = await list_alpha_shadow_reviews(
        session,
        workspace_key=principal.access_scope.workspace_id,
        limit=raw_limit,
    )
    payload: JsonObject = alpha_shadow_reviews_payload(reviews)
    lines = [
        f"Alpha shadow reviews: {payload['count']}",
        "Telegram delivery: blocked",
    ]
    for item in payload["items"]:
        lines.append(
            f"- {item['group_label']}: {item['status']}; "
            f"outcome={item['outcome'] or 'pending'}"
        )
    return _tool_result(payload, "\n".join(lines), request_id)


async def _alpha_get_test_group_presence_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
    settings: Settings,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    if not isinstance(raw_ref, str) or arguments.get("confirmed") is not True:
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message="installation_ref and confirmed=true are required",
        )
    try:
        async with httpx.AsyncClient() as http_client:
            presence = await observe_owner_test_presence(
                session=session,
                settings=settings,
                http_client=http_client,
                workspace_key=principal.access_scope.workspace_id,
                installation_ref=raw_ref,
            )
    except AlphaOwnerTestRuntimeError as exc:
        await session.rollback()
        return _tool_error_result(
            request_id=request_id,
            code=exc.code,
            message="Alpha owner-test presence probe failed closed",
        )
    payload = presence.payload()
    return _tool_result(
        payload,
        (
            f"Alpha test group {payload['group_label']}: "
            f"{payload['state']}; members={payload['member_count']}"
        ),
        request_id,
    )


async def _alpha_verify_review_replay_tool_result(
    *,
    request_id: Any,
    arguments: JsonObject,
    principal: Principal,
    session: AsyncSession,
) -> JsonObject:
    raw_ref = arguments.get("installation_ref")
    raw_turn = arguments.get("turn_ref")
    raw_digest = arguments.get("expected_candidate_sha256")
    if (
        not isinstance(raw_ref, str)
        or not isinstance(raw_turn, str)
        or not isinstance(raw_digest, str)
    ):
        return _tool_error_result(
            request_id=request_id,
            code="invalid_arguments",
            message=(
                "installation_ref, turn_ref and expected_candidate_sha256 "
                "are required"
            ),
        )
    if isinstance(raw_ref, str) and raw_ref.startswith("aqub-"):
        return _tool_error_result(
            request_id=request_id,
            code="aqub_project_frozen",
            message="AQUB is frozen and disconnected from Gate",
        )

    try:
        payload = await verify_owner_test_review_replay(
            session=session,
            workspace_key=principal.access_scope.workspace_id,
            installation_ref=raw_ref,
            turn_ref=raw_turn,
            expected_candidate_sha256=raw_digest,
        )
    except (AlphaOwnerTestRuntimeError, ValueError) as exc:
        await session.rollback()
        code = exc.code if isinstance(exc, AlphaOwnerTestRuntimeError) else str(exc)
        return _tool_error_result(
            request_id=request_id,
            code=code,
            message="Alpha owner-test replay verification failed closed",
        )
    return _tool_result(
        payload,
        (
            f"Alpha review replay proof: {payload['state']}; "
            f"replay={payload['replay']}"
        ),
        request_id,
    )
