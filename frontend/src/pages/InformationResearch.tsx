import { useApi } from "../hooks";
import { Card, QueryView } from "../components/ui";

type Metrics = { status: string; net_return: number | null };
type Report = {
  status: string; evidence_current: boolean; stale_reasons: string[]; selected_on_validation?: string | null;
  results: Record<string, Record<string, { retail: Record<string, Metrics>; matched_momentum_retail: Record<string, Metrics> }>>;
  filing_coverage?: { accepted: number; rejected: number; symbols: number };
  paper: { status?: string; captured_cohorts?: number; matured_cohorts?: number; paired_pool_size?: number; feature_coverage?: Record<string, number> };
  announcements?: { status: string; reason: string }; limits?: string[];
};
const labels: Record<string, string> = { technical_control: "Technical control", sector_candidate: "Sector context", earnings_candidate: "Sector context + verified earnings" };
const pct = (m?: Metrics) => !m || m.status !== "complete" || m.net_return === null ? "Incomplete / unavailable" : `${(m.net_return * 100).toFixed(2)}%`;

export default function InformationResearch() {
  const query = useApi<Report>(["information-research"], "/research/information");
  return <Card title="Information research and fresh paper trial" subtitle="Timestamped filings and sector context; announcement collection is still building history">
    <QueryView query={query}>{r => <>
      <p>Research status: <strong>{r.status.replace(/_/g, " ")}</strong>. Production rankings are unchanged.</p>
      {r.stale_reasons.map(reason => <p className="muted" key={reason}>{reason}</p>)}
      {r.evidence_current && <>
        <p>Earlier validation choice: {r.selected_on_validation ? labels[r.selected_on_validation] : "none qualified"}. Historical results below are from already examined periods.</p>
        <div className="table-wrap"><table><thead><tr><th>Information used</th><th>Validation net</th><th>Later net</th><th>Matched momentum</th></tr></thead><tbody>
          {Object.entries(r.results).map(([name, periods]) => <tr key={name}><td>{labels[name] ?? name}</td><td>{pct(periods.validation?.retail.base)}</td><td>{pct(periods.assessment?.retail.base)}</td><td>{pct(periods.assessment?.matched_momentum_retail.base)}</td></tr>)}
        </tbody></table></div>
        {r.filing_coverage && <p className="muted">Verified archive: {r.filing_coverage.accepted} filings across {r.filing_coverage.symbols} symbols; {r.filing_coverage.rejected} rejected versions. Coverage is partial.</p>}
        <p>Fresh paper trial: {r.paper.captured_cohorts ?? 0} recorded cohorts, {r.paper.matured_cohorts ?? 0} matured. Paired stock pool: {r.paper.paired_pool_size ?? "pending"}.</p>
        {r.paper.feature_coverage && <p className="muted">Latest information coverage: {r.paper.feature_coverage.info_margin ?? 0} stocks with usable filing margins; {r.paper.feature_coverage.info_revenue_qoq ?? 0} with verified quarterly revenue changes. Missing information is not treated as zero growth.</p>}
        <p className="muted">{r.announcements?.reason}. Announcement features are excluded from model claims for now. Paper results must mature before any performance conclusion.</p>
      </>}
      {!!r.limits?.length && <details><summary>Data and experiment limits</summary>{r.limits.map(limit => <p className="muted" key={limit}>{limit}</p>)}</details>}
    </>}</QueryView>
  </Card>;
}
