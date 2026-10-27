import { useState } from 'react';
import toast from 'react-hot-toast';
import type { Page } from '../App';
import { Card, Empty, Notice, PageHead, Pill, Segmented, Skeleton, Stat, SubNav } from '../components/ui';
import { usePolling } from '../hooks/usePolling';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { fmtInt, fmtNumber, fmtPct, fmtSigned, fmtUsd, toNumber, toneClass } from '../lib/format';
import {
  AGENT_LABELS,
  AVAILABLE_SYMBOLS,
  SIGNAL_LABELS,
  type AdaptiveWeightsReport,
  type ConcentrationReportData,
  type CoordinatorDecisionData,
  type DriftReportData,
  type LLMStatusData,
  type MarketRegimeData,
  type MlopsPerformanceData,
  type OpportunityAssessmentData,
  type ResearchContextData,
  type RiskStatusSummary,
  type ShadowScanData,
} from '../types';

type Section = 'comite' | 'aprendizaje' | 'riesgo' | 'mercado' | 'consumo' | 'sistema';

const SECTIONS: ReadonlyArray<{ id: Section; label: string }> = [
  { id: 'comite', label: 'Comité' },
  { id: 'aprendizaje', label: 'Aprendizaje' },
  { id: 'riesgo', label: 'Riesgo' },
  { id: 'mercado', label: 'Mercado' },
  { id: 'consumo', label: 'Consumo de IA' },
  { id: 'sistema', label: 'Sistema' },
];

interface Props {
  section: string | null;
  navigate: (page: Page, section?: string) => void;
  symbol: string;
  timeframe: string;
}

export function AdvancedView({ section, navigate, symbol, timeframe }: Props) {
  const active: Section = SECTIONS.some((item) => item.id === section) ? (section as Section) : 'comite';
  return (
    <>
      <PageHead
        title="Avanzado"
        description="Detalle técnico del comité, los resultados y los controles de riesgo. Nada de esta sección envía órdenes."
      />
      <SubNav label="Secciones avanzadas" items={SECTIONS} value={active} onChange={(id) => navigate('advanced', id)} />
      {active === 'comite' && <Committee symbol={symbol} timeframe={timeframe} />}
      {active === 'aprendizaje' && <Learning />}
      {active === 'riesgo' && <Risk />}
      {active === 'mercado' && <Market symbol={symbol} timeframe={timeframe} />}
      {active === 'consumo' && <AiUsage />}
      {active === 'sistema' && <SystemStatus />}
    </>
  );
}

/* ------------------------------------------------------------ Committee */

const SPECIALISTS = [
  {
    id: 'technical_analyst',
    description: 'Lee indicadores: RSI, MACD y medias móviles (9, 21, 50, 200) para medir tendencia e impulso.',
  },
  {
    id: 'price_action_analyst',
    description: 'Estudia soportes, resistencias y patrones de velas para evitar falsas rupturas.',
  },
  {
    id: 'risk_manager',
    description: 'Mide la volatilidad (ATR) y coloca stop y objetivo con una relación mínima de 2 a 1.',
  },
];

