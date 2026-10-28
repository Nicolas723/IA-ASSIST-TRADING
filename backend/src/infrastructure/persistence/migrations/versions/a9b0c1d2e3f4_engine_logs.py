"""persist autonomous engine logs

Revision ID: a9b0c1d2e3f4
Revises: b3c4d5e6f7a8
Create Date: 2026-10-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entry_id", sa.String(length=36), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("signal", sa.String(length=20), nullable=True),
        sa.Column("confidence_pct", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("order_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_engine_logs_timestamp", "engine_logs", ["timestamp"])
    op.create_index("ix_engine_logs_key_timestamp", "engine_logs", ["key", "timestamp"])


def downgrade() -> None:
    op.drop_index("ix_engine_logs_key_timestamp", table_name="engine_logs")
    op.drop_index("ix_engine_logs_timestamp", table_name="engine_logs")
    op.drop_table("engine_logs")
