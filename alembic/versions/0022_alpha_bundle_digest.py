"""Correct the immutable Alpha canary bundle digest.

Revision ID: 0022_alpha_bundle_digest
Revises: 0021_enable_alpha_canary
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_alpha_bundle_digest"
down_revision: str | None = "0021_enable_alpha_canary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_DIGEST = "8a40570ed64fe29ba56b50975113a89bee5a59c22c4bf9cc56cf0abf75eba578"
_NEW_DIGEST = "558db275ab8424e108aabf06687623a3f37ef6f2ac6ea0cd12eace6962a92bca"


def upgrade() -> None:
    """Pin the revision to the exact deployed bundle bytes."""

    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT id, content_sha256 FROM instruction_revisions "
            "WHERE revision_key = 'alpha-canary-v1' "
            "AND source_repository = 'markmalina/Gate' "
            "AND source_commit_sha = "
            "'0b5295606694c537c2b59915d0c91e789f1cbb51'"
        )
    ).mappings().one_or_none()
    if row is None:
        return
    if row["content_sha256"] == _NEW_DIGEST:
        return
    if row["content_sha256"] != _OLD_DIGEST:
        raise RuntimeError("alpha_canary_revision_digest_conflict")
    bind.execute(
        sa.text(
            "UPDATE instruction_revisions SET content_sha256 = :new_digest "
            "WHERE id = :revision_id AND content_sha256 = :old_digest"
        ),
        {
            "revision_id": row["id"],
            "old_digest": _OLD_DIGEST,
            "new_digest": _NEW_DIGEST,
        },
    )


def downgrade() -> None:
    """Restore the prior metadata value without changing release bindings."""

    op.get_bind().execute(
        sa.text(
            "UPDATE instruction_revisions SET content_sha256 = :old_digest "
            "WHERE revision_key = 'alpha-canary-v1' "
            "AND source_repository = 'markmalina/Gate' "
            "AND source_commit_sha = "
            "'0b5295606694c537c2b59915d0c91e789f1cbb51' "
            "AND content_sha256 = :new_digest"
        ),
        {"old_digest": _OLD_DIGEST, "new_digest": _NEW_DIGEST},
    )
