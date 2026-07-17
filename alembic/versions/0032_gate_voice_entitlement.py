"""Seed the dormant Gate Telegram voice transcription entitlement.

Revision ID: 0032_gate_voice_entitlement
Revises: 0031_billing_ledger
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032_gate_voice_entitlement"
down_revision: str | None = "0031_billing_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTITLEMENT_KEY = "gate-telegram-voice-v1"


def upgrade() -> None:
    """Give each active workspace a fail-closed USD 1 monthly voice ceiling."""

    op.execute(
        f"""
        INSERT INTO billing_entitlements (
            id,
            workspace_id,
            entitlement_key,
            product,
            scope_kind,
            scope_ref,
            plan_key,
            state,
            valid_from,
            valid_until,
            hard_limit_microusd
        )
        SELECT
            md5(workspaces.id::text || '-{_ENTITLEMENT_KEY}')::uuid,
            workspaces.id,
            '{_ENTITLEMENT_KEY}',
            'gate',
            'telegram_connector',
            'telegram_business',
            'gate-voice-initial',
            'active',
            TIMESTAMPTZ '2026-07-01 00:00:00+00',
            NULL,
            1000000
        FROM workspaces
        WHERE workspaces.is_active IS TRUE
        ON CONFLICT (workspace_id, entitlement_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Remove only the entitlement introduced by this revision."""

    op.execute(
        f"""
        DELETE FROM billing_entitlements
        WHERE entitlement_key = '{_ENTITLEMENT_KEY}'
          AND product = 'gate'
          AND scope_kind = 'telegram_connector'
          AND scope_ref = 'telegram_business'
        """
    )
