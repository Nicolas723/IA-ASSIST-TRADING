"""
Base Abstract Class for Technical Indicators.
All built-in and plugin indicators must inherit from BaseIndicator.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.indicators.entities import IndicatorMetadata, IndicatorResult
    from src.domain.market.entities import Candle


class BaseIndicator(abc.ABC):
    """
    Abstract Base Class for all technical indicators.
    """

    metadata: IndicatorMetadata

    @abc.abstractmethod
    def calculate(self, candles: list[Candle]) -> IndicatorResult:
        """
        Calculate the indicator on a series of historical candles (ordered chronologically).
        """
        ...
