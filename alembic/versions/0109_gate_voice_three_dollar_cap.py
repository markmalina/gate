"""Raise only Mark's existing Gate voice entitlement to USD 3 monthly.

Revision ID: 0109_gate_voice_three_dollar_cap
Revises: 0108_short_voice_headroom
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0109_gate_voice_three_dollar_cap"
down_revision: str | None = "0108_short_voice_headroom"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WHERE = """
  FROM workspaces AS w
 WHERE billing_entitlements.workspace_id = w.id
   AND w.key = 'mark-personal' AND w.is_active IS TRUE
   AND billing_entitlements.entitlement_key = 'gate-telegram-voice-v1'
   AND billing_entitlements.product = 'gate'
   AND billing_entitlements.scope_kind = 'telegram_connector'
   AND billing_entitlements.scope_ref = 'telegram_business'
   AND billing_entitlements.state = 'active'
   AND billing_entitlements.valid_until IS NULL
"""


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE billing_entitlements SET hard_limit_microusd = 3000000, "
        "updated_at = now() " + _WHERE
        + " AND billing_entitlements.hard_limit_microusd = 1000000"
    ))


def downgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE billing_entitlements SET hard_limit_microusd = 1000000, "
        "updated_at = now() " + _WHERE
        + " AND billing_entitlements.hard_limit_microusd = 3000000"
    ))
