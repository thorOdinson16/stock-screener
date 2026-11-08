import { useApi } from "../hooks";
import { Card, QueryView } from "../components/ui";

type Metric = { status: string; net_return: number | null; max_drawdown: number | null };
type Period = { retail: Record<string, Metric>; matched_momentum_retail: Record<string, Metric> };
type Calibration = { brier?: number; baseline_brier?: number; calibration_error?: number; passed: boolean; reasons: string[] };
type Report = { status: string; evidence_current: boolean; stale_reasons: string[]; selected_on_validation: string | null;
  steps: { name: string; status: string }[]; results: Record<string, Record<string, Period>>;
  financial_sources?: { verified_filings: number; verified_symbols: number; rejected_filings: number; features_with_observations: Record<string, number>; source_examples: { symbol: string; source: string; published_at: string }[] };
  verified_financial_experiment?: { status: string; covered_dates: number; first_date?: string; last_date?: string };
  verified_financial_later_only?: Record<string, Period>;
  probability_calibration: Record<string, { status: string; scopes?: { top20: { raw: Calibration; calibrated: Calibration; missing_matured_fraction: number } } }>;
  robustness: Record<string, { candidate: { status: string; market_beta: number | null; mean_cash_exposure: number; positive_pnl_top_stock_share: number | null } }>;
  robustness_checks?: Record<string, { passed: boolean; reasons: string[] }>;
  paper_tracking?: { status: string; captured_cohorts?: number; completed_cohorts?: number; last_source_session?: string; controls?: string[] };
  paper_cohorts?: { publication_date: string; signal_date: string; shared_pool: number; candidate: string; strategies: Record<string, { status: string; net_return: number | null; mark_to_market_return: number | null }> }[];
  prospective_assessment?: { status: string; matured_cohorts: number; scheduled_independent_cohorts: number; comparisons: Record<string, { completed_independent_pairs: number; mean_net_return_difference: number | null; supported: boolean; reasons: string[] }> } | null;
  ensemble?: { status: string }; limits: string[] };
const pct = (v: number | null | undefined) => v == null ? "—" : `${(100 * v).toFixed(1)}%`;

