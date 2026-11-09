import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid, Legend } from "recharts";
import { apiGet } from "../api";
import { useApi } from "../hooks";
import { Card, QueryView, StatCard, ErrorState } from "../components/ui";
import { fmtNum } from "../format";
import CompletionResearch from "./CompletionResearch";
import QuantEquations from "./QuantEquations";
import InformationResearch from "./InformationResearch";
import FinancialPilot from "./FinancialPilot";

type Metric = { passed: boolean; reasons: string[]; brier?: number; baseline_brier?: number; calibration_error?: number; rows?: number; dates?: number; brier_difference_block_interval?: { blocks: number }; reliability: { predicted: number; observed: number; rows: number; dates: number }[] };
type Range = { low: number; median: number; high: number };
type Horizon = { fit_rows: number; fit_dates: number; assessment_rows: number; assessment_dates: number; matured_missing_fraction: number; probability_metrics: Record<string, Metric>; range_metrics: { passed: boolean; reasons: string[]; observed_coverage?: number; nominal_coverage?: number; mean_width?: number }; range_model: { expansion: number; bands: Record<string, Range> } | null; split: { probability_fit_before: string; range_calibration_before: string; assessment_from: string; assessment_through: string } };
type Report = { status: string; evidence_current: boolean; horizons: Record<string, Horizon>; limits: string[]; entitlements: { id: string; symbols: string[]; terms_verified: boolean; terms_source: string; ex_date: string | null; valuation_status: string; entitlements: { asset_id: string; name: string; ratio: number; listing_date: string | null }[] }[] };
type Forecast = { status: string; symbol: string; reasons: string[]; profit_probability: number | null; outperform_probability: number | null; net_return_band: Range | null; exit_value_band: Range | null };
type BaselineMetrics = { status: string; net_return: number | null; max_drawdown: number | null; closed_trades: number; mean_entry_exposure?: number; blocked: { date: string; symbol: string; reason: string } | null };
type PairedComparison = { paired_cycles: number; mean_cycle_difference: number | null; descriptive_95pct_interval: [number, number] | null; full_period_comparable: boolean };
type TournamentRetail = { status: string; net_return: number | null; max_drawdown: number | null; closed_trades: number; blocked: { date: string; symbol: string; reason: string } | null };
type TournamentReport = { status: string; selected_on_validation: string | null; evidence_current: boolean; stale_reasons: string[]; limits: string[]; progress?: { stage: string; candidate: string } | null; paper_tracking?: { status: string; last_source_session?: string; captured_cohorts?: number; completed_cohorts?: number }; paper_candidate_assessment?: { supported: boolean; reasons: string[] }; candidates: Record<string, { family: string; features: string; target: string; assessment_quality: { mean_daily_rank_ic: number | null } }>; results: Record<string, Record<string, { retail: Record<string, TournamentRetail>; matched_momentum_retail: Record<string, TournamentRetail>; signal_dates: number; selection_diagnostic: { strategies: Record<string, { status: string; net_return: number | null }> } }>> };
type BaselineReport = { status: string; evidence_current: boolean; stale_reasons: string[]; limitations: string[]; results: Record<string, { scheduled_cycles: number; strategies: Record<string, BaselineMetrics>; paired_comparisons: Record<string, PairedComparison>; selection_diagnostic?: { method: string; strategies: Record<string, { status: string; net_return: number | null; complete_cycles: number }>; paired_comparisons: Record<string, PairedComparison> } }> };
const pct = (v: number | null | undefined) => v == null ? "—" : `${(v * 100).toFixed(1)}%`;

