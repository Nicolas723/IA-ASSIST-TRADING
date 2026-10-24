"""add versioned research memory

Revision ID: b3c4d5e6f7a8
Revises: f7a8b9c0d1e2
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("timeframe", sa.String(length=8), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by", sa.String(length=16), nullable=False, server_default="human"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("validation_status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_notes_kind_status", "research_notes", ["kind", "validation_status"]
    )
    op.create_index("ix_research_notes_created_at", "research_notes", ["created_at"])
    op.create_index("ix_research_notes_supersedes", "research_notes", ["supersedes_id"])


def downgrade() -> None:
    op.drop_index("ix_research_notes_supersedes", table_name="research_notes")
    op.drop_index("ix_research_notes_created_at", table_name="research_notes")
    op.drop_index("ix_research_notes_kind_status", table_name="research_notes")
    op.drop_table("research_notes")
