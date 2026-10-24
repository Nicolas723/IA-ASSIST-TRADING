"""Market Data API Router — v1"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.application.services.indicator_service import IndicatorService, TechnicalAnalysisSummary
from src.application.services.market_data_service import MarketDataService
from src.application.services.regime_service import MarketRegimeReport, RegimeService
from src.domain.market.entities import Timeframe
from src.interfaces.api.v1.deps import (
    get_indicator_service,
    get_market_data_service,
    get_regime_service,
)
from src.shared.config.settings import get_settings

router = APIRouter()


class CandleResponse(BaseModel):
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp")
    time: int = Field(..., description="Unix timestamp in seconds for charts")
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


class MarketCandlesResponse(BaseModel):
    symbol: str
    timeframe: str
    count: int
    data: list[CandleResponse]


class SyncResponse(BaseModel):
    status: str
    symbol: str
    timeframe: str
    candles_synced: int


class MarketCoverageEntry(BaseModel):
    asset_class: str
    status: str
    exchanges: list[str]
    execution_available: bool
    notes: str


class MarketCoverageResponse(BaseModel):
    generated_at: str
    configured_symbols: list[str]
    shadow_symbols: list[str]
    active_market_data_exchange: str
    entries: list[MarketCoverageEntry]


@router.get(
    "/coverage",
    response_model=MarketCoverageResponse,
    summary="Report which markets are actually implemented",
)
async def market_coverage() -> MarketCoverageResponse:
    """Describe real capability instead of implying multimarket support.

    ``execution_available`` means the code path exists, not that trading is
    enabled: real trading stays behind its own independent safety flags.
    """
    settings = get_settings()
    return MarketCoverageResponse(
        generated_at=datetime.now(tz=UTC).isoformat(),
        configured_symbols=list(settings.default_symbols),
        shadow_symbols=list(settings.shadow_scan_symbols),
        active_market_data_exchange=settings.market_data_exchange,
        entries=[
            MarketCoverageEntry(
                asset_class="crypto_spot",
                status="supported",
                exchanges=["binance"],
                execution_available=True,
                notes=(
                    "Market data and order execution exist for Binance spot. Execution is "
                    "disabled unless the real-trading flags and credentials are explicitly set."
                ),
            ),
            MarketCoverageEntry(
                asset_class="crypto_spot_bybit_data",
                status="market_data_supported",
                exchanges=["bybit"],
                execution_available=False,
                notes=(
                    "Public OHLCV, ticker and order-book ingestion uses a dedicated CCXT "
                    "adapter; live connectivity has not been validated end-to-end here. "
                    "No Bybit order execution, position reconciliation or OCO path is implemented."
                ),
            ),
            MarketCoverageEntry(
                asset_class="crypto_spot_other_exchanges",
                status="adapter_base_only",
                exchanges=["kraken", "coinbase"],
                execution_available=False,
                notes=(
                    "A generic CCXT market-data base exists, but no dedicated adapter or "
                    "end-to-end test has been validated for these exchanges."
                ),
            ),
            MarketCoverageEntry(
                asset_class="crypto_futures",
                status="not_implemented",
                exchanges=[],
                execution_available=False,
                notes="No futures/perpetual market data, funding, or margin logic is implemented.",
            ),
            MarketCoverageEntry(
                asset_class="forex",
                status="not_implemented",
                exchanges=[],
                execution_available=False,
                notes="No FX data provider, session calendar, or broker adapter is implemented.",
            ),
            MarketCoverageEntry(
                asset_class="equities",
                status="not_implemented",
                exchanges=[],
                execution_available=False,
                notes="No equity data provider, corporate-action handling, or broker adapter exists.",
            ),
            MarketCoverageEntry(
                asset_class="indices",
                status="not_implemented",
                exchanges=[],
                execution_available=False,
                notes="No index data source or contract specification is implemented.",
            ),
            MarketCoverageEntry(
                asset_class="commodities",
                status="not_implemented",
                exchanges=[],
                execution_available=False,
                notes="No commodity data source, roll logic, or contract specification is implemented.",
            ),
        ],
    )


@router.get("/symbols", summary="List available trading symbols")
async def list_symbols() -> dict[str, list[str]]:
    """Return list of configured trading symbols."""
    settings = get_settings()
    return {"symbols": settings.default_symbols}


@router.get(
    "/{symbol:path}/candles",
    response_model=MarketCandlesResponse,
    summary="Get OHLCV candlestick data",
)
async def get_candles(
    symbol: str,
    timeframe: str = Query("1h", description="Candlestick timeframe e.g. 1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(200, ge=1, le=1000, description="Max number of candles to return"),
    service: MarketDataService = Depends(get_market_data_service),
) -> MarketCandlesResponse:
    """Retrieve historical candlestick data for a symbol (from DB cache or fetched directly from Exchange)."""
    normalized_symbol = symbol.replace("-", "/").replace("_", "/").upper()

    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        valid_tfs = [t.value for t in Timeframe]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}'. Valid options: {valid_tfs}",
        ) from exc

    try:
        candles = await service.get_candles(
            symbol=normalized_symbol,
            timeframe=tf,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch market data: {exc}") from exc

    return MarketCandlesResponse(
        symbol=normalized_symbol,
        timeframe=tf.value,
        count=len(candles),
        data=[
            CandleResponse(
                timestamp=c.timestamp.isoformat(),
                time=int(c.timestamp.timestamp()),
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=float(c.volume),
                is_closed=c.is_closed,
            )
            for c in candles
        ],
    )


@router.post(
    "/{symbol:path}/sync",
    response_model=SyncResponse,
    summary="Sync historical candles into database",
)
async def sync_candles(
    symbol: str,
    timeframe: str = Query("1h", description="Candlestick timeframe e.g. 1h, 1d"),
    days_back: int = Query(14, ge=1, le=365, description="Days of history to backfill"),
    service: MarketDataService = Depends(get_market_data_service),
) -> SyncResponse:
    """Trigger a historical sync from the exchange into TimescaleDB."""
    normalized_symbol = symbol.replace("-", "/").replace("_", "/").upper()

    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe '{timeframe}'") from exc

    count = await service.sync_symbol(
        symbol=normalized_symbol,
        timeframe=tf,
        days_back=days_back,
    )
    return SyncResponse(
        status="synced",
        symbol=normalized_symbol,
        timeframe=tf.value,
        candles_synced=count,
    )


@router.get(
    "/{symbol:path}/indicators",
    response_model=TechnicalAnalysisSummary,
    summary="Get calculated technical indicators",
)
async def get_indicators(
    symbol: str,
    timeframe: str = Query("1h", description="Candlestick timeframe e.g. 1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(
        100, ge=30, le=500, description="Candle lookback window for indicator computation"
    ),
    market_service: MarketDataService = Depends(get_market_data_service),
    indicator_service: IndicatorService = Depends(get_indicator_service),
) -> TechnicalAnalysisSummary:
    """
    Calculate and return complete technical indicator suite (RSI, MACD, EMAs, Bollinger Bands, ATR, SuperTrend)
    along with aggregate bias scoring and actionable interpretations.
    """
    normalized_symbol = symbol.replace("-", "/").replace("_", "/").upper()
    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe '{timeframe}'") from exc

    candles = await market_service.get_candles(
        symbol=normalized_symbol,
        timeframe=tf,
        limit=limit,
    )

    if not candles:
        raise HTTPException(
            status_code=404,
            detail=f"No candle data available for {normalized_symbol}. Please sync market history first.",
        )

    analysis = indicator_service.analyze(
        symbol=normalized_symbol,
        timeframe=tf.value,
        candles=candles,
    )
    return analysis


@router.get(
    "/{symbol:path}/regime",
    response_model=MarketRegimeReport,
    summary="Classify the current market regime (read-only)",
)
async def get_market_regime(
    symbol: str,
    timeframe: str = Query("1h", description="Candlestick timeframe e.g. 1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(
        200, ge=30, le=1000, description="Closed-candle lookback used for classification"
    ),
    service: RegimeService = Depends(get_regime_service),
) -> MarketRegimeReport:
    """Describe trend and volatility from closed candles only.

    This endpoint is intentionally descriptive: it does not alter signals,
    sizing, risk vetoes or execution. Short or stale series are reported as
    such instead of forcing a label.
    """
    normalized_symbol = symbol.replace("-", "/").replace("_", "/").upper()

    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe '{timeframe}'") from exc

    try:
        return await service.analyze(
            symbol=normalized_symbol,
            timeframe=tf,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to classify regime: {exc}") from exc
