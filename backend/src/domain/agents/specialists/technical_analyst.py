"""
Technical Analyst AI Agent.
Analyzes mathematical indicators (RSI, MACD, EMAs, Bollinger Bands, ATR, SuperTrend) to form directional conviction.
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
from src.domain.indicators.builtin import (
    ATRIndicator,
    BollingerBandsIndicator,
    EMASuiteIndicator,
    MACDIndicator,
    RSIIndicator,
)
from src.infrastructure.llm.gateway import LLMGateway, LLMMessage, LLMRequest
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)


class TechnicalAnalystAgent(BaseAgent):
    """
    AI Agent specializing in technical indicator confluence.
    """

    def __init__(self, llm_gateway: LLMGateway | None = None) -> None:
        self.llm = llm_gateway
        self.rsi_ind = RSIIndicator()
        self.macd_ind = MACDIndicator()
        self.ema_ind = EMASuiteIndicator()
        self.bb_ind = BollingerBandsIndicator()
        self.atr_ind = ATRIndicator()

    @property
    def agent_type(self) -> AgentType:
        return AgentType.TECHNICAL_ANALYST

    async def analyze(self, task: AgentTask) -> AgentOutput:
        # 1. Compute quantitative indicators
        candles = task.market_context.candles.get("1h", [])
        if not candles:
            candles = next(iter(task.market_context.candles.values()), [])

        if len(candles) < 20:
            return await self.fallback_analyze(task)

        rsi_res = self.rsi_ind.calculate(candles)
        macd_res = self.macd_ind.calculate(candles)
        ema_res = self.ema_ind.calculate(candles)
        bb_res = self.bb_ind.calculate(candles)
        atr_res = self.atr_ind.calculate(candles)

        current_price = float(candles[-1].close)

        # 2. If LLM Gateway is available, prompt the LLM for deep reasoning
        if self.llm:
            prompt = f"""
You are a Senior Quantitative Technical Analyst for cryptocurrency markets.
Analyze the following market indicators for {task.symbol}:
- Current Price: ${current_price:,.2f}
- RSI (14): {rsi_res.values.get("rsi")} ({rsi_res.interpretation})
- MACD: Line={macd_res.values.get("macd")}, Signal={macd_res.values.get("signal")}, Hist={macd_res.values.get("histogram")} ({macd_res.interpretation})
- EMAs: EMA9={ema_res.values.get("ema_9")}, EMA21={ema_res.values.get("ema_21")}, EMA50={ema_res.values.get("ema_50")}, EMA200={ema_res.values.get("ema_200")} ({ema_res.interpretation})
- Bollinger Bands: Upper=${bb_res.values.get("upper")}, Middle=${bb_res.values.get("middle")}, Lower=${bb_res.values.get("lower")}, %B={bb_res.values.get("percent_b")}
- ATR (14): ${atr_res.values.get("atr")} ({atr_res.values.get("atr_percent")}%)

