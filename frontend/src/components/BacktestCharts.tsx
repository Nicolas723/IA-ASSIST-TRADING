import React, { useState } from 'react';

export interface EquityPointData {
  timestamp: string;
  time: number;
  equity: number;
  drawdown_pct: number;
}

export interface TradeData {
  id: number;
  entry_time: string;
  exit_time: string;
  pnl: number;
  pnl_pct: number;
}

export interface BacktestChartsProps {
  equityCurve: EquityPointData[];
  initialCapital: number;
  trades: TradeData[];
}

export const EquityCurveChart: React.FC<{ equityCurve: EquityPointData[]; initialCapital: number }> = ({
  equityCurve,
  initialCapital,
}) => {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (!equityCurve || equityCurve.length === 0) {
    return null;
  }

  const equities = equityCurve.map((e) => e.equity);
  const minEquity = Math.min(initialCapital * 0.9, ...equities);
  const maxEquity = Math.max(initialCapital * 1.1, ...equities);
  const range = maxEquity - minEquity || 1;

  const width = 800;
  const height = 240;
  const padding = 35;

  const points = equityCurve.map((point, index) => {
    const x = padding + (index / (equityCurve.length - 1 || 1)) * (width - padding * 2);
    const y = height - padding - ((point.equity - minEquity) / range) * (height - padding * 2);
    return { x, y, point };
  });

  const pathD = points.reduce((acc, p, i) => (i === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`), '');

  const areaD = `${pathD} L ${points[points.length - 1].x} ${height - padding} L ${points[0].x} ${height - padding} Z`;

  const initialCapitalY = height - padding - ((initialCapital - minEquity) / range) * (height - padding * 2);
  const isNetGain = (equities[equities.length - 1] ?? initialCapital) >= initialCapital;
  const lineColor = isNetGain ? '#10b981' : '#ef4444';

  const hoveredPoint = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '16px', marginBottom: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h4 style={{ margin: 0, fontSize: '14px', fontWeight: 700, color: '#94a3b8' }}>
          📈 Portfolio Equity Curve & Cumulative Drawdown
        </h4>
        {hoveredPoint && (
          <div style={{ fontSize: '12px', color: '#f8fafc', background: '#1e293b', padding: '4px 8px', borderRadius: '4px' }}>
            <span>{new Date(hoveredPoint.point.timestamp).toLocaleDateString()}</span> |{' '}
            <span style={{ color: lineColor, fontWeight: 700 }}>${hoveredPoint.point.equity.toLocaleString()}</span> |{' '}
            <span style={{ color: '#ef4444' }}>Drawdown: -{hoveredPoint.point.drawdown_pct}%</span>
          </div>
        )}
      </div>

      <div style={{ width: '100%', overflowX: 'auto' }}>
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
          <defs>
            <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity={0.35} />
              <stop offset="100%" stopColor={lineColor} stopOpacity={0.0} />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          <line x1={padding} y1={padding} x2={width - padding} y2={padding} stroke="#334155" strokeDasharray="3 3" />
          <line x1={padding} y1={height / 2} x2={width - padding} y2={height / 2} stroke="#334155" strokeDasharray="3 3" />
          <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#334155" />

          {/* Initial Capital Line */}
          <line x1={padding} y1={initialCapitalY} x2={width - padding} y2={initialCapitalY} stroke="#64748b" strokeDasharray="4 4" strokeWidth="1.5" />
          <text x={width - padding + 4} y={initialCapitalY + 4} fill="#64748b" fontSize="10">Initial</text>

          {/* Fill Area */}
          <path d={areaD} fill="url(#equityGradient)" />

          {/* Equity Line */}
          <path d={pathD} fill="none" stroke={lineColor} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />

          {/* Hover Overlay Points */}
          {points.map((p, idx) => (
            <circle
              key={idx}
              cx={p.x}
              cy={p.y}
              r={hoverIndex === idx ? 5 : 2}
              fill={hoverIndex === idx ? '#ffffff' : lineColor}
              onMouseEnter={() => setHoverIndex(idx)}
              onMouseLeave={() => setHoverIndex(null)}
              style={{ cursor: 'pointer' }}
            />
          ))}
        </svg>
      </div>
    </div>
  );
};

export const MonthlyReturnsHeatmap: React.FC<{ trades: TradeData[]; initialCapital: number }> = ({ trades, initialCapital }) => {
  if (!trades || trades.length === 0) return null;

  // Group PnLs by Year and Month
  const monthlyData: { [year: string]: { [month: number]: number } } = {};

  trades.forEach((t) => {
    const d = new Date(t.exit_time || t.entry_time);
    if (isNaN(d.getTime())) return;
    const year = d.getFullYear().toString();
    const month = d.getMonth(); // 0-11

    if (!monthlyData[year]) monthlyData[year] = {};
    if (!monthlyData[year][month]) monthlyData[year][month] = 0;
    monthlyData[year][month] += t.pnl;
  });

  const years = Object.keys(monthlyData).sort();
  if (years.length === 0) return null;

  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  return (
    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '16px', marginBottom: '20px' }}>
      <h4 style={{ margin: '0 0 12px', fontSize: '14px', fontWeight: 700, color: '#94a3b8' }}>
        🗓️ Monthly Performance Heatmap & PnL Breakdown
      </h4>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'center' }}>
          <thead>
            <tr style={{ color: '#64748b', borderBottom: '1px solid #334155' }}>
              <th style={{ padding: '6px', textAlign: 'left' }}>Year</th>
              {months.map((m) => (
                <th key={m} style={{ padding: '6px' }}>{m}</th>
              ))}
              <th style={{ padding: '6px', fontWeight: 700 }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {years.map((year) => {
              let yearTotal = 0;
              return (
                <tr key={year} style={{ borderBottom: '1px solid #1e293b' }}>
                  <td style={{ padding: '8px', fontWeight: 700, color: '#f8fafc', textAlign: 'left' }}>{year}</td>
                  {months.map((_, mIdx) => {
                    const pnl = monthlyData[year][mIdx] || 0;
                    yearTotal += pnl;
                    const pnlPct = (pnl / initialCapital) * 100;
                    const bg = pnl > 0 ? 'rgba(16, 185, 129, 0.2)' : pnl < 0 ? 'rgba(239, 68, 68, 0.2)' : 'transparent';
                    const color = pnl > 0 ? '#10b981' : pnl < 0 ? '#ef4444' : '#64748b';

                    return (
                      <td key={mIdx} style={{ padding: '8px', background: bg, color, fontWeight: pnl !== 0 ? 600 : 400 }}>
                        {pnl !== 0 ? `${pnl > 0 ? '+' : ''}${pnlPct.toFixed(1)}%` : '-'}
                      </td>
                    );
                  })}
                  <td
                    style={{
                      padding: '8px',
                      fontWeight: 700,
                      color: yearTotal >= 0 ? '#10b981' : '#ef4444',
                    }}
                  >
                    {yearTotal >= 0 ? '+' : ''}${Math.round(yearTotal).toLocaleString()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
