import { useState } from 'react';
import toast from 'react-hot-toast';
import type { Page } from '../App';
import { Card, Empty, Notice, PageHead, Pill, Skeleton, Stat, SubNav } from '../components/ui';
import { usePolling } from '../hooks/usePolling';
import { useI18n, type MessageKey } from '../i18n/core';
import { agentLabel, regimeLabel, signalLabel } from '../i18n/labels';
import { apiGet, apiSend, errorMessage } from '../lib/api';
import { fmtInt, fmtNumber, fmtPct, fmtSigned, fmtUsd, toNumber, toneClass } from '../lib/format';
import {
  AGENT_TYPES,
  AVAILABLE_SYMBOLS,
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

const SECTIONS: ReadonlyArray<{ id: Section; label: MessageKey }> = [
  { id: 'comite', label: 'adv.nav.committee' },
  { id: 'aprendizaje', label: 'adv.nav.learning' },
  { id: 'riesgo', label: 'adv.nav.risk' },
  { id: 'mercado', label: 'adv.nav.market' },
  { id: 'consumo', label: 'adv.nav.usage' },
  { id: 'sistema', label: 'adv.nav.system' },
];

interface Props {
  section: string | null;
  navigate: (page: Page, section?: string) => void;
  symbol: string;
  timeframe: string;
}

export function AdvancedView({ section, navigate, symbol, timeframe }: Props) {
  const { t } = useI18n();
  const active: Section = SECTIONS.some((item) => item.id === section) ? (section as Section) : 'comite';
  return (
    <>
      <PageHead title={t('adv.title')} description={t('adv.description')} />
      <SubNav
        label={t('adv.nav.label')}
        items={SECTIONS.map((item) => ({ id: item.id, label: t(item.label) }))}
        value={active}
        onChange={(id) => navigate('advanced', id)}
      />
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

function Committee({ symbol, timeframe }: { symbol: string; timeframe: string }) {
  const { t } = useI18n();
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
      toast.error(t('committee.failed', { detail: errorMessage(error) }));
    } finally {
      setBusy(false);
    }
  };

  const liveWeights = decision?.agent_weights ?? weights.data?.weights;

  return (
    <div className="stack-lg">
      <Card
        title={t('committee.title')}
        subtitle={t('committee.subtitle', { symbol, timeframe })}
        actions={
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void analyze()}>
            {busy ? t('committee.analyzing') : t('committee.analyze', { symbol })}
          </button>
        }
      >
        {decision ? (
          <div className="stack">
            <div className="row">
              <Pill tone="solid">{signalLabel(t, decision.final_signal)}</Pill>
              <Pill>{t('committee.strength', { value: Math.round(decision.aggregate_confidence * 100) })}</Pill>
              <Pill>{t('committee.consensus', { value: Math.round(decision.consensus_level * 100) })}</Pill>
              {decision.recommended_stop && <Pill>{t('committee.stop', { value: fmtUsd(decision.recommended_stop) })}</Pill>}
              {decision.recommended_target && (
                <Pill>{t('committee.target', { value: fmtUsd(decision.recommended_target) })}</Pill>
              )}
            </div>
            <div className="stack" style={{ gap: 6 }}>
              {decision.reasoning_summary.split(' | ').map((line) => (
                <p key={line} className="muted small">
                  {line}
                </p>
              ))}
            </div>
            <p className="muted xsmall">{t('committee.reasoningNote')}</p>
          </div>
        ) : (
          <Empty>{t('committee.empty')}</Empty>
        )}
      </Card>

      <div className="cols">
        {AGENT_TYPES.map((agent) => {
          const weight = liveWeights?.[agent];
          return (
            <Card
              key={agent}
              title={agentLabel(t, agent)}
              actions={weight !== undefined ? <Pill>{t('committee.weight', { value: Math.round(weight * 100) })}</Pill> : null}
            >
              <p className="muted small">{t(`agent.${agent}.description`)}</p>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- Learning */

function Learning() {
  const { t } = useI18n();
  const [symbol, setSymbol] = useState('');
  // Real activity only (Testnet or Mainnet); the simulated mode was retired.
  const params = new URLSearchParams({ limit: '200', mode: 'real' });
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
        title={t('learning.title')}
        subtitle={t('learning.subtitle')}
        actions={
          <>
            <select
              className="select"
              style={{ width: 'auto' }}
              aria-label={t('assistant.market')}
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
            >
              <option value="">{t('learning.allMarkets')}</option>
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
          <Notice tone="warn">{t('learning.dbDown')}</Notice>
        ) : (
          <div className="stats">
            <Stat
              label={t('learning.decisions')}
              value={fmtInt(data.total_decisions)}
              sub={t('learning.executed', { count: fmtInt(data.executed) })}
            />
            <Stat label={t('learning.closed')} value={fmtInt(data.resolved)} />
            <Stat label={t('learning.hitRate')} value={fmtPct(data.win_rate_pct)} />
            <Stat label={t('learning.net')} value={fmtSigned(data.net_pnl, 2, ' $')} tone={toneClass(data.net_pnl)} />
            <Stat
              label={t('learning.profitFactor')}
              value={data.profit_factor === null ? '—' : fmtNumber(data.profit_factor)}
            />
            <Stat label={t('learning.unattributed')} value={fmtInt(data.unattributed_orders)} />
          </div>
        )}
      </Card>

      <AdaptiveWeights report={weights.data} />

      {data?.data_available && data.by_agent.length > 0 && (
        <Card title={t('learning.byAgent')} subtitle={t('learning.byAgent.subtitle')}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('learning.col.agent')}</th>
                  <th>{t('learning.col.hitRate')}</th>
                  <th>{t('learning.col.hits')}</th>
                  <th>{t('learning.col.abstained')}</th>
                  <th>{t('learning.col.latency')}</th>
                </tr>
              </thead>
              <tbody>
                {data.by_agent.map((agent) => (
                  <tr key={agent.agent_type}>
                    <td>{agentLabel(t, agent.agent_type)}</td>
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
          <Card title={t('learning.bySignal')}>
            {data.by_signal.length === 0 ? (
              <Empty>{t('common.noData')}</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('learning.col.signal')}</th>
                      <th>{t('learning.col.hitRate')}</th>
                      <th>{t('learning.col.net')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_signal.map((item) => (
                      <tr key={item.signal}>
                        <td>{signalLabel(t, item.signal)}</td>
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
          <Card title={t('learning.bySymbol')}>
            {data.by_symbol.length === 0 ? (
              <Empty>{t('common.noData')}</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('ops.col.market')}</th>
                      <th>{t('learning.col.hitRate')}</th>
                      <th>{t('learning.col.net')}</th>
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
        <Card title={t('learning.byStrength')} subtitle={t('learning.byStrength.subtitle')}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('learning.col.strength')}</th>
                  <th>{t('learning.closed')}</th>
                  <th>{t('learning.col.hitRate')}</th>
                  <th>{t('learning.col.sample')}</th>
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
                      {bucket.sample_sufficient
                        ? t('learning.sample.enough')
                        : t('learning.sample.short', { n: bucket.decisions, min: bucket.minimum_sample_size })}
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
  const { t } = useI18n();
  if (!report) return null;
  const reason = (agent: AdaptiveWeightsReport['agents'][number]) => {
    if (agent.agent_type === 'risk_manager') return t('weights.reason.risk');
    if (!agent.adjusted) return t('weights.reason.samples', { n: agent.decisions_scored, min: report.min_samples });
    return t('weights.reason.adjusted', { rate: fmtPct(agent.hit_rate_pct) });
  };
  return (
    <Card
      title={t('weights.title')}
      subtitle={t('weights.subtitle')}
      actions={
        <Pill tone={report.applied ? 'solid' : 'default'}>
          {report.applied ? t('weights.applied') : report.enabled ? t('weights.waiting') : t('weights.preview')}
        </Pill>
      }
    >
      <div className="stack">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('learning.col.agent')}</th>
                <th>{t('weights.col.default')}</th>
                <th>{t('weights.col.proposed')}</th>
                <th>{t('weights.col.scored')}</th>
                <th>{t('weights.col.reason')}</th>
              </tr>
            </thead>
            <tbody>
              {report.agents.map((agent) => (
                <tr key={agent.agent_type}>
                  <td>{agentLabel(t, agent.agent_type)}</td>
                  <td>{Math.round(agent.default_weight * 100)} %</td>
                  <td className="strong">{fmtNumber(agent.weight * 100, 1)} %</td>
                  <td>{agent.decisions_scored}</td>
                  <td className="muted">{reason(agent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!report.enabled && (
          <p className="muted xsmall">
            {t('weights.enableHint', { min: report.min_samples })} <code>ADAPTIVE_AGENT_WEIGHTS_ENABLED=true</code>
          </p>
        )}
        <p className="muted xsmall">{t('weights.note')}</p>
      </div>
    </Card>
  );
}

const DRIFT_TONE: Record<DriftReportData['status'], 'default' | 'warn' | 'neg'> = {
  stable: 'default',
  watch: 'warn',
  degraded: 'neg',
  insufficient_data: 'default',
};

function Drift({ report }: { report: DriftReportData }) {
  const { t } = useI18n();
  return (
    <Card
      title={t('drift.title')}
      subtitle={t('drift.subtitle')}
      actions={<Pill tone={DRIFT_TONE[report.status]}>{t(`drift.status.${report.status}`)}</Pill>}
    >
      <div className="stack">
        <div className="stats">
          <Stat label={t('drift.sample')} value={`${report.recent.sample_size} / ${report.baseline.sample_size}`} />
          <Stat
            label={t('drift.hitRate')}
            value={`${fmtNumber(report.recent.win_rate_pct, 1)} / ${fmtNumber(report.baseline.win_rate_pct, 1)} %`}
          />
          <Stat
            label={t('learning.profitFactor')}
            value={`${fmtNumber(report.recent.profit_factor)} / ${fmtNumber(report.baseline.profit_factor)}`}
          />
        </div>
        {report.reasons.length > 0 && <p className="muted small">{report.reasons.join(' · ')}</p>}
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------------- Risk */

function Risk() {
  const { t } = useI18n();
  const risk = usePolling<RiskStatusSummary>('risk', () => apiGet<RiskStatusSummary>('/api/v1/risk/status'), 15000);
  const concentration = usePolling<ConcentrationReportData>('concentration', () =>
    apiGet<ConcentrationReportData>('/api/v1/risk/concentration?timeframe=1h&lookback=200'),
  );
  const data = risk.data;

  return (
    <div className="stack-lg">
      <Card
        title={t('risk.title')}
        icon="shield"
        subtitle={t('risk.subtitle')}
        actions={
          data && (
            <Pill tone={data.circuit_breaker_state === 'armed' ? 'default' : 'neg'} dot>
              {data.circuit_breaker_state === 'armed' ? t('risk.armed') : t('risk.tripped')}
            </Pill>
          )
        }
      >
        {!data ? (
          risk.error ? <Notice tone="error">{risk.error}</Notice> : <Skeleton height={80} />
        ) : (
          <div className="stats">
            <Stat
              label={t('risk.dailyLoss')}
              value={fmtPct(data.daily_loss_pct, 2)}
              sub={t('risk.limit', { value: fmtPct(data.max_daily_loss_pct, 1) })}
            />
            <Stat
              label={t('risk.drawdown')}
              value={fmtPct(data.current_drawdown_pct, 2)}
              sub={t('risk.limit', { value: fmtPct(data.max_drawdown_pct, 1) })}
            />
            <Stat label={t('bot.positions')} value={`${data.open_positions_count} / ${data.max_open_positions}`} />
            <Stat
              label={t('risk.exposure')}
              value={fmtUsd(data.gross_exposure_usd)}
              sub={t('risk.exposureLimit', { value: fmtPct(data.max_portfolio_exposure_pct, 0) })}
            />
          </div>
        )}
      </Card>

      {concentration.data && (
        <Card
          title={t('concentration.title')}
          subtitle={t('concentration.subtitle')}
          actions={
            <button type="button" className="btn btn-sm" onClick={() => void concentration.refresh()}>
              {t('common.refresh')}
            </button>
          }
        >
          <div className="stack">
            <div className="stats">
              <Stat label={t('concentration.equity')} value={fmtUsd(concentration.data.equity_usd)} />
              <Stat
                label={t('risk.exposure')}
                value={fmtUsd(concentration.data.gross_exposure_usd)}
                sub={concentration.data.gross_exposure_pct == null ? undefined : fmtPct(concentration.data.gross_exposure_pct)}
              />
              <Stat label={t('concentration.perAsset')} value={fmtPct(concentration.data.max_position_size_pct, 0)} />
            </div>
            {concentration.data.positions.length === 0 ? (
              <p className="muted small">{t('ops.positions.empty')}</p>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t('concentration.col.asset')}</th>
                      <th>{t('risk.exposure')}</th>
                      <th>{t('concentration.col.share')}</th>
                      <th>{t('concentration.col.limit')}</th>
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
  const { t } = useI18n();
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
  const regimeText = (trend: string | null | undefined, volatility: string | null | undefined) =>
    `${regimeLabel(t, 'trend', trend)} / ${regimeLabel(t, 'volatility', volatility)}`;

  return (
    <div className="stack-lg">
      <Card title={t('market.regime.title', { symbol, timeframe })} subtitle={t('market.regime.subtitle')}>
        {!r ? (
          <Skeleton height={80} />
        ) : r.status === 'insufficient_data' ? (
          <Notice tone="warn">{t('market.regime.insufficient', { detail: r.reasons.join(' ') })}</Notice>
        ) : (
          <div className="stack">
            <div className="row">
              {r.trend && <Pill tone="solid">{t(`regime.trend.${r.trend}`)}</Pill>}
              {r.volatility && <Pill>{t(`regime.volatility.${r.volatility}`)}</Pill>}
            </div>
            <div className="stats">
              <Stat label={t('market.regime.emaGap')} value={fmtPct(r.metrics.ema_separation_pct, 2)} />
              <Stat label={t('market.regime.emaSlope')} value={fmtPct(r.metrics.ema_slope_pct, 2)} />
              <Stat
                label={t('market.regime.atr')}
                value={fmtPct(r.metrics.atr_pct, 2)}
                sub={t('market.regime.percentile', { value: fmtInt(r.metrics.atr_percentile) })}
              />
              <Stat label={t('market.regime.strength')} value={`${fmtInt(r.metrics.trend_strength)} / 100`} />
            </div>
            <p className="muted small">{r.reasons.join(' · ')}</p>
          </div>
        )}
      </Card>

      <Card title={t('market.scan.title')} subtitle={t('market.scan.subtitle')}>
        {!scan.data?.scan ? (
          <Empty>{t('market.scan.empty')}</Empty>
        ) : (
          <div className="stack">
            <div className="stats">
              <Stat
                label={t('market.scan.decision')}
                value={
                  scan.data.assessment?.decision === 'eligible_for_manual_review'
                    ? t('market.scan.review')
                    : t('market.scan.abstain')
                }
              />
              <Stat label={t('market.scan.candidate')} value={scan.data.assessment?.selected_symbol || t('common.none')} />
              <Stat
                label={t('market.scan.matured')}
                value={fmtInt(scan.data.assessment?.evidence?.matured_evaluations ?? 0)}
              />
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t('ops.col.market')}</th>
                    <th>{t('market.scan.col.move')}</th>
                    <th>{t('market.scan.col.spread')}</th>
                    <th>{t('market.scan.col.regime')}</th>
                    <th>{t('market.scan.col.eligible')}</th>
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
                            ? regimeText(candidate.trend_regime, candidate.volatility_regime)
                            : t('market.scan.unclassified')}
                        </td>
                        <td>{assessed ? (assessed.eligible ? t('common.yes') : t('common.no')) : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      <Card title={t('market.notes.title')} subtitle={t('market.notes.subtitle', { symbol })}>
        {!research.data || research.data.notes.length === 0 ? (
          <Empty>{t('market.notes.empty')}</Empty>
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
  const { t } = useI18n();
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
    return b > 0 ? `${fmtInt(used)} / ${fmtInt(b)}` : t('usage.noCap', { used: fmtInt(used) });
  };

  return (
    <div className="stack-lg">
      <Card
        title={t('usage.title')}
        icon="cpu"
        subtitle={
          status.data.mode === 'llm_with_fallback'
            ? t('usage.chain', {
                primary: status.data.primary_provider ?? '—',
                fallback: (status.data.fallback_providers ?? []).join(', ') || t('common.none'),
              })
            : t('usage.deterministic')
        }
        actions={<Pill tone={exhausted ? 'neg' : 'default'}>{exhausted ? t('usage.exhausted') : t('usage.available')}</Pill>}
      >
        <div className="stats">
          <Stat label={t('usage.tokensToday')} value={ofBudget(usage?.tokens_used, usage?.daily_token_budget)} />
          <Stat label={t('usage.requestsToday')} value={ofBudget(usage?.request_attempts, usage?.daily_request_budget)} />
          <Stat label={t('usage.tokensCycle')} value={ofBudget(usage?.cycle_tokens_used, usage?.cycle_token_budget)} />
          <Stat
            label={t('usage.requestsCycle')}
            value={ofBudget(usage?.cycle_requests_used, usage?.cycle_request_budget)}
          />
          <Stat label={t('usage.fallbacks')} value={fmtInt(usage?.fallbacks ?? 0)} />
          <Stat
            label={t('usage.cost')}
            value={usage?.pricing?.configured ? fmtUsd(usage.estimated_cost_usd, 4) : '—'}
            sub={usage?.pricing?.configured ? undefined : t('usage.noRates')}
          />
        </div>
      </Card>

      {(usage?.provider_cooldowns?.length ?? 0) > 0 && (
        <Notice tone="warn">
          {t('usage.cooldowns', {
            list:
              usage?.provider_cooldowns
                ?.map((item) => `${item.provider} (${item.reason}, ${Math.ceil(item.seconds_remaining / 60)} min)`)
                .join(', ') ?? '',
          })}
        </Notice>
      )}

      {Object.keys(usage?.by_provider ?? {}).length > 0 && (
        <Card title={t('usage.byProvider')}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('usage.col.provider')}</th>
                  <th>{t('usage.col.calls')}</th>
                  <th>{t('usage.col.failures')}</th>
                  <th>{t('usage.col.tokens')}</th>
                  <th>{t('usage.col.cost')}</th>
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

const SERVICE_LABEL: Record<string, MessageKey> = {
  database: 'system.database',
  redis: 'system.cache',
  market_data: 'system.marketData',
  trading_engine: 'system.engine',
};

function SystemStatus() {
  const { t } = useI18n();
  const status = usePolling<Record<string, string>>('system', () => apiGet<Record<string, string>>('/api/v1/status'), 15000);
  if (!status.data) return status.error ? <Notice tone="error">{status.error}</Notice> : <Skeleton height={80} />;
  return (
    <Card title={t('system.title')} subtitle={t('system.subtitle')}>
      <div className="stats">
        {Object.entries(status.data).map(([service, state]) => {
          const ok = state === 'healthy' || state === 'ok' || state === 'connected';
          const labelKey = SERVICE_LABEL[service];
          return (
            <Stat
              key={service}
              label={labelKey ? t(labelKey) : service}
              value={ok ? t('system.online') : String(state)}
              tone={ok ? '' : 'neg'}
            />
          );
        })}
      </div>
    </Card>
  );
}