export default function CompletionResearch() {
  const query = useApi<Report>(["research-completion"], "/research/completion");
  return <QueryView query={query}>{r => <Card title="Full research plan: evidence and remaining market time" subtitle={r.status.replace(/_/g, " ")}>
    {r.status === "running" && <p className="muted">Context, verified-data and probability experiments are running. No production model changes.</p>}
    {r.status === "not_available" && <p className="muted">The research extension has not been run.</p>}
    {r.stale_reasons.map(reason => <p className="muted" key={reason}>{reason}</p>)}
    {r.steps.length > 0 && <>
      <div className="table-wrap"><table><thead><tr><th>Plan component</th><th>Status</th></tr></thead><tbody>{r.steps.map(s => <tr key={s.name}><td>{s.name}</td><td>{s.status.replace(/_/g, " ")}</td></tr>)}</tbody></table></div>
      <p className="muted">Validation-only research choice: {r.selected_on_validation ?? "none qualified"}. An earlier winner can fail later checks. Neither a historical lead nor a calibrated classifier automatically enables live confidence or production promotion.</p>
      {r.paper_tracking && <p>Paired paper tracking: <strong>{r.paper_tracking.status.replace(/_/g, " ")}</strong> · {r.paper_tracking.captured_cohorts ?? 0} captured cohorts · {r.paper_tracking.completed_cohorts ?? 0} matured · Source {r.paper_tracking.last_source_session ?? "unavailable"}. Current-model, momentum and frozen TCN controls use the same available pool. Cohorts are not compounded.</p>}
      {!!r.paper_cohorts?.length && <details><summary>Prospective ten-position cohort books</summary>
        <p className="muted">Publication dates are recorded before entry. Open marks are valuations of unfinished paper positions, not completed returns or predictions. Each cohort starts its own paper capital and is evaluated separately.</p>
        <div className="table-wrap"><table><thead><tr><th>Published</th><th>Strategy</th><th>Status</th><th>Completed net return</th><th>Open mark</th></tr></thead><tbody>
          {r.paper_cohorts.flatMap(c => Object.entries(c.strategies).map(([name, m]) => <tr key={`${c.publication_date}-${c.candidate}-${name}`}><td title={`Signal ${c.signal_date}; shared pool ${c.shared_pool}`}>{c.publication_date}</td><td>{name.replace(/_/g, " ")}</td><td>{m.status.replace(/_/g, " ")}</td><td>{pct(m.net_return)}</td><td>{m.status === "open" ? pct(m.mark_to_market_return) : "—"}</td></tr>))}
        </tbody></table></div>
      </details>}
      {r.prospective_assessment && <details><summary>Fresh-evidence comparison policy</summary>
        <p className="muted">{r.prospective_assessment.status.replace(/_/g, " ")} · {r.prospective_assessment.matured_cohorts} matured cohorts · {r.prospective_assessment.scheduled_independent_cohorts} prescheduled non-overlapping cohorts. Eight complete independent pairs, adequate coverage and a positive interval against each fixed control are required. The first completed 21-session result alone cannot establish an edge.</p>
        {Object.entries(r.prospective_assessment.comparisons).map(([name, c]) => <p className="muted" key={name}>Versus {name.replace(/_/g, " ")}: {c.completed_independent_pairs} independent pairs; mean net difference {pct(c.mean_net_return_difference)}. {c.supported ? "Paper comparison supported; production remains unchanged." : c.reasons.join("; ")}</p>)}
      </details>}
      <details open><summary>Later retail results and robustness</summary>
        <div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Status</th><th>Net return</th><th>Matched momentum</th><th>Drawdown</th><th>Market beta</th><th>Mean cash</th><th>Top stock share of positive PnL</th><th>Advancement check</th></tr></thead><tbody>
          {Object.entries(r.results).map(([name, periods]) => { const a = periods.assessment; const m = r.robustness[name]?.candidate; const check = r.robustness_checks?.[name]; return <tr key={name}><td>{name.replace(/_/g, " ")}</td><td>{a.retail.base.status}</td><td>{pct(a.retail.base.net_return)}</td><td>{pct(a.matched_momentum_retail.base.net_return)}</td><td>{pct(a.retail.base.max_drawdown)}</td><td>{m?.market_beta?.toFixed(2) ?? "—"}</td><td>{pct(m?.mean_cash_exposure)}</td><td>{pct(m?.positive_pnl_top_stock_share)}</td><td title={check?.reasons.join("; ")}>{check ? check.passed ? "Historical checks passed" : "Not established" : "Audit pending"}</td></tr>; })}
        </tbody></table></div>
        <p className="muted">Beta and contribution statistics are descriptive. Incomplete portfolios expose only their valued prefix; their statistics do not establish full-period performance. Detailed artifacts include quarters, market/volatility regimes, industry contributions and a validation-chosen top-stock removal test.</p>
      </details>
      <details><summary>Profit and outperformance probability calibration</summary>
        <p className="muted">Separate classifiers predict positive net return or beating a fully valued eligible-universe benchmark. Sigmoid calibration uses earlier out-of-sample outcomes that mature before the later assessment. These are aggregate historical diagnostics; live probabilities remain withheld.</p>
        <div className="table-wrap"><table><thead><tr><th>Classifier</th><th>Raw Brier</th><th>Calibrated Brier</th><th>Constant baseline</th><th>Calibration error</th><th>Missing targets</th><th>Check</th></tr></thead><tbody>
          {Object.entries(r.probability_calibration).map(([name, c]) => { const s = c.scopes?.top20; return <tr key={name}><td>{name.replace(/_/g, " ")}</td><td>{s?.raw.brier?.toFixed(4) ?? "—"}</td><td>{s?.calibrated.brier?.toFixed(4) ?? "—"}</td><td>{s?.calibrated.baseline_brier?.toFixed(4) ?? "—"}</td><td>{pct(s?.calibrated.calibration_error)}</td><td>{pct(s?.missing_matured_fraction)}</td><td title={s?.calibrated.reasons.join("; ")}>{s?.calibrated.passed ? "Passed historical checks" : "Insufficient evidence"}</td></tr>; })}
        </tbody></table></div>
      </details>
      {r.financial_sources && <details><summary>Verified financial inputs and their matched experiment</summary>
        <p>{r.financial_sources.verified_filings} original filings verified across {r.financial_sources.verified_symbols} companies; {r.financial_sources.rejected_filings} documents rejected. Availability follows sourced exchange publication times, with a conservative next-day rule.</p>
        <p className="muted">Verified experiment: {r.verified_financial_experiment?.status.replace(/_/g, " ")} · {r.verified_financial_experiment?.covered_dates} covered dates. This is a shorter matched subset experiment, not verified coverage of the entire original history. Missing comparison-year facts stay missing.</p>
        <p className="muted">Observed financial facts: {Object.entries(r.financial_sources.features_with_observations).map(([name, count]) => `${name.replace(/_/g, " ")}: ${count}`).join(" · ")}. A zero count means that feature supplied no verified information in this run.</p>
        <div className="table-wrap"><table><thead><tr><th>Later verified-subset arm</th><th>Status</th><th>Net return</th><th>Matched momentum</th></tr></thead><tbody>{Object.entries(r.verified_financial_later_only ?? {}).map(([name, p]) => <tr key={name}><td>{name.replace(/_/g, " ")}</td><td>{p.retail.base.status}</td><td>{pct(p.retail.base.net_return)}</td><td>{pct(p.matched_momentum_retail.base.net_return)}</td></tr>)}</tbody></table></div>
        {r.financial_sources.source_examples.map(s => <p key={s.source}><a className="link" href={s.source} target="_blank" rel="noreferrer">{s.symbol}: original NSE filing ↗</a> · Published {s.published_at}</p>)}
      </details>}
      {r.ensemble && <p className="muted">Ensemble gate: {r.ensemble.status.replace(/_/g, " ")}. Weak individual candidates are not automatically blended.</p>}
      <details><summary>Research limits</summary>{r.limits.map(limit => <p className="muted" key={limit}>{limit}</p>)}</details>
    </>}
  </Card>}</QueryView>;
}
