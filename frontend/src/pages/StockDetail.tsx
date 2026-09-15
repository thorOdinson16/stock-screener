import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi } from "../hooks";
import type { HistoryResponse, StockRow } from "../types";
import { changeClass, fmtCompact, fmtNum, fmtPct } from "../format";
import { Card, QueryView, Spinner } from "../components/ui";

const RANGES = ["3mo", "6mo", "1y", "2y"];

function FundItem({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="item">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

export default function StockDetail() {
  const { symbol = "" } = useParams();
  const [range, setRange] = useState("6mo");
  const stock = useApi<StockRow>(["stock", symbol], `/stocks/${symbol}`);
  const history = useApi<HistoryResponse>(["history", symbol, range], `/stocks/${symbol}/history?range=${range}`);

  const s = stock.data;
  const h = history.data?.history ?? [];
  const chartData = h.map((p) => ({ ...p, date: new Date(p.__time).toLocaleDateString() }));

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            {symbol.replace(".NS", "")}{" "}
            <span className="tag">{s?.company_name}</span>
          </h1>
          <p className="page-sub">
            <Link to="/screener" className="link">← Screener</Link>
            {s?.industry ? ` · ${s.industry}` : ""}
          </p>
        </div>
        {s && (
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 26, fontWeight: 700 }}>{fmtNum(s.close)}</div>
            <div className={changeClass(s.change_percent)}>{fmtPct(s.change_percent)}</div>
          </div>
        )}
      </div>

      <QueryView query={stock}>
        {(d) => (
          <div className="grid cols-3" style={{ marginBottom: 16 }}>
            <div className="stat">
              <div className="stat-label">21-day score / rank</div>
              <div className="stat-value">{fmtNum(d.score_21d, 4)}</div>
              <div className="stat-sub">rank #{fmtNum(d.rank_21d, 0)} of universe</div>
            </div>
            <div className="stat">
              <div className="stat-label">5-day score / rank</div>
              <div className="stat-value">{fmtNum(d.score_5d, 4)}</div>
              <div className="stat-sub">rank #{fmtNum(d.rank_5d, 0)} of universe</div>
            </div>
            <div className="stat">
              <div className="stat-label">RSI (14) / Vol ratio</div>
              <div className="stat-value">{fmtNum(d.rsi_14, 1)}</div>
              <div className="stat-sub">volume ratio {fmtNum(d.volume_ratio, 2)}</div>
            </div>
          </div>
        )}
      </QueryView>

      <Card
        title="Price & moving averages"
        actions={
          <div className="seg">
            {RANGES.map((r) => (
              <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>
                {r}
              </button>
            ))}
          </div>
        }
      >
        {history.isLoading ? (
          <Spinner />
        ) : (
          <div style={{ height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ left: 4, right: 16, top: 8 }}>
                <CartesianGrid stroke="#26304d" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" stroke="#8b93a7" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis stroke="#8b93a7" tick={{ fontSize: 11 }} domain={["auto", "auto"]} width={54} />
                <Tooltip contentStyle={{ background: "#141a2e", border: "1px solid #26304d", borderRadius: 8 }} />
                <Line type="monotone" dataKey="close" stroke="#4f8cff" dot={false} strokeWidth={2} name="Close" />
                <Line type="monotone" dataKey="sma_20" stroke="#f59e0b" dot={false} strokeWidth={1} name="SMA 20" />
                <Line type="monotone" dataKey="sma_50" stroke="#a78bfa" dot={false} strokeWidth={1} name="SMA 50" />
                <Line type="monotone" dataKey="sma_200" stroke="#22c55e" dot={false} strokeWidth={1} name="SMA 200" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <div style={{ height: 16 }} />

      <div className="grid cols-2">
        <Card title="RSI (14)">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ left: 4, right: 16, top: 8 }}>
                <CartesianGrid stroke="#26304d" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" stroke="#8b93a7" tick={{ fontSize: 10 }} minTickGap={50} />
                <YAxis stroke="#8b93a7" tick={{ fontSize: 11 }} domain={[0, 100]} width={40} />
                <Tooltip contentStyle={{ background: "#141a2e", border: "1px solid #26304d", borderRadius: 8 }} />
                <ReferenceLine y={70} stroke="#ef4444" strokeDasharray="4 4" />
                <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="rsi_14" stroke="#4f8cff" dot={false} strokeWidth={2} name="RSI" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="MACD">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ left: 4, right: 16, top: 8 }}>
                <CartesianGrid stroke="#26304d" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" stroke="#8b93a7" tick={{ fontSize: 10 }} minTickGap={50} />
                <YAxis stroke="#8b93a7" tick={{ fontSize: 11 }} width={44} />
                <Tooltip contentStyle={{ background: "#141a2e", border: "1px solid #26304d", borderRadius: 8 }} />
                <ReferenceLine y={0} stroke="#47506e" />
                <Line type="monotone" dataKey="macd" stroke="#4f8cff" dot={false} strokeWidth={2} name="MACD" />
                <Line type="monotone" dataKey="macd_signal" stroke="#f59e0b" dot={false} strokeWidth={1} name="Signal" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div style={{ height: 16 }} />

      <QueryView query={stock}>
        {(d) => (
          <Card title="Fundamentals" subtitle="Latest snapshot">
            <div className="fund-grid">
              <FundItem k="P/E" v={fmtNum(d.pe_ratio, 1)} />
              <FundItem k="P/B" v={fmtNum(d.pb_ratio, 2)} />
              <FundItem k="EPS" v={fmtNum(d.eps, 2)} />
              <FundItem k="Dividend yield" v={d.dividend_yield ? fmtPct((d.dividend_yield as number) * 100) : "—"} />
              <FundItem k="Market cap" v={fmtCompact(d.market_cap)} />
              <FundItem k="Beta" v={fmtNum(d.beta, 2)} />
              <FundItem k="ROE" v={d.return_on_equity != null ? fmtPct((d.return_on_equity as number) * 100) : "—"} />
              <FundItem k="Debt / equity" v={fmtNum(d.debt_to_equity, 2)} />
              <FundItem k="Revenue growth" v={d.revenue_growth != null ? fmtPct((d.revenue_growth as number) * 100) : "—"} />
              <FundItem k="Earnings growth" v={d.earnings_growth != null ? fmtPct((d.earnings_growth as number) * 100) : "—"} />
              <FundItem k="52w high" v={fmtNum(d.fifty_two_week_high)} />
              <FundItem k="52w low" v={fmtNum(d.fifty_two_week_low)} />
              <FundItem k="Distance from 52w high" v={d.distance_from_52w_high != null ? fmtPct((d.distance_from_52w_high as number) * 100) : "—"} />
              <FundItem k="1m momentum" v={d.price_momentum_1m != null ? fmtPct((d.price_momentum_1m as number) * 100) : "—"} />
              <FundItem k="3m momentum" v={d.price_momentum_3m != null ? fmtPct((d.price_momentum_3m as number) * 100) : "—"} />
            </div>
          </Card>
        )}
      </QueryView>
    </>
  );
}
