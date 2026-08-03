"""Merge Quest corrective and Alfa transcription migration branches.

Revision ID: 0083_become_quest_corrective_m
Revises: 0083_become_quest_corrective, 0083_alpha_transcription_product
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0083_become_quest_corrective_m"
down_revision: tuple[str, str] = (
    "0083_become_quest_corrective",
    "0083_alpha_transcription_product",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two independently additive migration branches."""


def downgrade() -> None:
    """Split back to both parent revisions without changing schema."""
