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
        "trigger_text": review.trigger_text,
        "candidate_text": review.candidate_text,
        "candidate_sha256": review.candidate_sha256,
        "context_message_count": review.context_message_count,
        "created_at": review.created_at.isoformat(),
        "sent_to_telegram": False,
    }
