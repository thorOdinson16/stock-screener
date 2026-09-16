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
                        <XAxis dataKey="name" stroke="#8b93a7" tick={{ fontSize: 11 }} />
                        <YAxis stroke="#8b93a7" tick={{ fontSize: 11 }} width={54} />
                        <Tooltip
                          cursor={{ fill: "rgba(255,255,255,0.04)" }}
                          contentStyle={{ background: "#141a2e", border: "1px solid #26304d", borderRadius: 8 }}
                          formatter={(v) => [Number(v).toFixed(4), "IC mean"]}
                        />
                        <Bar dataKey="ic" radius={[4, 4, 0, 0]}>
                          {chartData.map((c) => (
                            <Cell key={c.name} fill={c.baseline ? "#8b93a7" : "#4f8cff"} />
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
