"""trading journal and ai decision audit

Revision ID: c4f1a2b3d5e6
Revises: 93164c44d6de
Create Date: 2026-10-15 00:00:00.000000

Adds durable persistence for the trading domain (orders, trades, positions,
portfolio snapshots) and the AI decision audit trail (decision_logs).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f1a2b3d5e6"
down_revision: str | None = "93164c44d6de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- orders -------------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.DECIMAL(precision=30, scale=8), nullable=False),
        sa.Column("limit_price", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("stop_price", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("filled_quantity", sa.DECIMAL(precision=30, scale=8), nullable=False),
        sa.Column("average_fill_price", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("commission", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("commission_asset", sa.String(length=10), nullable=False),
        sa.Column("stop_loss", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("take_profit", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("coordinator_decision_id", sa.Uuid(), nullable=True),
        sa.Column("client_order_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_symbol_status", "orders", ["symbol", "status"])
    op.create_index("ix_orders_mode_created", "orders", ["mode", "created_at"])

    # -- trades -------------------------------------------------------------
    op.create_table(
        "trades",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.DECIMAL(precision=30, scale=8), nullable=False),
        sa.Column("price", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("commission", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("commission_asset", sa.String(length=10), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trades_symbol_executed", "trades", ["symbol", "executed_at"])
    op.create_index("ix_trades_order_id", "trades", ["order_id"])

    # -- positions ----------------------------------------------------------
    op.create_table(
        "positions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("entry_price", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("quantity", sa.DECIMAL(precision=30, scale=8), nullable=False),
        sa.Column("current_price", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("realized_pnl", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("total_commission", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("stop_loss", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("take_profit", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_symbol_mode", "positions", ["symbol", "mode"])

    # -- portfolio_snapshots ------------------------------------------------
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("exchange", sa.String(length=30), nullable=False),
        sa.Column("total_balance", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("available_balance", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("locked_balance", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("base_currency", sa.String(length=10), nullable=False),
        sa.Column("total_realized_pnl", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("daily_pnl", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("peak_value", sa.DECIMAL(precision=20, scale=8), nullable=False),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portfolio_snapshots_mode_updated",
        "portfolio_snapshots",
        ["mode", "updated_at"],
    )

    # -- decision_logs ------------------------------------------------------
    op.create_table(
        "decision_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("final_signal", sa.String(length=20), nullable=False),
        sa.Column("aggregate_confidence", sa.Float(), nullable=False),
        sa.Column("consensus_level", sa.Float(), nullable=False),
        sa.Column("risk_approved", sa.Boolean(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("recommended_entry", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("recommended_stop", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("recommended_target", sa.DECIMAL(precision=20, scale=8), nullable=True),
        sa.Column("reasoning_summary", sa.Text(), nullable=False),
        sa.Column("action_recommendation", sa.Text(), nullable=False),
        sa.Column("agent_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_duration_ms", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_logs_symbol_created", "decision_logs", ["symbol", "created_at"])
    op.create_index(
        "ix_decision_logs_signal_created",
        "decision_logs",
        ["final_signal", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_logs_signal_created", table_name="decision_logs")
    op.drop_index("ix_decision_logs_symbol_created", table_name="decision_logs")
    op.drop_table("decision_logs")

    op.drop_index("ix_portfolio_snapshots_mode_updated", table_name="portfolio_snapshots")
    op.drop_table("portfolio_snapshots")

    op.drop_index("ix_positions_symbol_mode", table_name="positions")
    op.drop_table("positions")

    op.drop_index("ix_trades_order_id", table_name="trades")
    op.drop_index("ix_trades_symbol_executed", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_orders_mode_created", table_name="orders")
    op.drop_index("ix_orders_symbol_status", table_name="orders")
    op.drop_table("orders")
