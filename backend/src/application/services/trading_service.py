"""
TradingService — Application Layer for Order Execution, Position Tracking, and Portfolio Management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.risk.entities import OrderRiskCheckRequest
from src.domain.trading.entities import ExecutionMode, Order, Portfolio, Position, Trade
from src.infrastructure.exchange.paper import PaperExchange, PlaceOrderRequest
from src.shared.exceptions.domain import RiskViolationError, ValidationError
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from uuid import UUID

    from src.application.services.market_data_service import MarketDataService
    from src.application.services.risk_service import RiskService

logger = get_logger(__name__)


class TradingService:
    """
    Central Trading Service coordinating risk-validated order execution across Paper and Live environments.
    """

    def __init__(
        self,
        paper_exchange: PaperExchange | None = None,
        risk_service: RiskService | None = None,
        market_service: MarketDataService | None = None,
    ) -> None:
        self.paper_exchange = paper_exchange or PaperExchange()
        self.risk_service = risk_service
        self.market_service = market_service

    def place_order(
        self,
        req: PlaceOrderRequest,
        current_market_price: float,
        mode: ExecutionMode = ExecutionMode.PAPER,
    ) -> Order:
        """
        Execute order with mandatory pre-flight risk check.
        """
        portfolio = self.get_portfolio(mode=mode)

        # 1. Pre-Flight Risk Validation (Phase 4 integration)
        if self.risk_service:
            risk_check_req = OrderRiskCheckRequest(
                symbol=req.symbol,
                side=req.side.value,
                order_type=req.order_type.value,
                price=current_market_price,
                quantity=req.quantity,
                stop_loss=req.stop_loss,
                take_profit=req.take_profit,
                account_balance=float(portfolio.total_balance),
                current_open_positions_count=len(portfolio.positions),
                current_daily_loss_pct=self.risk_service.current_daily_loss_pct,
                current_drawdown_pct=float(portfolio.current_drawdown_pct),
            )
            risk_res = self.risk_service.validate_order(risk_check_req)

            if not risk_res.is_allowed:
                msg = "; ".join(risk_res.violation_reasons)
                logger.warning(
                    "order_rejected_by_risk_guard",
                    symbol=req.symbol,
                    reasons=risk_res.violation_reasons,
                )
                raise RiskViolationError(f"Order rejected by Risk Guard: {msg}")

        # 2. Execution Routing
        if mode == ExecutionMode.PAPER:
            order = self.paper_exchange.place_order(req, current_market_price)
            # Update risk service positions count
            if self.risk_service:
                self.risk_service.open_positions_count = len(self.paper_exchange.positions)
            return order
        else:
            raise ValidationError("Real exchange execution is disabled (Paper mode is active)")

    def get_orders(self, mode: ExecutionMode = ExecutionMode.PAPER) -> list[Order]:
        if mode == ExecutionMode.PAPER:
            return list(self.paper_exchange.orders.values())
        return []

    def get_trades(self, mode: ExecutionMode = ExecutionMode.PAPER) -> list[Trade]:
        if mode == ExecutionMode.PAPER:
            return list(self.paper_exchange.trades)
        return []

    def get_positions(self, mode: ExecutionMode = ExecutionMode.PAPER) -> list[Position]:
        if mode == ExecutionMode.PAPER:
            return list(self.paper_exchange.positions.values())
        return []

    def update_market_prices(
        self,
        price_map: dict[str, float],
        mode: ExecutionMode = ExecutionMode.PAPER,
    ) -> None:
        """Refresh unrealized PnL of open positions with the latest tick prices."""
        if mode == ExecutionMode.PAPER:
            self.paper_exchange.update_market_prices(price_map)

    def close_position(
        self,
        position_id: UUID,
        current_market_price: float,
        mode: ExecutionMode = ExecutionMode.PAPER,
    ) -> Order | None:
        if mode == ExecutionMode.PAPER:
            order = self.paper_exchange.close_position(position_id, current_market_price)
            if self.risk_service:
                self.risk_service.open_positions_count = len(self.paper_exchange.positions)
            return order
        return None

    def get_portfolio(self, mode: ExecutionMode = ExecutionMode.PAPER) -> Portfolio:
        if mode == ExecutionMode.PAPER:
            return self.paper_exchange.get_portfolio()
        raise ValidationError(f"Mode {mode} not currently active")

    def reset_paper_account(self, initial_balance: float = 10000.0) -> Portfolio:
        self.paper_exchange.reset_account(initial_balance)
        if self.risk_service:
            self.risk_service.open_positions_count = 0
            self.risk_service.current_drawdown_pct = 0.0
            self.risk_service.current_daily_loss_pct = 0.0
        return self.get_portfolio(ExecutionMode.PAPER)
