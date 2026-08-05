"""Owner-only maintenance entry point for the BECOME Pilot Slice 1."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.become_transcript_review import (
    DeliverPilotTranscriptReview,
)
from app.core.config import get_settings
from app.infrastructure.become_learner_invitation import (
    SessionFactoryLearnerInvitationStore,
)
from app.infrastructure.become_media_intake import (
    SessionFactoryBecomeMediaRetention,
)
from app.infrastructure.become_transcript_review import (
    SessionFactoryPilotTranscriptReviewStore,
)
from app.services.telegram import TelegramClient, build_telegram_http_client

logger = logging.getLogger(__name__)
_MUTATING_ACTIONS = {
    "deliver",
    "redisplay",
    "manual-correct",
    "approve",
    "retry-transcription",
    "reconcile-delivery",
}


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


def _exact_binding(args: argparse.Namespace) -> tuple[str, int, str]:
    review_id = _required(args.review_id, "--review-id")
    if not isinstance(args.expected_version, int) or args.expected_version < 1:
        raise RuntimeError("--expected-version is required")
    digest = _required(args.expected_digest, "--expected-digest")
    if len(digest) != 64:
        raise RuntimeError("--expected-digest must be 64 characters")
    return review_id, args.expected_version, digest


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    command = str(args.command)
    if (
        command in _MUTATING_ACTIONS
        and not settings.gate_become_pilot_slice1_enabled
    ):
        raise RuntimeError("BECOME Pilot Slice 1 kill switch is off")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionFactoryPilotTranscriptReviewStore(sessions)
    slice3_store = SessionFactoryLearnerInvitationStore(sessions)
    result: dict[str, object]
    try:
        if command == "slice3-status":
            result = await slice3_store.status(now=datetime.now(UTC))
        elif command == "slice3-rollback-preflight":
            result = await slice3_store.rollback_preflight()
        elif command == "slice3-purge":
            result = await slice3_store.purge_expired(now=datetime.now(UTC))
        elif command == "status":
            result = {
                "schema": "gate.become-pilot-slice1-status@1",
                "state": await store.status(now=datetime.now(UTC)),
                "content_exposed": False,
            }
        elif command == "rollback-preflight":
            result = {
                "schema": "gate.become-pilot-slice1-rollback-preflight@1",
                **await store.rollback_preflight(),
            }
        elif command == "purge":
            now = datetime.now(UTC)
            media = await SessionFactoryBecomeMediaRetention(
                sessions
            ).purge_expired(now=now)
            review = await store.purge_expired(now=now)
            result = {
                "schema": "gate.become-pilot-slice1-purge@1",
                "raw_purged": media.raw_purged,
                "transcripts_purged": media.transcripts_purged,
                **review,
                "content_exposed": False,
            }
        elif command == "deliver":
            async with build_telegram_http_client() as http:
                telegram = TelegramClient(
                    credential=(
                        settings.telegram_become_bot_token.get_secret_value()
                    ),
                    http_client=http,
                )
                disposition = await DeliverPilotTranscriptReview(
                    store=store,
                    telegram=telegram,
                    enabled=True,
                    max_operations=(
                        settings.gate_become_pilot_slice1_max_operations
                    ),
                ).run_once(now=datetime.now(UTC))
            result = {
                "schema": "gate.become-pilot-slice1-delivery@1",
                "disposition": disposition,
                "content_exposed": False,
            }
        elif command == "redisplay":
            review_id, version, digest = _exact_binding(args)
            disposition = await store.owner_redisplay(
                review_id=review_id,
                expected_version=version,
                expected_digest=digest,
                now=datetime.now(UTC),
            )
            result = {
                "schema": "gate.become-pilot-slice1-owner-action@1",
                "action": command,
                "disposition": disposition,
                "content_exposed": False,
            }
        elif command == "manual-correct":
            review_id = _required(args.review_id, "--review-id")
            if not isinstance(args.expected_version, int):
                raise RuntimeError("--expected-version is required")
            text_path = Path(_required(args.text_file, "--text-file"))
            correction = text_path.read_text().strip()
            if not correction:
                raise RuntimeError("correction file is empty")
            disposition = await store.owner_apply_correction(
                review_id=review_id,
                expected_version=args.expected_version,
                correction_text=correction,
                now=datetime.now(UTC),
            )
            result = {
                "schema": "gate.become-pilot-slice1-owner-action@1",
                "action": command,
                "disposition": disposition,
                "content_exposed": False,
            }
        elif command == "approve":
            review_id, version, digest = _exact_binding(args)
            disposition = await store.owner_approve(
                review_id=review_id,
                expected_version=version,
                expected_digest=digest,
                now=datetime.now(UTC),
            )
            result = {
                "schema": "gate.become-pilot-slice1-owner-action@1",
                "action": command,
                "disposition": disposition,
                "content_exposed": False,
            }
        elif command == "reconcile-delivery":
            delivery_id = _required(args.delivery_id, "--delivery-id")
            action = _required(args.reconciliation_action, "--action")
            evidence_digest = _required(
                args.evidence_digest,
                "--evidence-digest",
            )
            disposition = await store.owner_reconcile_delivery(
                delivery_id=delivery_id,
                action=action,
                evidence_digest=evidence_digest,
                external_message_id=args.external_message_id,
                owner_authorized=True,
                now=datetime.now(UTC),
            )
            result = {
                "schema": "gate.become-pilot-slice1-reconciliation@1",
                "action": action,
                "disposition": disposition,
                "content_exposed": False,
            }
        elif command == "retry-transcription":
            if args.confirm_rejected_failure != "REJECTED":
                raise RuntimeError(
                    "--confirm-rejected-failure REJECTED is required"
                )
            disposition = await store.owner_prepare_transcription_retry(
                now=datetime.now(UTC)
            )
            result = {
                "schema": "gate.become-pilot-slice1-owner-action@1",
                "action": command,
                "disposition": disposition,
                "content_exposed": False,
            }
        elif command == "reset-check":
            review_id = _required(args.review_id, "--review-id")
            readiness = await store.owner_reset_ready(review_id=review_id)
            result = {
                "schema": "gate.become-pilot-slice1-reset-check@1",
                **readiness,
            }
        else:
            raise RuntimeError("unknown command")
        logger.info(
            "BECOME Pilot owner action completed: command=%s content_exposed=false",
            command,
        )
        return result
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Content-free BECOME Pilot maintenance"
    )
    parser.add_argument(
        "command",
        choices=(
            "status",
            "rollback-preflight",
            "purge",
            "slice3-status",
            "slice3-rollback-preflight",
            "slice3-purge",
            "deliver",
            "redisplay",
            "manual-correct",
            "approve",
            "retry-transcription",
            "reconcile-delivery",
            "reset-check",
        ),
    )
    parser.add_argument("--review-id")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--expected-digest")
    parser.add_argument("--text-file")
    parser.add_argument("--confirm-rejected-failure")
    parser.add_argument("--delivery-id")
    parser.add_argument(
        "--action",
        dest="reconciliation_action",
        choices=("confirm_sent", "mark_failed", "authorize_retry"),
    )
    parser.add_argument("--evidence-digest")
    parser.add_argument("--external-message-id")
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
