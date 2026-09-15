import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../hooks";
import type { ComparisonResponse } from "../types";
import { fmtNum } from "../format";
import { Card, QueryView, Updated } from "../components/ui";

const METRICS: { key: string; label: string }[] = [
  { key: "ic_mean", label: "IC mean" },
  { key: "ic_ir", label: "IC IR" },
  { key: "precision_at_k_vs_universe", label: "P@K univ" },
  { key: "precision_at_k_vs_index", label: "P@K index" },
  { key: "rmse", label: "RMSE" },
  { key: "top_k_mean_forward_return", label: "TopK fwd ret" },
  { key: "top_k_turnover", label: "Turnover" },
];

export default function Model() {
  const comparison = useApi<ComparisonResponse>(["model"], "/model/comparison");

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Model Evaluation</h1>
          <p className="page-sub">
            Held-out test period · metrics per label · Phase A (technical features only)
          </p>
        </div>
        <Updated at={comparison.dataUpdatedAt} />
      </div>

      <QueryView query={comparison}>
        {(d) => (
          <div className="grid" style={{ gap: 16 }}>
            <Card title="How to read this" subtitle="Honest, out-of-sample evaluation">
              <p className="muted" style={{ lineHeight: 1.6, margin: 0 }}>
                Scores predict forward returns over 5 or 21 trading days. <strong>IC</strong> is the
                per-date rank correlation between predicted score and realised return (higher is
                better); <strong>P@K</strong> is the share of the top-K picks beating the universe
                mean or the NIFTY index; <strong>turnover</strong> is how much the top-K list changes
                day to day. Markets are noisy, so IC values around 0.02–0.05 are expected — the
                rule-based baseline is shown for comparison and is not used for production scoring.
                Evaluated {new Date(d.evaluated_at).toLocaleString()} at K={d.k}.
              </p>
            </Card>

            {Object.entries(d.labels).map(([label, models]) => {
              const chartData = Object.entries(models).map(([name, m]) => ({
                name,
                ic: m.ic_mean ?? 0,
                baseline: name === "rule_baseline",
              }));
              return (
                <Card key={label} title={`${label === "fwd_ret_5d" ? "5-day" : "21-day"} horizon`} subtitle={label}>
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
                        {Object.entries(models).map(([name, m]) => (
                          <tr key={name}>
                            <td>
                              {name}
                              {name === "rule_baseline" && <span className="tag"> (baseline)</span>}
                            </td>
                            {METRICS.map((metric) => (
                              <td key={metric.key} className="num mono">
                                {fmtNum(m[metric.key] as number, 4)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </QueryView>
    </>
  );
}
