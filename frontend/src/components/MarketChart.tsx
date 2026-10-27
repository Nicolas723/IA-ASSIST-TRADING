import { useMemo, useState, type PointerEvent } from 'react';
import { fmtNumber, fmtPrice, fmtSigned, toneClass } from '../lib/format';
import type { CandleItem } from '../types';

export type { CandleItem };

interface MarketChartProps {
  candles: CandleItem[];
  livePrice?: number | null;
  mode?: 'candles' | 'line';
}

const WIDTH = 860;
const HEIGHT = 340;
const PAD = { top: 16, right: 72, bottom: 28, left: 8 };
const VISIBLE = 48;

/**
 * Monochrome price chart. Rising candles are hollow and falling candles are
 * filled, so direction reads without relying on colour.
 */
export function MarketChart({ candles, livePrice, mode = 'candles' }: MarketChartProps) {
  const [hover, setHover] = useState<number | null>(null);

  const data = useMemo(() => {
    const list = candles.slice(-VISIBLE);
    if (livePrice && list.length > 0) {
      const last = list[list.length - 1];
      list[list.length - 1] = {
        ...last,
        close: livePrice,
        high: Math.max(last.high, livePrice),
        low: Math.min(last.low, livePrice),
      };
    }
    return list;
  }, [candles, livePrice]);

  if (data.length === 0) {
    return <div className="empty">Sin datos de mercado todavía. Se cargarán en cuanto el exchange responda.</div>;
  }

  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const low = Math.min(...data.map((c) => c.low));
  const high = Math.max(...data.map((c) => c.high));
  const span = high - low || 1;
  const y = (value: number) => PAD.top + plotH - ((value - low) / span) * plotH;
  const step = plotW / data.length;
  const bodyW = Math.max(step * 0.62, 2);
  const x = (index: number) => PAD.left + index * step + step / 2;

  const grid = Array.from({ length: 5 }, (_, i) => low + (span * i) / 4);
  const linePath = data.map((c, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(c.close)}`).join(' ');
  const areaPath = `${linePath} L ${x(data.length - 1)} ${PAD.top + plotH} L ${x(0)} ${PAD.top + plotH} Z`;

  const active = data[hover ?? data.length - 1];
  const change = active.open > 0 ? ((active.close - active.open) / active.open) * 100 : 0;
  const labelEvery = Math.ceil(data.length / 6);

  const onPointer = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const relative = ((event.clientX - rect.left) / rect.width) * WIDTH - PAD.left;
    const index = Math.floor(relative / step);
    setHover(index >= 0 && index < data.length ? index : null);
  };

  return (
    <div>
      <svg
        className="chart-svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Gráfico de precio, último ${fmtPrice(data[data.length - 1].close)}`}
        onPointerMove={onPointer}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="chart-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--text)" stopOpacity="0.12" />
            <stop offset="100%" stopColor="var(--text)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {grid.map((value) => (
          <g key={value}>
            <line
              x1={PAD.left}
              x2={PAD.left + plotW}
              y1={y(value)}
              y2={y(value)}
              stroke="var(--border)"
              strokeWidth="1"
            />
            <text
              x={PAD.left + plotW + 10}
              y={y(value) + 4}
              fill="var(--muted)"
              fontSize="11"
              fontFamily="var(--font-mono)"
            >
              {fmtNumber(value, value >= 1000 ? 0 : 2)}
            </text>
          </g>
        ))}

        {hover !== null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD.top}
            y2={PAD.top + plotH}
            stroke="var(--border-strong)"
            strokeDasharray="3 3"
          />
        )}

        {mode === 'line' ? (
          <>
            <path d={areaPath} fill="url(#chart-area)" />
            <path d={linePath} fill="none" stroke="var(--text)" strokeWidth="1.75" strokeLinejoin="round" />
          </>
        ) : (
          data.map((c, i) => {
            const up = c.close >= c.open;
            const top = y(Math.max(c.open, c.close));
            const bodyH = Math.max(Math.abs(y(c.open) - y(c.close)), 1.5);
            const dim = hover !== null && hover !== i;
            return (
              <g key={c.timestamp || i} opacity={dim ? 0.45 : 1}>
                <line x1={x(i)} x2={x(i)} y1={y(c.high)} y2={y(c.low)} stroke="var(--text)" strokeWidth="1" />
                <rect
                  x={x(i) - bodyW / 2}
                  y={top}
                  width={bodyW}
                  height={bodyH}
                  rx="1"
                  fill={up ? 'var(--surface)' : 'var(--text)'}
                  stroke="var(--text)"
                  strokeWidth="1"
                />
              </g>
            );
          })
        )}

        {livePrice ? (
          <g>
            <line
              x1={PAD.left}
              x2={PAD.left + plotW}
              y1={y(livePrice)}
              y2={y(livePrice)}
              stroke="var(--text)"
              strokeWidth="1"
              strokeDasharray="2 4"
            />
            <rect x={PAD.left + plotW + 4} y={y(livePrice) - 10} width={66} height={20} rx="4" fill="var(--accent)" />
            <text
              x={PAD.left + plotW + 10}
              y={y(livePrice) + 4}
              fill="var(--accent-contrast)"
              fontSize="11"
              fontWeight="600"
              fontFamily="var(--font-mono)"
            >
              {fmtNumber(livePrice, livePrice >= 1000 ? 0 : 2)}
            </text>
          </g>
        ) : null}

        {data.map((c, i) =>
          i % labelEvery === 0 ? (
            <text
              key={`t-${c.timestamp || i}`}
              x={x(i)}
              y={HEIGHT - 8}
              fill="var(--muted)"
              fontSize="11"
              textAnchor="middle"
            >
              {new Date(c.timestamp).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
            </text>
          ) : null,
        )}
      </svg>

      <div className="chart-meta" aria-live="polite">
        <span>
          {hover === null ? 'Vela actual' : new Date(active.timestamp).toLocaleString('es-ES')}
        </span>
        <span>
          Apertura <strong>{fmtPrice(active.open)}</strong>
        </span>
        <span>
          Máx <strong>{fmtPrice(active.high)}</strong>
        </span>
        <span>
          Mín <strong>{fmtPrice(active.low)}</strong>
        </span>
        <span>
          Cierre <strong>{fmtPrice(active.close)}</strong>
        </span>
        <span className={toneClass(change)}>{fmtSigned(change, 2, ' %')}</span>
      </div>
    </div>
  );
}