Output STRICT JSON with the following structure:
{{
  "signal": "strong_buy" | "buy" | "neutral" | "sell" | "strong_sell",
  "confidence": 0.0,
  "reasoning": "Detailed technical explanation (minimum 50 characters)",
  "key_factors": [
    {{"name": "factor name", "impact": "bullish" | "bearish" | "neutral", "weight": 0.0-1.0, "description": "detail"}}
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
                            content="You are a professional technical analyst. Output ONLY valid JSON.",
                        ),
                        LLMMessage(role="user", content=prompt),
                    ],
                    model="",
                    temperature=0.1,
                    response_format="json",
                    metadata={"agent_type": self.agent_type.value},
                )
                llm_res = await self.llm.complete(llm_req)

                # Parse JSON
                cleaned = llm_res.content.replace("```json", "").replace("```", "").strip()
                data = json.loads(cleaned)

                factors = [
                    KeyFactor(
                        name=f.get("name", "Indicator"),
                        impact=f.get("impact", "neutral"),
                        weight=float(f.get("weight", 0.5)),
                        description=f.get("description", ""),
                    )
                    for f in data.get("key_factors", [])
                ]

                price_targets = PriceTarget(
                    entry=Decimal(str(data.get("entry", current_price))),
                    stop_loss=Decimal(str(data.get("stop_loss", current_price * 0.97))),
                    take_profit_1=Decimal(str(data.get("take_profit", current_price * 1.06))),
                )

                return AgentOutput(
                    task_id=task.task_id,
                    agent_type=self.agent_type,
                    symbol=task.symbol,
                    signal=SignalDirection(data.get("signal", "neutral")),
                    confidence=float(data.get("confidence", 0.7)),
                    reasoning=data.get(
                        "reasoning",
                        "Technical analysis concluded based on momentum and trend alignment.",
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
                logger.warning("technical_analyst_llm_failed_using_fallback", error=str(exc))

        # Fallback to rule-based analysis
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
                reasoning="No candle data available to perform technical analysis.",
                status=AgentStatus.DONE,
                is_fallback=True,
            )

        rsi_res = self.rsi_ind.calculate(candles)
        macd_res = self.macd_ind.calculate(candles)
        ema_res = self.ema_ind.calculate(candles)
        atr_res = self.atr_ind.calculate(candles)

        current_price = float(candles[-1].close)
        atr_val = atr_res.values.get("atr") or (current_price * 0.02)

        bullish_score = 0
        factors: list[KeyFactor] = []

        if rsi_res.signal in ("buy", "strong_buy"):
            bullish_score += 1
            factors.append(
                KeyFactor(
                    name="RSI Momentum",
                    impact="bullish",
                    weight=0.8,
                    description=rsi_res.interpretation,
                )
            )
        elif rsi_res.signal in ("sell", "strong_sell"):
            bullish_score -= 1
            factors.append(
                KeyFactor(
                    name="RSI Momentum",
                    impact="bearish",
                    weight=0.8,
                    description=rsi_res.interpretation,
                )
            )

        if macd_res.signal in ("buy", "strong_buy"):
            bullish_score += 1
            factors.append(
                KeyFactor(
                    name="MACD Trend",
                    impact="bullish",
                    weight=0.9,
                    description=macd_res.interpretation,
                )
            )
        elif macd_res.signal in ("sell", "strong_sell"):
            bullish_score -= 1
            factors.append(
                KeyFactor(
                    name="MACD Trend",
                    impact="bearish",
                    weight=0.9,
                    description=macd_res.interpretation,
                )
            )

        if ema_res.signal in ("buy", "strong_buy"):
            bullish_score += 1
            factors.append(
                KeyFactor(
                    name="EMA Alignment",
                    impact="bullish",
                    weight=0.9,
                    description=ema_res.interpretation,
                )
            )
        elif ema_res.signal in ("sell", "strong_sell"):
            bullish_score -= 1
            factors.append(
                KeyFactor(
                    name="EMA Alignment",
                    impact="bearish",
                    weight=0.9,
                    description=ema_res.interpretation,
                )
            )

        if bullish_score >= 2:
            signal = SignalDirection.BUY
            confidence = 0.82
            reasoning = f"Deterministic Technical Model: Confluence of bullish signals across {len(factors)} indicators ({', '.join(f.name for f in factors)})."
            targets = PriceTarget(
                entry=Decimal(str(current_price)),
                stop_loss=Decimal(str(round(current_price - (atr_val * 2.0), 2))),
                take_profit_1=Decimal(str(round(current_price + (atr_val * 4.0), 2))),
            )
        elif bullish_score <= -2:
            signal = SignalDirection.SELL
            confidence = 0.82
            reasoning = f"Deterministic Technical Model: Confluence of bearish signals across {len(factors)} indicators ({', '.join(f.name for f in factors)})."
            targets = PriceTarget(
                entry=Decimal(str(current_price)),
                stop_loss=Decimal(str(round(current_price + (atr_val * 2.0), 2))),
                take_profit_1=Decimal(str(round(current_price - (atr_val * 4.0), 2))),
            )
        else:
            signal = SignalDirection.NEUTRAL
            confidence = 0.50
            reasoning = "Deterministic Technical Model: Mixed signals across momentum and moving averages; market is in a consolidation phase."
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
