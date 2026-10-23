import React, { useState, useMemo } from 'react';

export interface CandleItem {
  timestamp: string;
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_closed: boolean;
}

interface MarketChartProps {
  candles: CandleItem[];
  livePrice?: number | null;
  symbol: string;
  timeframe: string;
}

export const MarketChart: React.FC<MarketChartProps> = ({
  candles,
  livePrice,
  symbol,
  timeframe,
}) => {
  const [chartMode, setChartMode] = useState<'candles' | 'line'>('candles');
  const [hoveredCandle, setHoveredCandle] = useState<CandleItem | null>(null);

  const displayCandles = useMemo(() => {
    if (!candles || candles.length === 0) return [];
    const list = [...candles.slice(-40)];
    if (livePrice && list.length > 0) {
      const last = { ...list[list.length - 1] };
      last.close = livePrice;
      last.high = Math.max(last.high, livePrice);
      last.low = Math.min(last.low, livePrice);
      list[list.length - 1] = last;
    }
    return list;
  }, [candles, livePrice]);

  if (displayCandles.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: '#94a3b8' }}>
        <p style={{ fontSize: '15px' }}>📊 Sin datos de mercado para graficar.</p>
        <p style={{ fontSize: '13px', marginTop: '6px' }}>
          Haz clic en <strong>"Actualizar Historial"</strong> para cargar las velas en vivo.
        </p>
      </div>
    );
  }

  const width = 850;
  const height = 320;
  const padding = { top: 20, right: 65, bottom: 35, left: 15 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const minPrice = Math.min(...displayCandles.map((c) => c.low));
  const maxPrice = Math.max(...displayCandles.map((c) => c.high));
  const priceRange = maxPrice - minPrice || 1;

  const getY = (val: number) =>
    padding.top + chartHeight - ((val - minPrice) / priceRange) * chartHeight;

  const candleWidth = Math.max(chartWidth / displayCandles.length - 4, 4);

  // Price grid lines
  const gridTicks = 5;
  const priceGrid = Array.from({ length: gridTicks }, (_, i) => {
    const price = minPrice + (priceRange * i) / (gridTicks - 1);
    return { price, y: getY(price) };
  });

  // Current active candle for stats
  const activeCandle = hoveredCandle || displayCandles[displayCandles.length - 1];
  const isUp = activeCandle.close >= activeCandle.open;
  const pctChange =
    activeCandle.open > 0
      ? (((activeCandle.close - activeCandle.open) / activeCandle.open) * 100).toFixed(2)
      : '0.00';

  // Path for smooth line mode
  const linePoints = displayCandles.map((c, i) => {
    const x = padding.left + i * (chartWidth / (displayCandles.length - 1 || 1));
    const y = getY(c.close);
    return { x, y };
  });
  const linePath = linePoints.reduce(
    (acc, pt, i) => (i === 0 ? `M ${pt.x} ${pt.y}` : `${acc} L ${pt.x} ${pt.y}`),
    ''
  );
  const areaPath = `${linePath} L ${padding.left + chartWidth} ${padding.top + chartHeight} L ${padding.left} ${padding.top + chartHeight} Z`;

  return (
    <div style={{ width: '100%' }}>
      {/* Header del gráfico */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '12px',
          marginBottom: '14px',
          paddingBottom: '12px',
          borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ fontSize: '18px', fontWeight: 800, color: '#f8fafc' }}>
            {symbol}{' '}
            <span style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 500 }}>
              ({timeframe})
            </span>
          </div>
          <div
            style={{
              padding: '4px 10px',
              borderRadius: '6px',
              fontSize: '12px',
              fontWeight: 700,
              backgroundColor: isUp ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
              color: isUp ? '#10b981' : '#ef4444',
            }}
          >
            ${activeCandle.close.toLocaleString(undefined, { minimumFractionDigits: 2 })} (
            {Number(pctChange) >= 0 ? '+' : ''}
            {pctChange}%)
          </div>
        </div>

        {/* Info detallada de la vela inspeccionada */}
        <div
          style={{
            display: 'flex',
            gap: '16px',
            fontSize: '12px',
            color: '#94a3b8',
            flexWrap: 'wrap',
          }}
        >
          <span>
            Apertura: <strong style={{ color: '#f8fafc' }}>${activeCandle.open.toFixed(2)}</strong>
          </span>
          <span>
            Máx: <strong style={{ color: '#10b981' }}>${activeCandle.high.toFixed(2)}</strong>
          </span>
          <span>
            Mín: <strong style={{ color: '#ef4444' }}>${activeCandle.low.toFixed(2)}</strong>
          </span>
          <span>
            Cierre: <strong style={{ color: '#38bdf8' }}>${activeCandle.close.toFixed(2)}</strong>
          </span>
          <span>
            Vol: <strong style={{ color: '#f8fafc' }}>{activeCandle.volume.toFixed(2)}</strong>
          </span>
        </div>

        {/* Selector de modo de gráfico */}
        <div style={{ display: 'flex', gap: '6px' }}>
          <button
            type="button"
            className={`btn ${chartMode === 'candles' ? 'btn-primary' : 'btn-secondary'}`}
            style={{ padding: '4px 10px', fontSize: '12px' }}
            onClick={() => setChartMode('candles')}
          >
            🕯️ Velas
          </button>
          <button
            type="button"
            className={`btn ${chartMode === 'line' ? 'btn-primary' : 'btn-secondary'}`}
            style={{ padding: '4px 10px', fontSize: '12px' }}
            onClick={() => setChartMode('line')}
          >
            📈 Línea
          </button>
        </div>
      </div>

      {/* SVG Canvas */}
      <div style={{ width: '100%', overflowX: 'auto', position: 'relative' }}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: '100%', height: 'auto', display: 'block', maxHeight: '350px' }}
        >
          <defs>
            <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#38bdf8" stopOpacity="0.0" />
            </linearGradient>
          </defs>

          {/* Líneas de Guía de Precios */}
          {priceGrid.map((item, idx) => (
            <g key={idx}>
              <line
                x1={padding.left}
                y1={item.y}
                x2={padding.left + chartWidth}
                y2={item.y}
                stroke="rgba(255, 255, 255, 0.06)"
                strokeDasharray="4 4"
              />
              <text
                x={padding.left + chartWidth + 8}
                y={item.y + 4}
                fill="#64748b"
                fontSize="10"
                fontFamily="monospace"
              >
                ${item.price.toLocaleString(undefined, { maximumFractionDigits: 1 })}
              </text>
            </g>
          ))}

          {/* Modo Línea de Área */}
          {chartMode === 'line' && (
            <>
              <path d={areaPath} fill="url(#areaGradient)" />
              <path d={linePath} fill="none" stroke="#38bdf8" strokeWidth="2.5" />
            </>
          )}

          {/* Modo Velas Japonesas */}
          {chartMode === 'candles' &&
            displayCandles.map((c, i) => {
              const xCenter =
                padding.left + i * (chartWidth / displayCandles.length) + candleWidth / 2 + 2;
              const yHigh = getY(c.high);
              const yLow = getY(c.low);
              const yOpen = getY(c.open);
              const yClose = getY(c.close);
              const candleColor = c.close >= c.open ? '#10b981' : '#ef4444';
              const bodyTop = Math.min(yOpen, yClose);
              const bodyHeight = Math.max(Math.abs(yClose - yOpen), 2);

              return (
                <g
                  key={c.timestamp || i}
                  onMouseEnter={() => setHoveredCandle(c)}
                  onMouseLeave={() => setHoveredCandle(null)}
                  style={{ cursor: 'pointer' }}
                >
                  {/* Mecha superior e inferior */}
                  <line
                    x1={xCenter}
                    y1={yHigh}
                    x2={xCenter}
                    y2={yLow}
                    stroke={candleColor}
                    strokeWidth="1.2"
                  />
                  {/* Cuerpo de la vela */}
                  <rect
                    x={xCenter - candleWidth / 2}
                    y={bodyTop}
                    width={candleWidth}
                    height={bodyHeight}
                    fill={candleColor}
                    rx="1.5"
                    opacity={hoveredCandle === c ? 1 : 0.9}
                  />
                </g>
              );
            })}

          {/* Línea horizontal del precio en vivo */}
          {livePrice && (
            <g>
              <line
                x1={padding.left}
                y1={getY(livePrice)}
                x2={padding.left + chartWidth}
                y2={getY(livePrice)}
                stroke="#38bdf8"
                strokeWidth="1.5"
                strokeDasharray="3 3"
              />
              <rect
                x={padding.left + chartWidth + 2}
                y={getY(livePrice) - 9}
                width="60"
                height="18"
                fill="#0284c7"
                rx="3"
              />
              <text
                x={padding.left + chartWidth + 6}
                y={getY(livePrice) + 4}
                fill="#ffffff"
                fontSize="9.5"
                fontWeight="700"
                fontFamily="monospace"
              >
                ${livePrice.toLocaleString(undefined, { maximumFractionDigits: 1 })}
              </text>
            </g>
          )}

          {/* Eje de Tiempo (Etiquetas) */}
          {displayCandles
            .filter((_, i) => i % Math.ceil(displayCandles.length / 5) === 0)
            .map((c, i) => {
              const originalIndex = displayCandles.indexOf(c);
              const xPos =
                padding.left +
                originalIndex * (chartWidth / displayCandles.length) +
                candleWidth / 2;
              return (
                <text
                  key={i}
                  x={xPos}
                  y={padding.top + chartHeight + 18}
                  fill="#64748b"
                  fontSize="10"
                  textAnchor="middle"
                  fontFamily="sans-serif"
                >
                  {new Date(c.timestamp).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </text>
              );
            })}
        </svg>
      </div>
    </div>
  );
};
