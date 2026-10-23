"""
IndicatorService — Application Layer for Technical Analysis.
Computes technical indicators and synthesizes aggregate market bias.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.domain.indicators.builtin import (
    ATRIndicator,
    BollingerBandsIndicator,
    EMASuiteIndicator,
    MACDIndicator,
    RSIIndicator,
)
from src.domain.indicators.entities import IndicatorResult, IndicatorSignal
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from src.domain.indicators.base import BaseIndicator
    from src.domain.market.entities import Candle

logger = get_logger(__name__)


class TechnicalAnalysisSummary(BaseModel):
    symbol: str
    timeframe: str
    candles_analyzed: int
    overall_signal: IndicatorSignal
    bullish_score: float = Field(..., description="Calculated aggregate bias score (-100 to +100)")
    summary_text: str
    indicators: dict[str, IndicatorResult]


class IndicatorService:
    """
    Service responsible for calculating technical indicators on market candles.
    """

    def __init__(self, indicators: list[BaseIndicator] | None = None) -> None:
        self.indicators: list[BaseIndicator] = indicators or [
            RSIIndicator(),
            MACDIndicator(),
            EMASuiteIndicator(),
            BollingerBandsIndicator(),
            ATRIndicator(),
        ]

    def add_indicator(self, indicator: BaseIndicator) -> None:
        self.indicators.append(indicator)

    def analyze(
        self, symbol: str, timeframe: str, candles: list[Candle]
    ) -> TechnicalAnalysisSummary:
        """
        Run all registered indicators on the given candle dataset and generate a technical summary.
        """
        results: dict[str, IndicatorResult] = {}
        signal_weights: list[float] = []

        # Signal score weights:
        # STRONG_BUY: +2.0, BUY: +1.0, NEUTRAL: 0.0, SELL: -1.0, STRONG_SELL: -2.0
        weight_map = {
            IndicatorSignal.STRONG_BUY: 2.0,
            IndicatorSignal.BUY: 1.0,
            IndicatorSignal.NEUTRAL: 0.0,
            IndicatorSignal.SELL: -1.0,
            IndicatorSignal.STRONG_SELL: -2.0,
        }

        for ind in self.indicators:
            try:
                res = ind.calculate(candles)
                results[res.name] = res
                signal_weights.append(weight_map.get(res.signal, 0.0))
            except Exception as exc:
                logger.error(
                    "indicator_calculation_error", indicator=ind.metadata.name, error=str(exc)
                )

        # Calculate composite score (-100 to +100)
        if signal_weights:
            max_possible = len(signal_weights) * 2.0
            total_score = sum(signal_weights)
            bullish_score = (
                round((total_score / max_possible) * 100.0, 1) if max_possible > 0 else 0.0
            )
        else:
            bullish_score = 0.0

        if bullish_score >= 50.0:
            overall_signal = IndicatorSignal.STRONG_BUY
            summary_text = "Strong bullish confluence across multiple technical indicators."
        elif bullish_score >= 15.0:
            overall_signal = IndicatorSignal.BUY
            summary_text = "Moderate bullish bias with positive momentum."
        elif bullish_score <= -50.0:
            overall_signal = IndicatorSignal.STRONG_SELL
            summary_text = "Strong bearish confluence with severe downward pressure."
        elif bullish_score <= -15.0:
            overall_signal = IndicatorSignal.SELL
            summary_text = "Moderate bearish bias with negative momentum."
        else:
            overall_signal = IndicatorSignal.NEUTRAL
            summary_text = "Market in consolidation; technical indicators are mixed or neutral."

        return TechnicalAnalysisSummary(
            symbol=symbol,
            timeframe=timeframe,
            candles_analyzed=len(candles),
            overall_signal=overall_signal,
            bullish_score=bullish_score,
            summary_text=summary_text,
            indicators=results,
        )
