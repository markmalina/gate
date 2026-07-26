"""Merge the audio and Beta migration heads.

Revision ID: 0059_merge_audio_beta_heads
Revises: 0058_long_call_chunking, 0058_beta_checkpoint_exports
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0059_merge_audio_beta_heads"
down_revision: tuple[str, str] = (
    "0058_long_call_chunking",
    "0058_beta_checkpoint_exports",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
