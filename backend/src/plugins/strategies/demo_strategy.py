# src/plugins/strategies/demo_strategy.py
"""Demo strategy plugin.

A minimal implementation of ``BaseStrategy`` that always returns a static
``Signal`` indicating a BUY decision. This is useful for testing the plugin
system without needing external data.
"""

from __future__ import annotations

from typing import Any

from src.domain.strategy.base import BaseStrategy, Signal, StrategyMetadata
from src.domain.strategy.entities import SignalSide


class DemoStrategy(BaseStrategy):
    """A trivial strategy that always signals BUY.

    The ``metadata`` attribute provides information used by the plugin
    registry and UI. ``BaseStrategy`` defines the abstract ``generate_signal``
    coroutine that we implement here.
    """

    metadata = StrategyMetadata(
        name="demo_strategy",
        version="0.1.0",
        author="AI Trading Assistant Team",
        description="Static BUY signal for demonstration purposes",
    )

    async def generate_signal(self, context: Any = None) -> Signal:
        # ``context`` would normally contain market data; we ignore it here.
        return Signal(
            side=SignalSide.BUY,
            confidence=1.0,
            reason="Demo strategy always buys",
        )
