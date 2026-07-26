"""Merge concurrent long-call and Beta checkpoint migration heads.

Revision ID: 0059_merge_checkpoint_heads
Revises: 0058_long_call_chunking, 0058_beta_checkpoint_exports
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0059_merge_checkpoint_heads"
down_revision: str | Sequence[str] | None = (
    "0058_long_call_chunking",
    "0058_beta_checkpoint_exports",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both transactional histories without additional DDL."""


def downgrade() -> None:
    """Split back to both predecessor heads without data mutation."""
