"""Add the default-off durable AQUB v2 delivery ledger.

Revision ID: 0100_aqub_v2_delivery_ledger
Revises: 0099_telegram_caption_backfill
"""

from alembic import op

revision = "0100_aqub_v2_delivery_ledger"
down_revision = "0099_telegram_caption_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aqub_v2_route_authority (
          installation_ref varchar(255) NOT NULL,
          conversation_ref varchar(255) NOT NULL,
          cube_ref varchar(255) NOT NULL,
          route_generation integer NOT NULL
            CONSTRAINT ck_aqub_v2_route_generation CHECK (route_generation > 0),
          state varchar(16) NOT NULL
            CONSTRAINT ck_aqub_v2_route_state CHECK (state IN ('active','hold')),
          authority_epoch bigint NOT NULL
            CONSTRAINT ck_aqub_v2_route_epoch CHECK (authority_epoch > 0),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(installation_ref,conversation_ref),
          CONSTRAINT ck_aqub_v2_route_installation_ref
            CHECK (installation_ref ~ '^installation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
          CONSTRAINT ck_aqub_v2_route_conversation_ref
            CHECK (conversation_ref ~ '^conversation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
          CONSTRAINT ck_aqub_v2_route_cube_ref
            CHECK (cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$')
        );

        CREATE TABLE aqub_v2_delivery_ledger (
          id uuid PRIMARY KEY,
          command_id uuid NOT NULL UNIQUE,
          installation_ref varchar(255) NOT NULL,
          conversation_ref varchar(255) NOT NULL,
          cube_ref varchar(255) NOT NULL,
          route_generation integer NOT NULL
            CONSTRAINT ck_aqub_v2_delivery_generation CHECK (route_generation > 0),
          idempotency_key varchar(255) NOT NULL,
          command_sha256 varchar(64) NOT NULL
            CONSTRAINT ck_aqub_v2_command_sha CHECK (char_length(command_sha256)=64),
          command_json jsonb NOT NULL,
          state varchar(32) NOT NULL
            CONSTRAINT ck_aqub_v2_delivery_state
            CHECK (state IN ('committed','sending','sent','failed','unknown','rejected')),
          ack_ref varchar(255) NOT NULL,
          send_started_at timestamptz,
          settled_at timestamptz,
          receipt_json jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_aqub_v2_delivery_key
            UNIQUE(installation_ref,idempotency_key),
          CONSTRAINT ck_aqub_v2_delivery_installation_ref
            CHECK (installation_ref ~ '^installation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
          CONSTRAINT ck_aqub_v2_delivery_conversation_ref
            CHECK (conversation_ref ~ '^conversation:[A-Za-z][A-Za-z0-9._~-]{7,127}$'),
          CONSTRAINT ck_aqub_v2_delivery_cube_ref
            CHECK (cube_ref ~ '^cube:[A-Za-z][A-Za-z0-9._~-]{7,127}$')
        )
        """
    )


def downgrade() -> None:
    op.drop_table("aqub_v2_delivery_ledger")
    op.drop_table("aqub_v2_route_authority")
