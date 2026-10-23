"""
Base Abstract Class for Trading Strategies.
All strategy plugins must subclass BaseStrategy.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

from src.domain.strategy.entities import (
    Signal as Signal,
)
from src.domain.strategy.entities import (
    SignalSide,
)
from src.domain.strategy.entities import (
    StrategyMetadata as StrategyMetadata,
)

if TYPE_CHECKING:
    from src.domain.market.entities import Candle, MarketContext


class BaseStrategy(abc.ABC):
    """
    Abstract Base Class for all quantitative and AI-driven trading strategies.
    """

    metadata: StrategyMetadata

    def __init__(self, parameters: dict[str, Any] | None = None) -> None:
        self.parameters = {**self.metadata.default_parameters, **(parameters or {})}

    @abc.abstractmethod
    async def generate_signal(self, context: MarketContext | Any) -> Signal:
        """
        Evaluate market context and emit a trading Signal (BUY, SELL, or HOLD).
        """
        ...

    def on_candle(self, candle: Candle, history: list[Candle]) -> Signal:
        """
        Synchronous evaluation used primarily for fast backtesting iteration.
        Default implementation wraps candle context into signal logic.
        """
        return Signal(
            symbol=candle.symbol,
            timeframe=candle.timeframe.value,
            side=SignalSide.HOLD,
            entry_price=float(candle.close),
            reason="Default HOLD",
        )
