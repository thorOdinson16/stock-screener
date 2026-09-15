import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi } from "../hooks";
import type { Overview, StockRow } from "../types";
import { changeClass, fmtCompact, fmtNum, fmtPct } from "../format";
import { Card, QueryView, StatCard, Updated } from "../components/ui";
import PipelineControl from "../components/PipelineControl";

function MiniTable({ rows, title }: { rows: StockRow[]; title: string }) {
  return (
    <Card title={title}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">Close</th>
              <th className="num">Change</th>
              <th className="num">Mkt cap</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className="clickable">
                <td>
                  <Link to={`/stocks/${r.symbol}`} className="link">
                    {r.symbol.replace(".NS", "")}
                  </Link>
                  <div className="tag">{r.industry}</div>
                </td>
                <td className="num mono">{fmtNum(r.close)}</td>
                <td className={`num mono ${changeClass(r.change_percent)}`}>
                  {fmtPct(r.change_percent)}
                </td>
                <td className="num mono">{fmtCompact(r.market_cap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function Dashboard() {
  const overview = useApi<Overview>(["overview"], "/market/overview");

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Market Dashboard</h1>
          <p className="page-sub">Latest scored snapshot across the NIFTY 500 universe</p>
        </div>
        <div className="head-right">
          <PipelineControl />
          <Updated at={overview.dataUpdatedAt} />
        </div>
      </div>

      <QueryView query={overview}>
        {(d) => {
          const total = d.advancers + d.decliners + d.unchanged || 1;
          const sectorData = d.sectors.slice(0, 12).map((s) => ({
            industry: s.industry,
            avg: s.avg_change_percent,
          }));
          return (
            <div className="grid" style={{ gap: 16 }}>
              <div className="grid cols-4">
                <StatCard label="Universe" value={fmtNum(d.universe_size, 0)} sub="symbols scored" />
                <StatCard label="Advancers" value={fmtNum(d.advancers, 0)} tone="pos" sub={`${((d.advancers / total) * 100).toFixed(0)}% of universe`} />
                <StatCard label="Decliners" value={fmtNum(d.decliners, 0)} tone="neg" sub={`${((d.decliners / total) * 100).toFixed(0)}% of universe`} />
                <StatCard label="Avg change" value={fmtPct(d.avg_change_percent)} tone={changeClass(d.avg_change_percent)} sub="daily, equal-weight" />
              </div>

              <Card title="Market breadth" subtitle="Advancers vs decliners">
                <div className="breadth">
                  <div className="up" style={{ width: `${(d.advancers / total) * 100}%` }} />
                  <div className="flat" style={{ width: `${(d.unchanged / total) * 100}%` }} />
                  <div className="down" style={{ width: `${(d.decliners / total) * 100}%` }} />
                </div>
                <div className="row-between" style={{ marginTop: 10 }}>
                  <span className="pos">{d.advancers} advancing</span>
                  <span className="muted">{d.unchanged} unchanged</span>
                  <span className="neg">{d.decliners} declining</span>
                </div>
              </Card>

              <div className="grid cols-2">
                <MiniTable rows={d.gainers} title="Top gainers" />
                <MiniTable rows={d.losers} title="Top losers" />
              </div>

              <Card title="Sector performance" subtitle="Average daily change by industry (top 12)">
                <div style={{ height: 340 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={sectorData} layout="vertical" margin={{ left: 40, right: 20 }}>
                      <XAxis type="number" stroke="#8b93a7" tickFormatter={(v) => `${v}%`} />
                      <YAxis type="category" dataKey="industry" width={170} stroke="#8b93a7" tick={{ fontSize: 11 }} />
                      <Tooltip
                        cursor={{ fill: "rgba(255,255,255,0.04)" }}
                        contentStyle={{ background: "#141a2e", border: "1px solid #26304d", borderRadius: 8 }}
                        formatter={(v) => [`${Number(v).toFixed(2)}%`, "Avg change"]}
                      />
                      <Bar dataKey="avg" radius={[0, 4, 4, 0]}>
                        {sectorData.map((s) => (
                          <Cell key={s.industry} fill={s.avg >= 0 ? "#22c55e" : "#ef4444"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Card>
            </div>
          );
        }}
      </QueryView>
    </>
  );
}
