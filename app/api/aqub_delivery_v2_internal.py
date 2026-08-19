"""Default-off signed internal ingress for AQUB v2 delivery commands."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.application.aqub_contract_v2 import (
    AQUB_V2_MAX_JSON_BYTES,
    AQUBContractV2Error,
    parse_contract_json_v2,
    parse_delivery_command_v2,
)
from app.application.aqub_delivery_v2_store import (
    AQUBV2DeliverySQLStore,
    AQUBV2DeliveryStoreError,
)
from app.core.config import Settings, get_settings

router = APIRouter(tags=["internal-aqub-v2"])
_SIGNATURE_RE = re.compile(r"^sha256=([0-9a-f]{64})$")
_MINIMUM_HMAC_SECRET_BYTES = 32


async def get_aqub_v2_delivery_store(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AQUBV2DeliverySQLStore:
    return AQUBV2DeliverySQLStore(
        session,
        schema=settings.gate_aqub_v2_database_schema,
    )


async def _read_bounded_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > AQUB_V2_MAX_JSON_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="AQUB_V2_JSON_TOO_LARGE",
            )
    return bytes(body)


def _verify_signature(raw_body: bytes, supplied: str | None, secret: bytes) -> None:
    match = _SIGNATURE_RE.fullmatch(supplied or "")
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AQUB_V2_SIGNATURE_INVALID",
        )
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AQUB_V2_SIGNATURE_INVALID",
        )


@router.post(
    "/internal/aqub/v2/delivery",
    include_in_schema=False,
)
async def accept_aqub_v2_delivery(
    request: Request,
    store: Annotated[AQUBV2DeliverySQLStore, Depends(get_aqub_v2_delivery_store)],
    settings: Annotated[Settings, Depends(get_settings)],
    signature: Annotated[str | None, Header(alias="X-AQUB-Signature")] = None,
) -> dict[str, object]:
    if not settings.gate_aqub_v2_delivery_endpoint_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    secret = settings.gate_aqub_v2_delivery_hmac_secret.get_secret_value().encode()
    if len(secret) < _MINIMUM_HMAC_SECRET_BYTES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AQUB_V2_DELIVERY_NOT_CONFIGURED",
        )

    raw_body = await _read_bounded_body(request)
    _verify_signature(raw_body, signature, secret)
    try:
        parsed = parse_contract_json_v2(raw_body)
        command = parse_delivery_command_v2(parsed.to_wire())
    except AQUBContractV2Error as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.code,
        ) from exc

    try:
        return await store.accept(command)
    except AQUBV2DeliveryStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.code,
        ) from exc
