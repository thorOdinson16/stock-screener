import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../hooks";
import type { PicksResponse } from "../types";
import { changeClass, fmtNum, fmtPct } from "../format";
import { Card, QueryView, Updated } from "../components/ui";

type Horizon = "fwd_ret_5d" | "fwd_ret_21d";

export default function TopPicks() {
  const [label, setLabel] = useState<Horizon>("fwd_ret_21d");
  const picks = useApi<PicksResponse>(["picks", label], `/picks?label=${label}&k=20`);
  const navigate = useNavigate();

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Top Picks</h1>
          <p className="page-sub">
            Ranked by the ML score for the selected forward-return horizon
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <div className="seg">
            <button className={label === "fwd_ret_5d" ? "active" : ""} onClick={() => setLabel("fwd_ret_5d")}>
              5-day
            </button>
            <button className={label === "fwd_ret_21d" ? "active" : ""} onClick={() => setLabel("fwd_ret_21d")}>
              21-day
            </button>
          </div>
          <Updated at={picks.dataUpdatedAt} />
        </div>
      </div>

      <Card
        title={`Top ${picks.data?.count ?? 20} picks — ${label === "fwd_ret_5d" ? "5-day" : "21-day"} horizon`}
        subtitle="Click a row for full detail"
      >
        <QueryView query={picks}>
          {(d) => (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Symbol</th>
                    <th>Industry</th>
                    <th className="num">Score</th>
                    <th className="num">Close</th>
                    <th className="num">Change</th>
                    <th className="num">RSI</th>
                    <th className="num">P/E</th>
                    <th className="num">1m mom</th>
                  </tr>
                </thead>
                <tbody>
                  {d.picks.map((r) => (
                    <tr key={r.symbol} className="clickable" onClick={() => navigate(`/stocks/${r.symbol}`)}>
                      <td><span className="badge rank">{r.rank}</span></td>
                      <td>
                        {r.symbol.replace(".NS", "")}
                        <div className="tag">{r.company_name}</div>
                      </td>
                      <td>{r.industry}</td>
                      <td className="num mono">{fmtNum(r.score, 4)}</td>
                      <td className="num mono">{fmtNum(r.close)}</td>
                      <td className={`num mono ${changeClass(r.change_percent)}`}>{fmtPct(r.change_percent)}</td>
                      <td className="num mono">{fmtNum(r.rsi_14, 1)}</td>
                      <td className="num mono">{fmtNum(r.pe_ratio, 1)}</td>
                      <td className={`num mono ${changeClass((r.price_momentum_1m as number) * 100)}`}>
                        {r.price_momentum_1m === null || r.price_momentum_1m === undefined
                          ? "—"
                          : fmtPct((r.price_momentum_1m as number) * 100)}
                      </td>
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