function Committee({ symbol, timeframe }: { symbol: string; timeframe: string }) {
  const [decision, setDecision] = useState<CoordinatorDecisionData | null>(null);
  const [busy, setBusy] = useState(false);
  const weights = usePolling<AdaptiveWeightsReport>('agent-weights', () =>
    apiGet<AdaptiveWeightsReport>('/api/v1/mlops/agent-weights'),
  );

  const analyze = async () => {
    setBusy(true);
    try {
      setDecision(
        await apiSend<CoordinatorDecisionData>('POST', '/api/v1/agents/analyze', {
          symbol,
          timeframe,
          lookback_candles: 50,
        }),
      );
    } catch (error) {
      toast.error(`No se pudo analizar: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const liveWeights = decision?.agent_weights ?? weights.data?.weights;

  return (
    <div className="stack-lg">
      <Card
        title="Consultar al comité"
        subtitle={`Tres especialistas analizan ${symbol} (${timeframe}) por separado y un coordinador combina sus votos. Consulta puntual: no abre operaciones.`}
        actions={
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void analyze()}>
            {busy ? 'Analizando…' : `Analizar ${symbol}`}
          </button>
        }
      >
        {decision ? (
          <div className="stack">
            <div className="row">
              <Pill tone="solid">{SIGNAL_LABELS[decision.final_signal] ?? decision.final_signal}</Pill>
              <Pill>Fuerza {Math.round(decision.aggregate_confidence * 100)} %</Pill>
              <Pill>Consenso {Math.round(decision.consensus_level * 100)} %</Pill>
              {decision.recommended_stop && <Pill>Stop {fmtUsd(decision.recommended_stop)}</Pill>}
              {decision.recommended_target && <Pill>Objetivo {fmtUsd(decision.recommended_target)}</Pill>}
            </div>
            <p className="small">{decision.action_recommendation}</p>
            <div className="stack" style={{ gap: 6 }}>
              {decision.reasoning_summary.split(' | ').map((line) => (
                <p key={line} className="muted small">
                  {line}
                </p>
              ))}
            </div>
          </div>
        ) : (
          <Empty>Pulsa «Analizar» para ver la opinión actual del comité.</Empty>
        )}
      </Card>

      <div className="cols">
        {SPECIALISTS.map((specialist) => {
          const weight = liveWeights?.[specialist.id];
          return (
            <Card
              key={specialist.id}
              title={AGENT_LABELS[specialist.id]}
              actions={weight !== undefined ? <Pill>Peso {Math.round(weight * 100)} %</Pill> : null}
            >
              <p className="muted small">{specialist.description}</p>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- Learning */

function Learning() {
  const [mode, setMode] = useState<'all' | 'paper' | 'real'>('all');
  const [symbol, setSymbol] = useState('');
  const params = new URLSearchParams({ limit: '200' });
  if (mode !== 'all') params.set('mode', mode);
  if (symbol) params.set('symbol', symbol);
  const query = params.toString();

  const performance = usePolling<MlopsPerformanceData>(
    `mlops|${query}`,
    () => apiGet<MlopsPerformanceData>(`/api/v1/mlops/performance?${query}`),
    30000,
  );
  const drift = usePolling<DriftReportData>('drift', () => apiGet<DriftReportData>('/api/v1/mlops/drift'));
  const weights = usePolling<AdaptiveWeightsReport>('agent-weights', () =>
    apiGet<AdaptiveWeightsReport>('/api/v1/mlops/agent-weights'),
  );
  const data = performance.data;

  return (
    <div className="stack-lg">
      <Card
        title="Resultados del comité"
        subtitle="Cada decisión se cruza con las órdenes reales que provocó. Solo cuentan operaciones con entrada y salida completas."
        actions={
          <>
            <Segmented
              label="Modo"
              value={mode}
              onChange={setMode}
              options={[
                { value: 'all', label: 'Todo' },
                { value: 'paper', label: 'Simulado' },
                { value: 'real', label: 'Real' },
              ]}
            />
            <select
              className="select"
              style={{ width: 'auto' }}
              aria-label="Mercado"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
            >
              <option value="">Todos los mercados</option>
              {AVAILABLE_SYMBOLS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </select>
          </>
        }
      >
        {!data ? (
          performance.error ? <Notice tone="error">{performance.error}</Notice> : <Skeleton height={90} />
        ) : !data.data_available ? (
          <Notice tone="warn">La base de datos no responde ahora mismo. Las métricas volverán cuando se recupere.</Notice>
        ) : (
          <div className="stats">
            <Stat label="Decisiones" value={fmtInt(data.total_decisions)} sub={`${fmtInt(data.executed)} ejecutadas`} />
            <Stat label="Cerradas" value={fmtInt(data.resolved)} />
            <Stat label="Acierto" value={fmtPct(data.win_rate_pct)} />
            <Stat label="Resultado neto" value={fmtSigned(data.net_pnl, 2, ' $')} tone={toneClass(data.net_pnl)} />
            <Stat label="Factor de beneficio" value={data.profit_factor === null ? '—' : fmtNumber(data.profit_factor)} />
            <Stat label="Órdenes sin atribuir" value={fmtInt(data.unattributed_orders)} />
          </div>
        )}
      </Card>

      <AdaptiveWeights report={weights.data} />

      {data?.data_available && data.by_agent.length > 0 && (
        <Card title="Acierto por analista" subtitle="Dirección del precio frente a la señal de cada analista. El gestor de riesgo no se puntúa.">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Analista</th>
                  <th>Acierto</th>
                  <th>Aciertos / evaluadas</th>
                  <th>Abstenciones</th>
                  <th>Latencia media</th>
                </tr>
              </thead>
              <tbody>
                {data.by_agent.map((agent) => (
                  <tr key={agent.agent_type}>
                    <td>{AGENT_LABELS[agent.agent_type] ?? agent.agent_type}</td>
                    <td>{fmtPct(agent.hit_rate_pct)}</td>
                    <td>
                      {agent.correct} / {agent.decisions_scored}
                    </td>
                    <td>{agent.abstained}</td>
                    <td>{fmtInt(agent.avg_processing_time_ms)} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {data?.data_available && (
        <div className="cols">
          <Card title="Por señal">
            {data.by_signal.length === 0 ? (
              <Empty>Sin datos todavía.</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Señal</th>
                      <th>Acierto</th>
                      <th>Neto</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_signal.map((item) => (
                      <tr key={item.signal}>
                        <td>{SIGNAL_LABELS[item.signal] ?? item.signal}</td>
                        <td>
                          {fmtPct(item.hit_rate_pct)} <span className="muted">({item.correct}/{item.resolved})</span>
                        </td>
                        <td className={toneClass(item.net_pnl)}>{fmtSigned(item.net_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Card title="Por mercado">
            {data.by_symbol.length === 0 ? (
              <Empty>Sin datos todavía.</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Mercado</th>
                      <th>Acierto</th>
                      <th>Neto</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_symbol.map((item) => (
                      <tr key={item.symbol}>
                        <td>{item.symbol}</td>
                        <td>
                          {fmtPct(item.win_rate_pct)} <span className="muted">({item.resolved})</span>
                        </td>
                        <td className={toneClass(item.net_pnl)}>{fmtSigned(item.net_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {data?.data_available && (
        <Card
          title="Acierto según la fuerza de la señal"
          subtitle="Descriptivo: con pocas muestras no indica una probabilidad real."
        >
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Fuerza</th>
                  <th>Cerradas</th>
                  <th>Acierto</th>
                  <th>Muestra</th>
                </tr>
              </thead>
              <tbody>
                {data.confidence_buckets.map((bucket) => (
                  <tr key={`${bucket.lower}-${bucket.upper}`}>
                    <td>
                      {Math.round(bucket.lower * 100)}–{Math.round(bucket.upper * 100)} %
                    </td>
                    <td>{bucket.decisions}</td>
                    <td>{fmtPct(bucket.hit_rate_pct)}</td>
                    <td className="muted">
                      {bucket.sample_sufficient ? 'Suficiente' : `Insuficiente (${bucket.decisions}/${bucket.minimum_sample_size})`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {drift.data && <Drift report={drift.data} />}
    </div>
  );
}

function AdaptiveWeights({ report }: { report: AdaptiveWeightsReport | null }) {
  if (!report) return null;
  return (
    <Card
      title="Pesos adaptativos"
      subtitle="El comité puede dar más peso al analista que más acierta. Con pocas operaciones el ajuste es mínimo y siempre está acotado entre la mitad y 1,5 veces el peso inicial."
      actions={
        <Pill tone={report.applied ? 'solid' : 'default'}>
          {report.applied ? 'Aplicándose' : report.enabled ? 'Activo · esperando datos' : 'Vista previa'}
        </Pill>
      }
    >
      <div className="stack">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Analista</th>
                <th>Peso inicial</th>
                <th>Peso propuesto</th>
                <th>Evaluadas</th>
                <th>Motivo</th>
              </tr>
            </thead>
            <tbody>
              {report.agents.map((agent) => (
                <tr key={agent.agent_type}>
                  <td>{AGENT_LABELS[agent.agent_type] ?? agent.agent_type}</td>
                  <td>{Math.round(agent.default_weight * 100)} %</td>
                  <td className="strong">{fmtNumber(agent.weight * 100, 1)} %</td>
                  <td>{agent.decisions_scored}</td>
                  <td className="muted">{agent.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!report.enabled && (
          <p className="muted xsmall">
            Para aplicarlos, activa <code>ADAPTIVE_AGENT_WEIGHTS_ENABLED=true</code> en el servidor. Cada analista
            necesita al menos {report.min_samples} decisiones cerradas antes de moverse. {report.note}
          </p>
        )}
      </div>
    </Card>
  );
}

const DRIFT_LABEL: Record<DriftReportData['status'], { text: string; tone: 'default' | 'pos' | 'neg' | 'warn' }> = {
  stable: { text: 'Estable', tone: 'default' },
  watch: { text: 'Vigilar', tone: 'warn' },
  degraded: { text: 'Empeorando', tone: 'neg' },
  insufficient_data: { text: 'Datos insuficientes', tone: 'default' },
};

function Drift({ report }: { report: DriftReportData }) {
  const label = DRIFT_LABEL[report.status];
  return (
    <Card
      title="¿Está empeorando?"
      subtitle="Compara las operaciones recientes con las anteriores. Solo informa: no detiene el asistente."
      actions={<Pill tone={label.tone}>{label.text}</Pill>}
    >
      <div className="stack">
        <div className="stats">
          <Stat label="Muestra reciente / anterior" value={`${report.recent.sample_size} / ${report.baseline.sample_size}`} />
          <Stat label="Acierto reciente / anterior" value={`${fmtNumber(report.recent.win_rate_pct, 1)} / ${fmtNumber(report.baseline.win_rate_pct, 1)} %`} />
          <Stat label="Factor de beneficio" value={`${fmtNumber(report.recent.profit_factor)} / ${fmtNumber(report.baseline.profit_factor)}`} />
        </div>
        {report.reasons.length > 0 && <p className="muted small">{report.reasons.join(' · ')}</p>}
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------------- Risk */

function Risk() {
  const risk = usePolling<RiskStatusSummary>('risk', () => apiGet<RiskStatusSummary>('/api/v1/risk/status'), 15000);
  const concentration = usePolling<ConcentrationReportData>('concentration', () =>
    apiGet<ConcentrationReportData>('/api/v1/risk/concentration?timeframe=1h&lookback=200'),
  );
  const data = risk.data;

  return (
    <div className="stack-lg">
      <Card
        title="Cortafuegos de riesgo"
        icon="shield"
        subtitle="Si se supera un límite, el asistente deja de abrir operaciones nuevas automáticamente."
        actions={
          data && (
            <Pill tone={data.circuit_breaker_state === 'armed' ? 'default' : 'neg'} dot>
              {data.circuit_breaker_state === 'armed' ? 'Protegiendo' : 'Disparado · operaciones bloqueadas'}
            </Pill>
          )
        }
      >
        {!data ? (
          risk.error ? <Notice tone="error">{risk.error}</Notice> : <Skeleton height={80} />
        ) : (
          <div className="stats">
            <Stat label="Pérdida de hoy" value={fmtPct(data.daily_loss_pct, 2)} sub={`Límite ${fmtPct(data.max_daily_loss_pct, 1)}`} />
            <Stat label="Caída desde el máximo" value={fmtPct(data.current_drawdown_pct, 2)} sub={`Límite ${fmtPct(data.max_drawdown_pct, 1)}`} />
            <Stat label="Posiciones" value={`${data.open_positions_count} / ${data.max_open_positions}`} />
            <Stat
              label="Exposición"
              value={fmtUsd(data.gross_exposure_usd)}
              sub={`Límite ${fmtPct(data.max_portfolio_exposure_pct, 0)} del capital`}
            />
          </div>
        )}
      </Card>

      {concentration.data && (
        <Card
          title="Concentración y correlación"
          subtitle="Cuánto pesa cada activo y cuánto se mueven juntos. Solo informativo."
          actions={
            <button type="button" className="btn btn-sm" onClick={() => void concentration.refresh()}>
              Actualizar
            </button>
          }
        >
          <div className="stack">
            <div className="stats">
              <Stat label="Capital" value={fmtUsd(concentration.data.equity_usd)} />
              <Stat
                label="Exposición"
                value={fmtUsd(concentration.data.gross_exposure_usd)}
                sub={concentration.data.gross_exposure_pct == null ? undefined : fmtPct(concentration.data.gross_exposure_pct)}
              />
              <Stat label="Límite por activo" value={fmtPct(concentration.data.max_position_size_pct, 0)} />
            </div>
            {concentration.data.positions.length === 0 ? (
              <p className="muted small">Sin posiciones abiertas.</p>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Activo</th>
                      <th>Exposición</th>
                      <th>% del capital</th>
                      <th>Límite</th>
                    </tr>
                  </thead>
                  <tbody>
                    {concentration.data.positions.map((position) => (
                      <tr key={position.symbol}>
                        <td className={position.within_limit === false ? 'neg' : ''}>{position.symbol}</td>
                        <td>{fmtUsd(position.exposure_usd)}</td>
                        <td>{fmtPct(position.equity_pct)}</td>
                        <td>{fmtUsd(position.limit_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {concentration.data.correlations.length > 0 && (
              <div className="row">
                {concentration.data.correlations.map((pair) => (
                  <Pill key={`${pair.left}-${pair.right}`} tone={pair.high ? 'warn' : 'default'}>
                    {pair.left} ↔ {pair.right}: {pair.correlation == null ? '—' : fmtNumber(pair.correlation, 2)}
                  </Pill>
                ))}
              </div>
            )}
            {concentration.data.warnings.map((warning) => (
              <Notice key={warning} tone="warn">
                {warning}
              </Notice>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- Market */

function Market({ symbol, timeframe }: { symbol: string; timeframe: string }) {
  const regime = usePolling<MarketRegimeData>(`regime|${symbol}|${timeframe}`, () =>
    apiGet<MarketRegimeData>(`/api/v1/market/${encodeURIComponent(symbol)}/regime?timeframe=${timeframe}&limit=200`),
  );
  const scan = usePolling<{ scan: ShadowScanData | null; assessment: OpportunityAssessmentData | null }>(
    'shadow',
    async () => {
      const history = await apiGet<ShadowScanData[]>('/api/v1/agents/scan/shadow/history?limit=1');
      const latest = history[0] ?? null;
      if (!latest) return { scan: null, assessment: null };
      const assessment = await apiSend<OpportunityAssessmentData>(
        'POST',
        `/api/v1/agents/scan/shadow/${latest.scan_id}/assessment`,
      ).catch(() => null);
      return { scan: latest, assessment };
    },
  );
  const research = usePolling<ResearchContextData>(`research|${symbol}|${timeframe}`, () =>
    apiGet<ResearchContextData>(
      `/api/v1/research/context?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=5`,
    ),
  );
  const r = regime.data;

  return (
    <div className="stack-lg">
      <Card title={`Régimen de ${symbol} · ${timeframe}`} subtitle="Clasificación de las velas cerradas. Describe el mercado; no es una señal.">
        {!r ? (
          <Skeleton height={80} />
        ) : r.status === 'insufficient_data' ? (
          <Notice tone="warn">Datos insuficientes: {r.reasons.join(' ')}</Notice>
        ) : (
          <div className="stack">
            <div className="stats">
              <Stat label="Separación EMA20/50" value={fmtPct(r.metrics.ema_separation_pct, 2)} />
              <Stat label="Pendiente EMA20" value={fmtPct(r.metrics.ema_slope_pct, 2)} />
              <Stat label="Volatilidad (ATR)" value={fmtPct(r.metrics.atr_pct, 2)} sub={`Percentil ${fmtInt(r.metrics.atr_percentile)}`} />
              <Stat label="Fuerza de tendencia" value={`${fmtInt(r.metrics.trend_strength)} / 100`} />
            </div>
            <p className="muted small">{r.reasons.join(' · ')}</p>
          </div>
        )}
      </Card>

      <Card
        title="Escaneo de oportunidades"
        subtitle="Compara varios mercados sin operar ni gastar IA. El sistema se abstiene hasta que haya evidencia suficiente."
      >
        {!scan.data?.scan ? (
          <Empty>Todavía no hay escaneos guardados.</Empty>
        ) : (
          <div className="stack">
            <div className="stats">
              <Stat
                label="Decisión"
                value={scan.data.assessment?.decision === 'eligible_for_manual_review' ? 'Revisar' : 'Abstenerse'}
              />
              <Stat label="Candidato" value={scan.data.assessment?.selected_symbol || 'Ninguno'} />
              <Stat label="Evaluaciones maduras" value={fmtInt(scan.data.assessment?.evidence?.matured_evaluations ?? 0)} />
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Mercado</th>
                    <th>Movimiento neto</th>
                    <th>Spread</th>
                    <th>Régimen</th>
                    <th>Elegible</th>
                  </tr>
                </thead>
                <tbody>
                  {scan.data.scan.candidates.map((candidate) => {
                    const assessed = scan.data?.assessment?.candidates.find((item) => item.symbol === candidate.symbol);
                    return (
                      <tr key={candidate.symbol}>
                        <td>{candidate.rank ?? '—'}</td>
                        <td className="strong">{candidate.symbol}</td>
                        <td className={toneClass(candidate.observed_movement_less_spread_pct)}>
                          {fmtSigned(candidate.observed_movement_less_spread_pct, 3, ' %')}
                        </td>
                        <td>{fmtPct(candidate.quoted_spread_pct, 3)}</td>
                        <td className="muted">
                          {candidate.regime_status === 'classified'
                            ? `${candidate.trend_regime ?? '—'} / ${candidate.volatility_regime ?? '—'}`
                            : 'Sin clasificar'}
                        </td>
                        <td>{assessed ? (assessed.eligible ? 'Sí' : 'No') : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="muted xsmall">{scan.data.assessment?.selection_reason}</p>
          </div>
        )}
      </Card>

      <Card title="Notas de investigación" subtitle={`Contexto guardado para ${symbol}. No altera señales ni riesgo.`}>
        {!research.data || research.data.notes.length === 0 ? (
          <Empty>Sin notas vigentes para este mercado.</Empty>
        ) : (
          <div className="stack">
            {research.data.notes.map((note) => (
              <div key={note.id}>
                <div className="row">
                  <span className="strong small">{note.title}</span>
                  <Pill>{note.validation_status}</Pill>
                </div>
                <p className="muted small" style={{ marginTop: 4 }}>
                  {note.statement}
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- AI usage */

function AiUsage() {
  const status = usePolling<LLMStatusData>('llm-status', () => apiGet<LLMStatusData>('/api/v1/agents/llm/status'), 15000);
  const usage = status.data?.usage;
  if (!status.data) return status.error ? <Notice tone="error">{status.error}</Notice> : <Skeleton height={120} />;
  const exhausted =
    usage?.budget_exhausted ||
    usage?.cycle_budget_exhausted ||
    usage?.request_budget_exhausted ||
    usage?.cycle_request_budget_exhausted;

  const ofBudget = (used: unknown, budget: unknown) => {
    const b = toNumber(budget) ?? 0;
    return b > 0 ? `${fmtInt(used)} / ${fmtInt(b)}` : `${fmtInt(used)} · sin tope`;
  };

  return (
    <div className="stack-lg">
      <Card
        title="Consumo de IA hoy"
        icon="cpu"
        subtitle={
          status.data.mode === 'llm_with_fallback'
            ? `Principal: ${status.data.primary_provider ?? '—'} · respaldo: ${(status.data.fallback_providers ?? []).join(', ') || 'ninguno'}`
            : 'Sin proveedores configurados: el comité usa el análisis determinista.'
        }
        actions={<Pill tone={exhausted ? 'neg' : 'default'}>{exhausted ? 'Presupuesto agotado' : 'Presupuesto disponible'}</Pill>}
      >
        <div className="stats">
          <Stat label="Tokens hoy" value={ofBudget(usage?.tokens_used, usage?.daily_token_budget)} />
          <Stat label="Peticiones hoy" value={ofBudget(usage?.request_attempts, usage?.daily_request_budget)} />
          <Stat label="Tokens del ciclo" value={ofBudget(usage?.cycle_tokens_used, usage?.cycle_token_budget)} />
          <Stat label="Peticiones del ciclo" value={ofBudget(usage?.cycle_requests_used, usage?.cycle_request_budget)} />
          <Stat label="Respaldos usados" value={fmtInt(usage?.fallbacks ?? 0)} />
          <Stat
            label="Coste estimado"
            value={usage?.pricing?.configured ? fmtUsd(usage.estimated_cost_usd, 4) : '—'}
            sub={usage?.pricing?.configured ? undefined : 'Tarifas sin configurar'}
          />
        </div>
      </Card>

      {(usage?.provider_cooldowns?.length ?? 0) > 0 && (
        <Notice tone="warn">
          En espera tras un fallo:{' '}
          {usage?.provider_cooldowns
            ?.map((item) => `${item.provider} (${item.reason}, ${Math.ceil(item.seconds_remaining / 60)} min)`)
            .join(', ')}
        </Notice>
      )}

      {Object.keys(usage?.by_provider ?? {}).length > 0 && (
        <Card title="Por proveedor">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Proveedor</th>
                  <th>Llamadas</th>
                  <th>Fallos</th>
                  <th>Tokens</th>
                  <th>Coste</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(usage?.by_provider ?? {}).map(([name, bucket]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{fmtInt(bucket.requests)}</td>
                    <td>{fmtInt(bucket.failures)}</td>
                    <td>{fmtInt(bucket.total_tokens)}</td>
                    <td>{bucket.estimated_cost_usd == null ? '—' : fmtUsd(bucket.estimated_cost_usd, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- System */

const SERVICE_LABEL: Record<string, string> = {
  database: 'Base de datos',
  redis: 'Caché',
  market_data: 'Datos de mercado',
  trading_engine: 'Motor de órdenes',
};

function SystemStatus() {
  const status = usePolling<Record<string, string>>('system', () => apiGet<Record<string, string>>('/api/v1/status'), 15000);
  if (!status.data) return status.error ? <Notice tone="error">{status.error}</Notice> : <Skeleton height={80} />;
  return (
    <Card title="Componentes" subtitle="Estado de los servicios internos.">
      <div className="stats">
        {Object.entries(status.data).map(([service, state]) => {
          const ok = state === 'healthy' || state === 'ok' || state === 'connected';
          return (
            <Stat
              key={service}
              label={SERVICE_LABEL[service] ?? service}
              value={ok ? 'En línea' : String(state)}
              tone={ok ? '' : 'neg'}
            />
          );
        })}
      </div>
    </Card>
  );
}
