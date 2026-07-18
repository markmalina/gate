"""Owner-safe review views for Alpha model shadow proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AlphaShadowReview:
    turn_ref: str
    installation_ref: str
    group_label: str
    deployment_key: str
    status: str
    outcome: str | None
    result_code: str | None
    trigger_text: str | None
    input_origin: str
    candidate_text: str | None
    candidate_sha256: str | None
    context_message_count: int
    created_at: datetime


def alpha_shadow_reviews_payload(
    reviews: tuple[AlphaShadowReview, ...],
) -> dict[str, object]:
    """Serialize shadow evidence without provider dialog/message identifiers."""

    return {
        "count": len(reviews),
        "items": [_review_payload(review) for review in reviews],
        "telegram_delivery_blocked": True,
        "provider_dialog_ids_exposed": False,
    }


def _review_payload(review: AlphaShadowReview) -> dict[str, object]:
    return {
        "turn_ref": review.turn_ref,
        "installation_ref": review.installation_ref,
        "group_label": review.group_label,
        "deployment_key": review.deployment_key,
        "status": review.status,
        "outcome": review.outcome,
        "result_code": review.result_code,
        "trigger_text": _bounded(review.trigger_text, 4_000),
        "input_origin": review.input_origin,
        "candidate_text": _bounded(review.candidate_text, 12_000),
        "candidate_sha256": review.candidate_sha256,
        "context_message_count": review.context_message_count,
        "created_at": review.created_at.isoformat(),
        "sent_to_telegram": False,
    }


def _bounded(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None
