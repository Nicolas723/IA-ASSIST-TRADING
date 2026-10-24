"""persist read-only shadow scan snapshots

Revision ID: e6f7091a2b3c
Revises: d5e2b3c4f6a7
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7091a2b3c"
down_revision: str | None = "d5e2b3c4f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_scan_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("lookback_candles", sa.Integer(), nullable=False),
        sa.Column("requested_symbols", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shadow_scan_runs_scanned_at",
        "shadow_scan_runs",
        ["scanned_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_scan_runs_scanned_at", table_name="shadow_scan_runs")
    op.drop_table("shadow_scan_runs")
