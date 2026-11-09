import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../hooks";
import type { ComparisonResponse, ModelMetric, WalkForwardMetric } from "../types";
import { fmtNum } from "../format";
import { Card, QueryView, Updated } from "../components/ui";

type MetricColumn = {
  key: string;
  label: string;
  value?: (m: ModelMetric) => unknown;
};

type RetailResearch = {
  status: string; selected_on_validation: string | null; production_changed: boolean;
  results: Record<string, Record<string, { status: string; net_return: number | null; max_drawdown: number | null; closed_trades: number; blocked: { reason: string; date: string; symbol: string } | null }>>;
  limitations: string[];
};

const METRICS: MetricColumn[] = [
  { key: "ic_mean", label: "IC mean" },
  { key: "ic_t_stat", label: "IC t" },
  { key: "sector_neutral_ic_mean", label: "IC sec-neut" },
  { key: "ic_ir", label: "IC IR" },
  { key: "precision_at_k_vs_universe", label: "P@K univ" },
  { key: "precision_at_k_vs_index", label: "P@K index" },
  { key: "top_k_turnover", label: "Turnover" },
  { key: "net_sharpe", label: "Net Sharpe", value: (m) => m.backtest?.net_sharpe },
];

export default function Model() {
  const comparison = useApi<ComparisonResponse>(["model"], "/model/comparison");
  const retail = useApi<RetailResearch>(["retail-research"], "/research/experiments");
  const quality = useApi<{ model_uses_current_quality_policy: boolean }>(["research-quality"], "/research/quality");
  const percent = (v: number | null) => v === null ? "—" : `${(v * 100).toFixed(2)}%`;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Model Evaluation</h1>
          <p className="page-sub">
            Held-out test period · excess-return labels · cost-aware long/short backtest
          </p>
        </div>
        <Updated at={comparison.dataUpdatedAt} />
      </div>

      {quality.data && !quality.data.model_uses_current_quality_policy && <Card title="Historical model needs a quality refresh">
        <p className="muted">The current model and its historical evaluation predate corporate-action-aware indicators and label checks. Refresh prices, then use Retrain model to rebuild them. The results below retain their original evaluation date.</p>
      </Card>}
      <div style={{ margin: "16px 0" }}><Card title="Retail portfolio experiments" subtitle="Earlier folds select a candidate; later folds assess it. Temporary models do not replace production.">
        <QueryView query={retail}>{r => r.status === "not_available" ? <p className="muted">No completed retail research report yet.</p> : <>
          <p className="muted">Status: {r.status} · Validation choice: {r.selected_on_validation ?? "none qualified"} · Production changed: {r.production_changed ? "yes" : "no"}</p>
          <div className="table-wrap"><table><thead><tr><th>Variant</th><th>Period</th><th>Status</th><th>Net return</th><th>Daily drawdown</th><th>Closed trades</th></tr></thead><tbody>
            {Object.entries(r.results).flatMap(([name, periods]) => Object.entries(periods).map(([period, result]) => <tr key={`${name}-${period}`}><td>{name}</td><td>{period}</td><td>{result.status}{result.blocked && <div className="tag">{result.blocked.date} · {result.blocked.symbol}: {result.blocked.reason}</div>}</td><td>{percent(result.net_return)}</td><td>{percent(result.max_drawdown)}</td><td>{result.closed_trades}</td></tr>))}
          </tbody></table></div>
          <details style={{ marginTop: 12 }}><summary>Method limits</summary>{r.limitations.map(v => <p key={v} className="muted">{v}</p>)}</details>
        </>}</QueryView>
      </Card></div>
      <QueryView query={comparison}>
        {(d) => (
          <div className="grid" style={{ gap: 16 }}>
            <Card title="How to read this" subtitle="Honest, out-of-sample evaluation">
              <p className="muted" style={{ lineHeight: 1.6, margin: 0 }}>
                Scores predict <strong>excess</strong> forward returns (5 or 21 trading days) over the
                equal-weight universe. <strong>IC</strong> is the per-date rank correlation between
                score and realised excess return, with a Newey–West <strong>t-stat</strong> that
                accounts for overlapping horizons; <strong>sector-neutral IC</strong> removes industry
                effects. <strong>P@K</strong> is the share of top-K picks beating the universe mean or
                the NIFTY index. <strong>Net Sharpe</strong> is the non-overlapping long/short backtest
                after {d.cost_bps ?? 10} bps/side. Evaluated{" "}
                {new Date(d.evaluated_at).toLocaleString()} at K={d.k}.
              </p>
            </Card>

            {Object.entries(d.labels).map(([label, modelMap]) => {
              const models = Object.entries(modelMap).filter(
                ([name]) => name !== "walk_forward"
              ) as [string, ModelMetric][];
              const wf = modelMap.walk_forward as WalkForwardMetric | undefined;
              const horizon = label.replace("excess_ret_", "").replace("d", "");
              const chartData = models.map(([name, m]) => ({
                name,
                ic: m.ic_mean ?? 0,
                baseline: name === "rule_baseline",
              }));
              return (
                <Card key={label} title={`${horizon}-day horizon`} subtitle={label}>
                  <div style={{ height: 220, marginBottom: 12 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={chartData} margin={{ left: 4, right: 16, top: 8 }}>
                        <XAxis dataKey="name" stroke="#b5b3a6" tick={{ fontSize: 11 }} />
                        <YAxis stroke="#b5b3a6" tick={{ fontSize: 11 }} width={54} />
                        <Tooltip
                          cursor={{ fill: "rgba(255,255,255,0.04)" }}
                          contentStyle={{ background: "#141412", border: "1px solid #383830", borderRadius: 8 }}
                          formatter={(v) => [Number(v).toFixed(4), "IC mean"]}
                        />
                        <Bar dataKey="ic" radius={[4, 4, 0, 0]}>
                          {chartData.map((c) => (
                            <Cell key={c.name} fill={c.baseline ? "#b5b3a6" : "#fff9dc"} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Model</th>
                          {METRICS.map((m) => (
                            <th key={m.key} className="num">{m.label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {models.map(([name, m]) => (
                          <tr key={name}>
                            <td>
                              {name}
                              {name === "rule_baseline" && <span className="tag"> (baseline)</span>}
                            </td>
                            {METRICS.map((metric) => (
                              <td key={metric.key} className="num mono">
                                {fmtNum(
                                  (metric.value ? metric.value(m) : m[metric.key]) as number,
                                  4
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {wf && !wf.error && (
                    <p className="muted" style={{ marginTop: 10, marginBottom: 0 }}>
                      Walk-forward (selected model): {wf.n_periods ?? 0} periods · IC{" "}
                      {fmtNum(wf.ic_mean as number, 4)} (t={fmtNum(wf.ic_t_stat as number, 2)}) · net
                      Sharpe {fmtNum(wf.net_sharpe as number, 4)}
                    </p>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </QueryView>
    </>
  );
}
