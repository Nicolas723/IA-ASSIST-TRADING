"""
Price Action & Market Structure AI Specialist Agent.
Evaluates candlestick patterns, support/resistance levels, and market structure.
"""

from __future__ import annotations

import json
from decimal import Decimal

from src.domain.agents.base import BaseAgent
from src.domain.agents.entities import (
    AgentOutput,
    AgentStatus,
    AgentTask,
    AgentType,
    KeyFactor,
    PriceTarget,
    SignalDirection,
)
from src.infrastructure.llm.gateway import LLMGateway, LLMMessage, LLMRequest
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class PriceActionAnalystAgent(BaseAgent):
    """
    Agent specializing in candlestick anatomy, support/resistance levels, and swing market structure.
    """

    def __init__(self, llm_gateway: LLMGateway | None = None) -> None:
        self.llm = llm_gateway

    @property
    def agent_type(self) -> AgentType:
        return AgentType.PRICE_ACTION_ANALYST

    async def analyze(self, task: AgentTask) -> AgentOutput:
        candles = task.market_context.candles.get("1h", [])
        if not candles:
            candles = next(iter(task.market_context.candles.values()), [])

        if len(candles) < 10:
            return await self.fallback_analyze(task)

        current_candle = candles[-1]
        prev_candle = candles[-2]
        current_price = float(current_candle.close)

        highs = [float(c.high) for c in candles[-20:]]
        lows = [float(c.low) for c in candles[-20:]]
        swing_high = max(highs)
        swing_low = min(lows)

        # Candlestick anatomy analysis
        body_size = float(current_candle.body_size)
        upper_wick = float(current_candle.upper_wick)
        lower_wick = float(current_candle.lower_wick)

        # Prompt LLM if gateway is configured
        if self.llm:
            prompt = f"""
You are an expert Price Action & Smart Money Concepts (SMC) trader analyzing {task.symbol}.
Recent Price Action Data:
- Current Price: ${current_price:,.2f}
- Recent 20-candle Swing High (Resistance): ${swing_high:,.2f}
- Recent 20-candle Swing Low (Support): ${swing_low:,.2f}
- Latest Candle: Open=${float(current_candle.open):,.2f}, High=${float(current_candle.high):,.2f}, Low=${float(current_candle.low):,.2f}, Close=${float(current_candle.close):,.2f}
- Latest Body Size: ${body_size:.2f}, Upper Wick: ${upper_wick:.2f}, Lower Wick: ${lower_wick:.2f}
- Previous Candle: Close=${float(prev_candle.close):,.2f}

Evaluate the market structure (Higher Highs vs Lower Lows, Rejection wicks, Engulfing patterns, Liquidity Sweeps).
Output STRICT JSON:
{{
  "signal": "strong_buy" | "buy" | "neutral" | "sell" | "strong_sell",
  "confidence": 0.0,
  "reasoning": "Detailed price action breakdown (minimum 50 characters)",
  "key_factors": [
    {{"name": "pattern/level name", "impact": "bullish" | "bearish" | "neutral", "weight": 0.0-1.0, "description": "detail"}}
  ],
  "entry": float,
  "stop_loss": float,
  "take_profit": float
}}

The confidence field is only your heuristic signal-strength score, not a calibrated probability of success.
"""
            if task.research_context:
                prompt = f"{prompt}\n\n{task.research_context}"
            try:
                llm_req = LLMRequest(
                    messages=[
                        LLMMessage(
                            role="system",
                            content="You are a price action analyst. Output ONLY valid JSON.",
                        ),
                        LLMMessage(role="user", content=prompt),
                    ],
                    model="",
                    temperature=0.1,
                    response_format="json",
                    metadata={"agent_type": self.agent_type.value},
                )
                llm_res = await self.llm.complete(llm_req)
                cleaned = llm_res.content.replace("```json", "").replace("```", "").strip()
                data = json.loads(cleaned)

                factors = [
                    KeyFactor(
                        name=f.get("name", "Price Action"),
                        impact=f.get("impact", "neutral"),
                        weight=float(f.get("weight", 0.5)),
                        description=f.get("description", ""),
                    )
                    for f in data.get("key_factors", [])
                ]

                price_targets = PriceTarget(
                    entry=Decimal(str(data.get("entry", current_price))),
                    stop_loss=Decimal(str(data.get("stop_loss", swing_low * 0.99))),
                    take_profit_1=Decimal(str(data.get("take_profit", swing_high * 1.01))),
                )

                return AgentOutput(
                    task_id=task.task_id,
                    agent_type=self.agent_type,
                    symbol=task.symbol,
                    signal=SignalDirection(data.get("signal", "neutral")),
                    confidence=float(data.get("confidence", 0.75)),
                    reasoning=data.get(
                        "reasoning", "Price action indicates support/resistance interaction."
                    ),
                    key_factors=factors,
                    price_targets=price_targets,
                    llm_provider=llm_res.provider.value,
                    llm_model=llm_res.model,
                    prompt_sent=prompt,
                    llm_response=llm_res.content,
                    status=AgentStatus.DONE,
                )
            except Exception as exc:
                logger.warning("price_action_llm_failed_using_fallback", error=str(exc))

        return await self.fallback_analyze(task)

    async def fallback_analyze(self, task: AgentTask) -> AgentOutput:
        candles = task.market_context.candles.get("1h", [])
        if not candles:
            candles = next(iter(task.market_context.candles.values()), [])

        if not candles:
            return AgentOutput(
                task_id=task.task_id,
                agent_type=self.agent_type,
                symbol=task.symbol,
                signal=SignalDirection.NO_SIGNAL,
                confidence=0.0,
                reasoning="Insufficient candle history for price action analysis.",
                status=AgentStatus.DONE,
                is_fallback=True,
            )

        current_candle = candles[-1]
        prev_candle = candles[-2] if len(candles) > 1 else current_candle
        current_price = float(current_candle.close)

        highs = [float(c.high) for c in candles[-20:]]
        lows = [float(c.low) for c in candles[-20:]]
        swing_high = max(highs)
        swing_low = min(lows)

        factors: list[KeyFactor] = []
        body = float(current_candle.body_size)
        lower_wick = float(current_candle.lower_wick)
        upper_wick = float(current_candle.upper_wick)

        # Bullish Engulfing pattern or Hammer
        if (
            current_candle.is_bullish
            and float(current_candle.close) > float(prev_candle.open)
            and float(current_candle.open) < float(prev_candle.close)
        ):
            signal = SignalDirection.BUY
            confidence = 0.80
            factors.append(
                KeyFactor(
                    name="Bullish Engulfing Pattern",
                    impact="bullish",
                    weight=0.9,
                    description="Current candle completely engulfed previous candle's body.",
                )
            )
            reasoning = f"Price Action Model: Strong Bullish Engulfing candle detected near key swing zone (${swing_low:,.2f})."
            targets = PriceTarget(
                entry=Decimal(str(current_price)),
                stop_loss=Decimal(str(round(swing_low * 0.99, 2))),
                take_profit_1=Decimal(str(round(swing_high, 2))),
            )
        elif lower_wick > (body * 2.0) and lower_wick > upper_wick:
            # Hammer / Pin bar
            signal = SignalDirection.BUY
            confidence = 0.78
            factors.append(
                KeyFactor(
                    name="Bullish Pin Bar / Hammer",
                    impact="bullish",
                    weight=0.85,
                    description="Long lower wick indicates aggressive rejection of lower prices.",
                )
            )
            reasoning = f"Price Action Model: Hammer rejection pin bar at ${float(current_candle.low):,.2f} with strong buying wick."
            targets = PriceTarget(
                entry=Decimal(str(current_price)),
                stop_loss=Decimal(str(round(float(current_candle.low) * 0.995, 2))),
                take_profit_1=Decimal(str(round(swing_high, 2))),
            )
        elif (
            not current_candle.is_bullish
            and float(current_candle.close) < float(prev_candle.open)
            and float(current_candle.open) > float(prev_candle.close)
        ):
            # Bearish Engulfing
            signal = SignalDirection.SELL
            confidence = 0.80
            factors.append(
                KeyFactor(
                    name="Bearish Engulfing Pattern",
                    impact="bearish",
                    weight=0.9,
                    description="Current candle completely engulfed previous candle downwards.",
                )
            )
            reasoning = f"Price Action Model: Bearish Engulfing reversal pattern detected near swing resistance (${swing_high:,.2f})."
            targets = PriceTarget(
                entry=Decimal(str(current_price)),
                stop_loss=Decimal(str(round(swing_high * 1.01, 2))),
                take_profit_1=Decimal(str(round(swing_low, 2))),
            )
        else:
            signal = SignalDirection.NEUTRAL
            confidence = 0.50
            reasoning = f"Price Action Model: Price ranging within ${swing_low:,.2f} (Support) and ${swing_high:,.2f} (Resistance). No definitive breakout candle."
            targets = None

        return AgentOutput(
            task_id=task.task_id,
            agent_type=self.agent_type,
            symbol=task.symbol,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            key_factors=factors,
            price_targets=targets,
            status=AgentStatus.DONE,
            is_fallback=True,
        )
