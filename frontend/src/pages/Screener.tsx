import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../hooks";
import type { ScreenerResponse, StockRow, UniverseResponse } from "../types";
import { buildQuery } from "../api";
import { changeClass, fmtCompact, fmtNum, fmtPct } from "../format";
import { Card, QueryView, Updated } from "../components/ui";

interface Filters {
  label: string;
  industries: string[];
  minScore: string;
  minPe: string;
  maxPe: string;
  minRsi: string;
  maxRsi: string;
  minMomentum: string;
  nearHigh: string;
  minVolumeRatio: string;
  sort: string;
  order: string;
}

const DEFAULTS: Filters = {
  label: "fwd_ret_21d",
  industries: [],
  minScore: "",
  minPe: "",
  maxPe: "",
  minRsi: "",
  maxRsi: "",
  minMomentum: "",
  nearHigh: "",
  minVolumeRatio: "",
  sort: "score",
  order: "desc",
};

const num = (s: string): number | undefined => (s.trim() === "" ? undefined : Number(s));

function toCsv(rows: StockRow[]): string {
  const cols = [
    "symbol", "company_name", "industry", "close", "change_percent", "score", "rank",
    "rsi_14", "pe_ratio", "price_momentum_1m", "volume_ratio", "distance_from_52w_high",
  ];
  const header = cols.join(",");
  const body = rows
    .map((r) => cols.map((c) => JSON.stringify(r[c] ?? "")).join(","))
    .join("\n");
  return `${header}\n${body}`;
}

export default function Screener() {
  const [filters, setFilters] = useState<Filters>(DEFAULTS);
  const universe = useApi<UniverseResponse>(["universe"], "/universe");
  const navigate = useNavigate();

  const path = useMemo(() => {
    const qs = buildQuery({
      label: filters.label,
      industry: filters.industries,
      min_score: num(filters.minScore),
      min_pe: num(filters.minPe),
      max_pe: num(filters.maxPe),
      min_rsi: num(filters.minRsi),
      max_rsi: num(filters.maxRsi),
      min_momentum: num(filters.minMomentum) === undefined ? undefined : num(filters.minMomentum)! / 100,
      max_distance_52w_high:
        num(filters.nearHigh) === undefined ? undefined : -Math.abs(num(filters.nearHigh)!) / 100,
      min_volume_ratio: num(filters.minVolumeRatio),
      sort: filters.sort,
      order: filters.order,
      limit: 200,
    });
    return `/screener${qs}`;
  }, [filters]);

  const results = useApi<ScreenerResponse>(["screener", path], path);

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));

  const exportCsv = () => {
    const rows = results.data?.rows ?? [];
    const blob = new Blob([toCsv(rows)], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "screener.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Screener</h1>
          <p className="page-sub">Filter the universe by score, valuation, momentum and technicals</p>
        </div>
        <Updated at={results.dataUpdatedAt} />
      </div>

      <Card title="Filters" className="filters">
        <div className="controls">
          <div className="field">
            <label>Horizon</label>
            <select value={filters.label} onChange={(e) => set({ label: e.target.value })}>
              <option value="fwd_ret_21d">21-day</option>
              <option value="fwd_ret_5d">5-day</option>
            </select>
          </div>
          <div className="field">
            <label>Industries</label>
            <select
              multiple
              value={filters.industries}
              onChange={(e) => set({ industries: Array.from(e.target.selectedOptions, (o) => o.value) })}
            >
              {(universe.data?.industries ?? []).map((i) => (
                <option key={i.industry} value={i.industry}>
                  {i.industry} ({i.count})
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Min score</label>
            <input style={{ width: 100 }} value={filters.minScore} onChange={(e) => set({ minScore: e.target.value })} placeholder="e.g. 0.02" />
          </div>
          <div className="field">
            <label>P/E min</label>
            <input style={{ width: 90 }} value={filters.minPe} onChange={(e) => set({ minPe: e.target.value })} placeholder="0" />
          </div>
          <div className="field">
            <label>P/E max</label>
            <input style={{ width: 90 }} value={filters.maxPe} onChange={(e) => set({ maxPe: e.target.value })} placeholder="30" />
          </div>
          <div className="field">
            <label>RSI min</label>
            <input style={{ width: 80 }} value={filters.minRsi} onChange={(e) => set({ minRsi: e.target.value })} placeholder="40" />
          </div>
          <div className="field">
            <label>RSI max</label>
            <input style={{ width: 80 }} value={filters.maxRsi} onChange={(e) => set({ maxRsi: e.target.value })} placeholder="70" />
          </div>
          <div className="field">
            <label>Min 1m momentum %</label>
            <input style={{ width: 100 }} value={filters.minMomentum} onChange={(e) => set({ minMomentum: e.target.value })} placeholder="0" />
          </div>
          <div className="field">
            <label>Within % of 52w high</label>
            <input style={{ width: 110 }} value={filters.nearHigh} onChange={(e) => set({ nearHigh: e.target.value })} placeholder="5" />
          </div>
          <div className="field">
            <label>Min volume ratio</label>
            <input style={{ width: 100 }} value={filters.minVolumeRatio} onChange={(e) => set({ minVolumeRatio: e.target.value })} placeholder="1.2" />
          </div>
          <div className="field">
            <label>Sort by</label>
            <select value={filters.sort} onChange={(e) => set({ sort: e.target.value })}>
              <option value="score">Score</option>
              <option value="rank">Rank</option>
              <option value="change_percent">Change %</option>
              <option value="momentum">1m momentum</option>
              <option value="rsi_14">RSI</option>
              <option value="pe_ratio">P/E</option>
              <option value="market_cap">Market cap</option>
              <option value="symbol">Symbol</option>
            </select>
          </div>
          <div className="field">
            <label>Order</label>
            <select value={filters.order} onChange={(e) => set({ order: e.target.value })}>
              <option value="desc">Descending</option>
              <option value="asc">Ascending</option>
            </select>
          </div>
          <button className="btn" onClick={() => setFilters(DEFAULTS)}>Reset</button>
          <button className="btn primary" onClick={exportCsv} disabled={!results.data?.rows?.length}>Export CSV</button>
        </div>
      </Card>

      <div style={{ height: 16 }} />

      <Card
        title={`Results — ${results.data?.total ?? 0} matches`}
        subtitle="Showing up to 200 rows"
      >
        <QueryView query={results}>
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Industry</th>
                    <th className="num">Score</th>
                    <th className="num">Rank</th>
                    <th className="num">Close</th>
                    <th className="num">Change</th>
                    <th className="num">RSI</th>
                    <th className="num">P/E</th>
                    <th className="num">Mkt cap</th>
                    <th className="num">Vol ratio</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.symbol} className="clickable" onClick={() => navigate(`/stocks/${r.symbol}`)}>
                      <td>
                        {r.symbol.replace(".NS", "")}
                        <div className="tag">{r.company_name}</div>
                      </td>
                      <td>{r.industry}</td>
                      <td className="num mono">{fmtNum(r.score, 4)}</td>
                      <td className="num mono">{fmtNum(r.rank, 0)}</td>
                      <td className="num mono">{fmtNum(r.close)}</td>
                      <td className={`num mono ${changeClass(r.change_percent)}`}>{fmtPct(r.change_percent)}</td>
                      <td className="num mono">{fmtNum(r.rsi_14, 1)}</td>
                      <td className="num mono">{fmtNum(r.pe_ratio, 1)}</td>
                      <td className="num mono">{fmtCompact(r.market_cap)}</td>
                      <td className="num mono">{fmtNum(r.volume_ratio, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryView>
      </Card>
    </>
  );
}