export default function ForecastLab() {
  const report = useApi<Report>(["forecast-report"], "/forecasts/report");
  const baselines = useApi<BaselineReport>(["baseline-comparison"], "/research/baselines");
  const tournament = useApi<TournamentReport>(["model-tournament"], "/research/tournament");
  const [tournamentPeriod, setTournamentPeriod] = useState("assessment");
  const [horizon, setHorizon] = useState("21");
  const [symbol, setSymbol] = useState("RELIANCE");
  const preview = useMutation({ mutationFn: () => apiGet<Forecast>(`/forecasts/${encodeURIComponent(symbol.trim())}?horizon=${horizon}`) });
  const [target, setTarget] = useState("profit");
  return <>
    <div className="page-head"><div><h1 className="page-title">Forecast Lab</h1><p className="page-sub">Probability calibration, net-return ranges and entitlement evidence</p></div></div>
    <div className="seg" style={{ marginBottom: 16 }}>{["21", "63"].map(h => <button key={h} className={horizon === h ? "active" : ""} onClick={() => { setHorizon(h); preview.reset(); }}>{h} trading sessions</button>)}</div>
    <div className="grid" style={{ gap: 16 }}>
      <QuantEquations />
      <InformationResearch />
      <FinancialPilot />
      <CompletionResearch />
      <QueryView query={tournament}>{r => <Card title="Architecture tournament" subtitle={`21-session historical research · ${r.status.replace(/_/g, " ")}`}>
        <p className="muted">Linear, LightGBM and CatBoost candidates share feature rows and chronological splits. Price-only versus provisional fundamentals, and existing versus execution-aligned targets, are separate arms. The small TCN has a flattened-window LightGBM control using the same historical inputs. Configuration choices and the paper candidate are locked using earlier validation only.</p>
        {r.progress && <p className="muted">{r.progress.stage.replace(/_/g, " ")} · {r.progress.candidate}</p>}
        {r.status === "not_available" && <p className="muted">No completed tournament yet.</p>}
        {r.stale_reasons.map(reason => <p className="muted" key={reason}>{reason}; rerun before relying on this evidence.</p>)}
        {Object.keys(r.results).length > 0 && <>
          <p><strong>Validation-only paper choice: {r.selected_on_validation ?? "none qualified"}</strong></p>
          {r.paper_candidate_assessment && <p className="muted">{r.paper_candidate_assessment.supported ? "The frozen choice passed the later historical comparison checks; fresh evidence is still required." : "The later assessment does not support promoting the frozen choice. Paper tracking remains a research diagnostic."}</p>}
          <p className="muted">Paper tracking: {r.paper_tracking?.status.replace(/_/g, " ") ?? "unavailable"}{r.paper_tracking?.last_source_session && ` · Latest source session ${r.paper_tracking.last_source_session}`}. Production rankings and live probability readiness are unchanged.</p>
          <div className="seg" style={{ marginBottom: 12 }}>{[["validation", "Earlier validation"], ["assessment", "Later assessment"]].map(([key, label]) => <button key={key} className={tournamentPeriod === key ? "active" : ""} onClick={() => setTournamentPeriod(key)}>{label}</button>)}</div>
          <div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Signal dates</th><th>Retail status</th><th>Net return</th><th>Matched momentum</th><th>Max drawdown</th><th>Doubled costs</th><th>Delayed entry</th><th>Equal-notional diagnostic</th></tr></thead><tbody>
            {Object.entries(r.results).map(([name, periods]) => { const item = periods[tournamentPeriod]; if (!item) return null; const base = item.retail.base; return <tr key={name}><td title={name}>{name.replace(/_/g, " ")}{name === r.selected_on_validation ? " · frozen for paper" : ""}</td><td>{item.signal_dates}</td><td title={base.blocked ? `${base.blocked.symbol} ${base.blocked.date}: ${base.blocked.reason}` : undefined}>{base.status.replace(/_/g, " ")}</td><td>{pct(base.net_return)}</td><td>{pct(item.matched_momentum_retail.base.net_return)}</td><td>{pct(base.max_drawdown)}</td><td>{pct(item.retail.double_costs.net_return)}</td><td>{pct(item.retail.entry_delayed.net_return)}</td><td>{pct(item.selection_diagnostic.strategies.model.net_return)}</td></tr>; })}
          </tbody></table></div>
          <p className="muted">Retail columns use the actual ten-position allocator, whole shares and cash limits. Matched momentum uses each candidate's identical available stocks and signal dates; sequence windows can reduce that pool. The equal-notional column isolates selection and is not an executable portfolio. Unavailable returns remain blank; incomplete runs cannot qualify as winners. This later history was previously researched and is not a fresh holdout.</p>
          <details><summary>Tournament limits</summary>{r.limits.map(limit => <p className="muted" key={limit}>{limit}</p>)}</details>
        </>}
      </Card>}</QueryView>
      <QueryView query={baselines}>{r => <Card title="Does the model beat simple strategies?" subtitle={`Fixed 21-session historical comparison · ${r.status.replace(/_/g, " ")}`}>
        <p className="muted">All three start with the same eligible stocks and use equal target weights, whole shares, cash/risk/industry caps, next-session-close entries, an 8% stop, and the same fees and slippage. Model and 63-session momentum select 20 stocks; equal weight selects the full eligible pool. Entries use non-overlapping 21-session cycles. The shared research position ceiling allows the full-pool baseline and differs from the live ten-position planner.</p>
        {r.status === "not_available" && <p className="muted">No completed comparison yet.</p>}
        {r.stale_reasons.map(reason => <p className="muted" key={reason}>{reason}; rerun the comparison before relying on these measurements.</p>)}
        {Object.entries(r.results).map(([period, result]) => <div key={period} style={{ marginTop: 16 }}>
          <strong>{period === "assessment" ? "Later assessment" : "Earlier validation"} · {result.scheduled_cycles} scheduled cycles</strong>
          <div className="table-wrap"><table><thead><tr><th>Strategy</th><th>Status</th><th>Net return</th><th>Max drawdown</th><th>Mean entry exposure</th><th>Closed trades</th></tr></thead><tbody>
            {Object.entries(result.strategies).map(([name, m]) => <tr key={name}><td>{({ model: "Model top 20", momentum: "Momentum top 20", equal_weight: "All eligible, equal targets" } as Record<string, string>)[name] ?? name}</td><td>{m.status.replace(/_/g, " ")}{m.blocked && `: ${m.blocked.symbol} ${m.blocked.date} — ${m.blocked.reason}`}</td><td>{pct(m.net_return)}</td><td>{pct(m.max_drawdown)}</td><td>{pct(m.mean_entry_exposure)}</td><td>{m.closed_trades}</td></tr>)}
          </tbody></table></div>
          {Object.entries(result.paired_comparisons).map(([name, p]) => <p className="muted" key={name}>Model minus {name.replace(/_/g, " ")}: mean cycle difference {pct(p.mean_cycle_difference)}; descriptive 95% interval {p.descriptive_95pct_interval ? `${pct(p.descriptive_95pct_interval[0])} to ${pct(p.descriptive_95pct_interval[1])}` : "unavailable"} across {p.paired_cycles} paired cycles.{!p.full_period_comparable && " Partial completed cycles only; full-period comparison is unavailable."}</p>)}
          {result.selection_diagnostic && <details><summary>Stock selection with equal notional exposure</summary>
            <p className="muted">{result.selection_diagnostic.method}</p>
            <div className="table-wrap"><table><thead><tr><th>Strategy</th><th>Status</th><th>Net return</th><th>Complete cycles</th></tr></thead><tbody>
              {Object.entries(result.selection_diagnostic.strategies).map(([name, m]) => <tr key={name}><td>{name.replace(/_/g, " ")}</td><td>{m.status}</td><td>{pct(m.net_return)}</td><td>{m.complete_cycles}</td></tr>)}
            </tbody></table></div>
            {Object.entries(result.selection_diagnostic.paired_comparisons).map(([name, p]) => <p className="muted" key={name}>Equal-notional model minus {name.replace(/_/g, " ")}: mean cycle difference {pct(p.mean_cycle_difference)}; descriptive 95% interval {p.descriptive_95pct_interval ? `${pct(p.descriptive_95pct_interval[0])} to ${pct(p.descriptive_95pct_interval[1])}` : "unavailable"} across {p.paired_cycles} paired cycles.{!p.full_period_comparable && " Partial completed cycles only; full-period comparison is unavailable."}</p>)}
          </details>}
        </div>)}
        <p className="muted">An interval spanning zero does not establish an advantage. This history was explored before; no strategy is promoted from these results.</p>
        <details><summary>Comparison limits</summary>{r.limitations.map(limit => <p className="muted" key={limit}>{limit}</p>)}</details>
      </Card>}</QueryView>
      <Card title="What the forecast means" subtitle="Research estimates are separated from live forecast readiness">
        <p className="muted">The tested scenario enters at the next session's close, uses an 8% stop, exits after the chosen holding period if the stop has not filled, and estimates 10 bps fees plus 10 bps slippage on each side. Gap-through stops fill at the worse opening price. This differs from merely asking whether a stock will rise by the end date.</p>
        <p className="muted">An 80% net-return band describes scenario proceeds. Its price equivalent is an illustrative exit value per reference share, not a guaranteed target or a prediction of the horizon-end quoted market price. Production estimates are withheld when evidence, model lineage or data freshness does not pass.</p>
      </Card>
      <QueryView query={report}>{r => {
        const item = r.horizons[horizon];
        if (!item) return <Card title="No calibration report yet"><p className="muted">Run the outcome audit and calibration research to populate this page.</p></Card>;
        const metric = item.probability_metrics[target];
        const chart = [{ predicted: 0, observed: null, ideal: 0 }, ...metric.reliability.map(b => ({ predicted: b.predicted * 100, observed: b.observed * 100, ideal: b.predicted * 100 })), { predicted: 100, observed: null, ideal: 100 }];
        return <>
          <Card title="Evidence status" subtitle={`${r.status} · ${r.evidence_current ? "current corporate-action ledger" : "corporate-action evidence changed; rerun the audit"}`}>
            <p className="muted">Probability fit outcomes mature before {item.split.probability_fit_before}; range calibration outcomes mature before {item.split.range_calibration_before}. Later assessment: {item.split.assessment_from} to {item.split.assessment_through}. This history was researched previously and is not an untouched future trial.</p>
            <p className="muted">{item.fit_rows.toLocaleString()} fit cases across {item.fit_dates} dates · {item.assessment_rows.toLocaleString()} completed later cases across {item.assessment_dates} dates · {pct(item.matured_missing_fraction)} of eligible matured outcomes unavailable. Cases share dates and overlap; their raw count is not independent evidence.</p>
          </Card>
          <div className="seg">{[["profit", "Profit after costs"], ["outperform", "Beat universe after costs"]].map(([key, label]) => <button key={key} className={target === key ? "active" : ""} onClick={() => setTarget(key)}>{label}</button>)}</div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
            <StatCard label="Probability check" value={metric.passed ? "Passed historical checks" : "Insufficient evidence"} sub={`${metric.brier_difference_block_interval?.blocks ?? 0} non-overlapping date blocks`} />
            <StatCard label="Brier score (lower is better)" value={fmtNum(metric.brier, 4)} sub={`Constant baseline: ${fmtNum(metric.baseline_brier, 4)}`} />
            <StatCard label="Calibration error" value={metric.calibration_error == null ? "—" : `${(metric.calibration_error * 100).toFixed(1)} percentage points`} sub="Gap between predicted and observed frequencies" />
            <StatCard label="80% band: later coverage" value={pct(item.range_metrics.observed_coverage)} sub={item.range_metrics.passed ? "Passed historical coverage/sample checks" : "Coverage or sample checks incomplete"} />
          </div>
          <Card title="Why a forecast may be withheld">
            {[...new Set([...metric.reasons, ...item.range_metrics.reasons])].map(reason => <p className="muted" key={reason}>{reason}</p>)}
            <p className="muted">Provider-adjusted execution prices, corporate-action gaps and the lack of prospective validation still prevent production promotion, even when an individual historical check passes.</p>
          </Card>
          <Card title="Predicted versus observed frequencies" subtitle="Later-period measurements; ideally points follow the dashed diagonal">
            <div style={{ height: 280 }}><ResponsiveContainer width="100%" height="100%"><LineChart data={chart}>
              <CartesianGrid stroke="#26304d" /><XAxis dataKey="predicted" type="number" domain={[0, 100]} stroke="#8b93a7" unit="%" /><YAxis domain={[0, 100]} stroke="#8b93a7" unit="%" />
              <Tooltip formatter={value => `${Number(value).toFixed(1)}%`} /><Legend />
              <Line dataKey="ideal" name="Ideal calibration" stroke="#8b93a7" strokeDasharray="5 5" dot={false} />
              <Line dataKey="observed" name="Observed frequency" stroke="#4f8cff" connectNulls />
            </LineChart></ResponsiveContainer></div>
          </Card>
          {item.range_model && <Card title="Historical net-return bands by rank group" subtitle="Learned from older cases and expanded on a separate calibration period; these are not live stock forecasts">
            <div className="table-wrap"><table><thead><tr><th>Rank group</th><th>Lower bound</th><th>Median</th><th>Upper bound</th></tr></thead><tbody>
              {Object.entries(item.range_model.bands).map(([bucket, band]) => <tr key={bucket}><td>{Number(bucket) * 5 + 1}–{Number(bucket) * 5 + 5}</td><td>{pct(Math.max(-1, band.low - item.range_model!.expansion))}</td><td>{pct(band.median)}</td><td>{pct(band.high + item.range_model!.expansion)}</td></tr>)}
            </tbody></table></div>
          </Card>}
          <Card title="Corporate-action terms versus valuation" subtitle="An entitlement ratio does not provide a tradable price while the resulting share is unlisted">
            {r.entitlements.map(event => <details key={event.id} style={{ padding: "10px 0" }}><summary>{event.symbols.join(" / ")} · terms {event.terms_verified ? "verified" : "pending"} · valuation pending</summary>
              <p><a className="link" href={event.terms_source} target="_blank" rel="noreferrer">Official entitlement source ↗</a> · Ex-date {event.ex_date ?? "unverified"}</p>
              {event.entitlements.map(child => <p className="muted" key={child.asset_id}>{child.name}: {child.ratio} share per original share · Listing date {child.listing_date ?? "not verified"}</p>)}
            </details>)}
            <p className="muted">Same-date raw quotes and an exact-window cash/share-basis reconciliation are required. A later listing price is never used to manufacture an earlier exit value.</p>
          </Card>
          <details><summary>Full method limits</summary>{r.limits.map(limit => <p className="muted" key={limit}>{limit}</p>)}</details>
        </>;
      }}</QueryView>
      <Card title="Check a live forecast card" subtitle="Unsupported estimates return insufficient evidence rather than a number">
        <form onSubmit={e => { e.preventDefault(); preview.mutate(); }} style={{ display: "flex", gap: 12, alignItems: "center" }}><input aria-label="Stock symbol" value={symbol} onChange={e => { setSymbol(e.target.value); preview.reset(); }} placeholder="RELIANCE" /><button className="btn" disabled={preview.isPending || !symbol.trim()}>{preview.isPending ? "Checking…" : "Check forecast"}</button></form>
        {preview.error && <ErrorState error={preview.error} />}
        {preview.data && <div style={{ marginTop: 12 }}><strong>{preview.data.symbol} · {preview.data.status.replace(/_/g, " ")}</strong>
          {preview.data.profit_probability !== null && <p>Probability of scenario profit: {pct(preview.data.profit_probability)}</p>}
          {preview.data.net_return_band && <p>Net-return band: {pct(preview.data.net_return_band.low)} to {pct(preview.data.net_return_band.high)} · Median {pct(preview.data.net_return_band.median)}</p>}
          {preview.data.exit_value_band && <p>Illustrative exit-value equivalent: ₹{fmtNum(preview.data.exit_value_band.low)} to ₹{fmtNum(preview.data.exit_value_band.high)}</p>}
          {preview.data.reasons.map(reason => <p className="muted" key={reason}>{reason}</p>)}
        </div>}
      </Card>
    </div>
  </>;
}
