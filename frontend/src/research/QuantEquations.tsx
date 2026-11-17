import { useApi } from "../hooks";
import { Card, QueryView } from "../components/ui";

type Metric = { status: string; net_return: number | null; max_drawdown: number | null };
type Period = { retail: Record<string, Metric>; matched_momentum_retail: Record<string, Metric> };
type Report = { status: string; evidence_current: boolean; stale_reasons: string[]; selected_on_validation?: string | null;
  protocol?: { formulas: Record<string, string> }; coverage?: { signal_dates: number; symbols: number; retained_fraction: number };
  results: Record<string, Record<string, Period>>; assessment_checks: Record<string, { supported: boolean; reasons: string[]; independent_pairs: number; familywise_95pct_interval: [number, number] | null }>;
  limits?: string[] };
const pct = (v: number | null | undefined) => v == null ? "—" : `${(100 * v).toFixed(1)}%`;

export default function QuantEquations() {
  const query = useApi<Report>(["quant-equations"], "/research/equations");
  return <QueryView query={query}>{r => <Card title="Fixed quant equations" subtitle={`21-session mathematical signals · ${r.status.replace(/_/g, " ")}`}>
    <p className="muted">Six fixed formulas test trend, volatility adjustment, market residuals, reversal, breakouts and a rank blend. No formula, weight or lookback search is fitted to the later period. These are rankings, not profit probabilities or price targets.</p>
    {r.stale_reasons.map(reason => <p className="muted" key={reason}>{reason}</p>)}
    {r.status === "running" && <p className="muted">The fixed comparison is running.</p>}
    {r.status === "not_available" && <p className="muted">No completed equation report yet.</p>}
    {Object.keys(r.results).length > 0 && <>
      <p>Validation-only choice: <strong>{r.selected_on_validation ?? "none qualified"}</strong>. Production and the existing paper trial remain unchanged.</p>
      {r.coverage && <p className="muted">Shared pool: {r.coverage.symbols} symbols across {r.coverage.signal_dates} signal dates; {pct(r.coverage.retained_fraction)} of original reference rows retained. Every strategy and its momentum control share these past-data requirements.</p>}
      <div className="table-wrap"><table><thead><tr><th>Equation</th><th>Earlier net</th><th>Later status</th><th>Later net</th><th>Matched momentum</th><th>Later drawdown</th><th>Doubled costs</th><th>Delayed entry</th><th>Adjusted evidence check</th></tr></thead><tbody>
        {Object.entries(r.results).map(([name, periods]) => { const a = periods.assessment; const check = r.assessment_checks[name]; return <tr key={name}><td>{name.replace(/_/g, " ")}</td><td>{pct(periods.validation.retail.base.net_return)}</td><td>{a.retail.base.status}</td><td>{pct(a.retail.base.net_return)}</td><td>{pct(a.matched_momentum_retail.base.net_return)}</td><td>{pct(a.retail.base.max_drawdown)}</td><td>{pct(a.retail.double_costs.net_return)}</td><td>{pct(a.retail.entry_delayed.net_return)}</td><td title={check?.reasons.join("; ")}>{check ? check.supported ? "Passed historical checks" : "Not established" : "Reference"}</td></tr>; })}
      </tbody></table></div>
      <p className="muted">All portfolios use the same ten-position planner, whole shares, costs and stop rules. Independent-cycle comparisons adjust for testing six formulas. This already explored history remains research evidence, not a fresh holdout.</p>
      <details><summary>Exact formulas and caveats</summary>
        {Object.entries(r.protocol?.formulas ?? {}).map(([name, equation]) => <p key={name}><strong>{name.replace(/_/g, " ")}</strong>: <code>{equation}</code></p>)}
        {r.limits?.map(limit => <p className="muted" key={limit}>{limit}</p>)}
      </details>
    </>}
  </Card>}</QueryView>;
}
