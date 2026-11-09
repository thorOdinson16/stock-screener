import { useApi } from "../hooks";
import { Card, QueryView } from "../components/ui";

type Gate = { ready: boolean; reasons: string[]; date_pass_fraction: number; quarter_cell_fraction: number; signal_dates: number; latest: { margin_fraction: number; qoq_fraction: number; yoy_fraction: number } | null };
type Pilot = { status: string; evidence_current: boolean; ready: boolean; stale_reasons?: string[]; readiness?: Gate; accepted_filings?: number; rejected_filings?: number; reconciled_quarters?: number; per_symbol: Record<string, { covered_quarters: number; qoq_date_fraction: number; yoy_date_fraction: number }> };
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export default function FinancialPilot() {
  const query = useApi<Pilot>(["financial-pilot"], "/research/financial-pilot");
  return <Card title="Financial data pilot" subtitle="Fixed liquid-stock sample; data quality must pass before another verified earnings experiment">
    <QueryView query={query}>{r => <>
      <p><strong>{r.status.replace(/_/g, " ")}</strong>. This measures data coverage, not prediction confidence.</p>
      {r.stale_reasons?.map(reason => <p className="muted" key={reason}>{reason}</p>)}
      {r.evidence_current && r.readiness && <>
        <p>{r.accepted_filings} accepted filings; {r.rejected_filings} rejected. {pct(r.readiness.date_pass_fraction)} of {r.readiness.signal_dates} signal dates pass the coverage rules. Required stock-quarter coverage: {pct(r.readiness.quarter_cell_fraction)}.</p>
        {!!r.reconciled_quarters && <p>{r.reconciled_quarters} missing quarters reconciled against original financial reports. Rejected XML files remain in the audit history.</p>}
        {r.readiness.reasons.map(reason => <p className="muted" key={reason}>{reason}</p>)}
        <details><summary>Coverage for each fixed stock</summary><div className="table-wrap"><table><thead><tr><th>Stock</th><th>Quarters</th><th>Dates with quarterly growth</th><th>Dates with yearly growth</th></tr></thead><tbody>
          {Object.entries(r.per_symbol).map(([symbol, row]) => <tr key={symbol}><td>{symbol}</td><td>{row.covered_quarters}</td><td>{pct(row.qoq_date_fraction)}</td><td>{pct(row.yoy_date_fraction)}</td></tr>)}
        </tbody></table></div></details>
        <p className="muted">No new model is trained by this audit. Existing paper models and trading rules remain frozen.</p>
      </>}
    </>}</QueryView>
  </Card>;
}
