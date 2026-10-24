"""decision attribution for trading outcomes

Revision ID: d5e2b3c4f6a7
Revises: c4f1a2b3d5e6
Create Date: 2026-10-23 00:00:00.000000

Links executed trades and open positions to the AI committee decision that
produced them. The orders table already had ``coordinator_decision_id``; this
migration completes the chain so a closed trade can be attributed without
guessing from symbol/time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e2b3c4f6a7"
down_revision: str | None = "c4f1a2b3d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("coordinator_decision_id", sa.Uuid(), nullable=True))
    op.add_column("positions", sa.Column("coordinator_decision_id", sa.Uuid(), nullable=True))

    op.create_index(
        "ix_orders_coordinator_decision",
        "orders",
        ["coordinator_decision_id"],
    )
    op.create_index(
        "ix_trades_coordinator_decision",
        "trades",
        ["coordinator_decision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_trades_coordinator_decision", table_name="trades")
    op.drop_index("ix_orders_coordinator_decision", table_name="orders")

    op.drop_column("positions", "coordinator_decision_id")
    op.drop_column("trades", "coordinator_decision_id")
