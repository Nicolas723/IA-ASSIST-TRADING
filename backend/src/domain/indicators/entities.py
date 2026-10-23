"""
Domain models and value objects for Technical Indicators.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IndicatorCategory(StrEnum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"


class IndicatorSignal(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class IndicatorMetadata(BaseModel):
    name: str
    display_name: str
    category: IndicatorCategory
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class IndicatorResult(BaseModel):
    name: str
    category: IndicatorCategory
    values: dict[str, float | None] = Field(
        ...,
        description="Calculated values (e.g. {'rsi': 65.4} or {'upper': 70000, 'lower': 68000})",
    )
    signal: IndicatorSignal = IndicatorSignal.NEUTRAL
    interpretation: str = ""
