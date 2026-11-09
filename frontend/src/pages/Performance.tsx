import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, QueryView, StatCard, Updated } from "../components/ui";
import { useApi } from "../hooks";
import { fmtDateTime, fmtPct } from "../format";

type Pick = { symbol: string; rank: number; score: number | null; gross_return: number | null; net_return: number | null; beat_universe: boolean | null };
type Cohort = {
  id: string; published_at: string; horizon: number; pick_count: number;
  status: "pending" | "complete" | "incomplete"; reason: string | null;
  entry_date: string | null; exit_date: string | null; elapsed_sessions: number;
  net_return: number | null; universe_return: number | null; index_return: number | null;
  excess_return: number | null; hit_rate: number | null; universe_coverage: number;
  model_fingerprint: string; picks: Pick[];
};
type Month = { month: string; cohorts: number; net_return: number; universe_return: number; index_return: number; win_rate: number };
type Summary = {
  total: number; complete: number; pending: number; incomplete: number;
  mean_net_return: number | null; mean_universe_return: number | null; mean_index_return: number | null;
  mean_hit_rate: number | null; cohort_win_rate: number | null;
  worst_net_return: number | null; worst_pick_return: number | null;
  nonoverlapping_count: number; nonoverlapping_mean_excess: number | null; months: Month[];
};
type Report = { updated_at: string | null; summaries: Record<string, Summary>; cohorts: Cohort[] };
const percent = (value: number | null) => value === null ? "—" : fmtPct(value * 100);

export default function Performance() {
  const report = useApi<Report>(["forward-performance"], "/model/forward");
  const [horizon, setHorizon] = useState("21");
  return <>
    <div className="page-head">
      <div><h1 className="page-title">Track Record</h1><p className="page-sub">Published top-20 picks · forward paper results · long only</p></div>
      <Updated at={report.dataUpdatedAt} />
    </div>
    <div className="seg" style={{ marginBottom: 16 }}>
      {["5", "21"].map(h => <button key={h} className={horizon === h ? "active" : ""} onClick={() => setHorizon(h)}>{h} trading days</button>)}
    </div>
    <QueryView query={report}>{data => {
      const summary = data.summaries[horizon];
      const cohorts = data.cohorts.filter(c => c.horizon === Number(horizon));
      const chart = summary.months.map(m => ({ month: m.month, picks: m.net_return * 100, universe: m.universe_return * 100, index: m.index_return * 100 }));
      return <div className="grid" style={{ gap: 16 }}>
        <Card title="What is being measured?" subtitle={data.updated_at ? `Results calculated ${fmtDateTime(data.updated_at)}` : "Tracking starts with your next successful pipeline run"}>
          <p className="muted" style={{ margin: 0, lineHeight: 1.7 }}>
            Each served list is saved with its original scores and model identity. We simulate buying equal amounts at the
            first completed trading-day close after publication and selling {horizon} sessions later.
            Each cohort pays its recorded cost assumption (currently 10 bps on entry and exit).
            Benchmarks are the frozen publication universe and NIFTY 50, before costs.
            A pick “hits” when its gross return beats the universe average. Excess compares net picks with that gross average.
          </p>
          <p className="muted" style={{ marginBottom: 0, lineHeight: 1.7 }}>
            These are paper results, not executed trades. Today's bars are excluded. Missing prices keep a cohort incomplete;
            settled outcomes are retained within a methodology version. Corporate-action policy changes archive the previous report and re-evaluate affected evidence. Lists may overlap, so averages and monthly bars are not a compounded portfolio or independent trials.
            “Worst cohort” measures a holding-period loss, not peak-to-trough drawdown.
          </p>
        </Card>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
          <StatCard label="Completed cohorts" value={`${summary.complete} / ${summary.total}`} sub={`${summary.pending} pending · ${summary.incomplete} incomplete`} />
          <StatCard label="Average net return" value={percent(summary.mean_net_return)} sub={`Universe ${percent(summary.mean_universe_return)} · NIFTY ${percent(summary.mean_index_return)}`} />
          <StatCard label="Picks beating universe" value={percent(summary.mean_hit_rate)} sub="Average share within completed lists" />
          <StatCard label="Lists beating universe" value={percent(summary.cohort_win_rate)} sub="After costs on picks" />
          <StatCard label="Worst cohort" value={percent(summary.worst_net_return)} sub={`Worst individual pick (gross): ${percent(summary.worst_pick_return)}`} />
          <StatCard label="Non-overlapping cohorts" value={summary.nonoverlapping_count} sub={`Mean excess: ${percent(summary.nonoverlapping_mean_excess)}`} />
        </div>
        {summary.complete === 0 && <Card title="No matured results yet"><p className="muted">Run the pipeline to save picks and refresh prices. Results appear once entry plus {horizon} subsequent completed sessions are available. Historical backtests remain on the <Link className="link" to="/model">Model page</Link>.</p></Card>}
        {chart.length > 0 && <Card title="Consistency by entry month" subtitle="Average cohort returns (%) · overlapping lists are not compounded">
          <div style={{ height: 270 }}><ResponsiveContainer width="100%" height="100%"><BarChart data={chart}>
            <CartesianGrid stroke="#26304d" vertical={false} /><XAxis dataKey="month" stroke="#8b93a7" /><YAxis stroke="#8b93a7" />
            <Tooltip formatter={value => `${Number(value).toFixed(2)}%`} /><Legend />
            <Bar dataKey="picks" name="Top 20 after costs" fill="#4f8cff" /><Bar dataKey="universe" name="Universe (gross)" fill="#34d399" /><Bar dataKey="index" name="NIFTY 50 (gross)" fill="#8b93a7" />
          </BarChart></ResponsiveContainer></div>
          <div className="table-wrap"><table><thead><tr><th>Entry month</th><th>Cohorts</th><th>Lists beating universe</th></tr></thead><tbody>
            {summary.months.map(m => <tr key={m.month}><td>{m.month}</td><td>{m.cohorts}</td><td>{percent(m.win_rate)}</td></tr>)}
          </tbody></table></div>
        </Card>}
        <Card title="Published lists" subtitle="Open a row to inspect the original picks and available outcomes">
          {cohorts.length === 0 ? <p className="muted">No published lists recorded yet.</p> : cohorts.map(cohort => <details key={cohort.id} style={{ borderBottom: "1px solid #26304d", padding: "12px 0" }}>
            <summary style={{ cursor: "pointer" }}>{fmtDateTime(cohort.published_at)} · {cohort.pick_count} picks · {cohort.status} · net {percent(cohort.net_return)}</summary>
            <p className="muted">Entry {cohort.entry_date ?? "pending"} · Exit {cohort.exit_date ?? "pending"} · {cohort.elapsed_sessions}/{cohort.horizon} sessions · Model {cohort.model_fingerprint.slice(0, 12)}</p>
            {cohort.reason && <p className="muted">{cohort.reason}</p>}
            {cohort.status === "complete" && <p>Universe {percent(cohort.universe_return)} · NIFTY 50 {percent(cohort.index_return)} · Excess {percent(cohort.excess_return)} · Hit rate {percent(cohort.hit_rate)}</p>}
            {cohort.picks.length > 0 && <div className="table-wrap"><table><thead><tr><th>Rank</th><th>Stock</th><th>Gross return</th><th>Net return</th><th>Beat universe?</th></tr></thead><tbody>
              {cohort.picks.map(pick => <tr key={pick.symbol}><td>{pick.rank}</td><td><Link className="link" to={`/stocks/${encodeURIComponent(pick.symbol)}`}>{pick.symbol}</Link></td><td>{percent(pick.gross_return)}</td><td>{percent(pick.net_return)}</td><td>{pick.beat_universe === null ? "—" : pick.beat_universe ? "Yes" : "No"}</td></tr>)}
            </tbody></table></div>}
          </details>)}
        </Card>
        <p className="muted">Adjusted Yahoo daily prices are an approximation: corporate-action revisions, missing sessions and execution costs can affect results. This page tracks the deployed model versions; it is not evidence for one unchanged model.</p>
      </div>;
    }}</QueryView>
  </>;
}
