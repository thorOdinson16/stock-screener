import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiPost } from "../api";
import { Card, ErrorState, StatCard } from "../components/ui";
import { fmtNum } from "../format";
import { useApi } from "../hooks";

type Candidate = { symbol: string; industry: string; rank: number; score: number; reference_price: number; assumed_entry: number; quantity: number; cash_required: number; stop_reference: number; planned_risk: number; weight_pct: number; context: string[]; forecast?: { status: string; reasons: string[]; profit_probability: number | null; net_return_band: { low: number; high: number } | null } };
type Plan = { asof: string; status: string; candidates: Candidate[]; skipped: { symbol: string; reasons: string[] }[]; cash_before: number; cash_remaining: number; method: string };
const initial = { capital: 100000, horizon: 21, max_positions: 10, risk_pct: .5, stop_pct: 8,
  max_position_pct: 15, max_sector_pct: 30, fee_bps: 10, slippage_bps: 10, min_turnover: 10000000,
  max_volatility: .06, flat_fee: 0 };
const fields: [keyof typeof initial, string, string][] = [
  ["capital", "Total capital (₹)", "1000"], ["risk_pct", "Planned risk per position (%)", "0.1"],
  ["max_positions", "Maximum positions", "1"], ["stop_pct", "Stop distance (%)", "0.5"],
  ["max_position_pct", "Maximum stock weight (%)", "1"], ["max_sector_pct", "Maximum industry weight (%)", "1"],
  ["min_turnover", "Minimum average traded value (₹/day)", "1000000"],
  ["max_volatility", "Maximum daily volatility (fraction)", "0.01"],
  ["fee_bps", "Estimated fees per side (bps)", "1"], ["slippage_bps", "Slippage per side (bps)", "1"],
  ["flat_fee", "Additional fee per order (₹)", "1"],
];

export default function TradePlan() {
  const quality = useApi<{ model_uses_current_quality_policy: boolean; corporate_actions: { id: string; symbols: string[]; source: string; note: string }[] }>(["research-quality"], "/research/quality");
  const [settings, setSettings] = useState(initial);
  const [holdings, setHoldings] = useState("");
  const [inputError, setInputError] = useState("");
  const [changed, setChanged] = useState(false);
  const mutation = useMutation({ mutationFn: (body: unknown) => apiPost<Plan>("/trade-plan", body) });
  const calculate = () => {
    try {
      const parsed = holdings.trim() ? holdings.trim().split("\n").map(line => {
        const [symbol, quantity, price, industry = "Unknown"] = line.split(",").map(s => s.trim());
        if (!symbol || !Number.isInteger(Number(quantity)) || Number(quantity) <= 0 || !Number.isFinite(Number(price)) || Number(price) <= 0) throw new Error("Holdings need: symbol, whole shares, current price, industry (one per line).");
        return { symbol, quantity: Number(quantity), price: Number(price), industry };
      }) : [];
      setInputError(""); setChanged(false);
      mutation.mutate({ settings, holdings: parsed });
    } catch (error) { setInputError((error as Error).message); }
  };
  return <>
    <div className="page-head"><div><h1 className="page-title">Trade Planner</h1><p className="page-sub">Cash equity scenarios · whole shares · capital and risk limits</p></div></div>
    <div className="grid" style={{ gap: 16 }}>
      {quality.data && <Card title="Research data status" subtitle={quality.data.model_uses_current_quality_policy ? "Model records the current corporate-action policy" : "Current model predates the new quality policy — retrain after refreshing data"}>
        <p className="muted">Split/dividend adjustments remain provider supplied. Complex share entitlements below are unresolved; affected outcomes are withheld and indicator histories reset. Calibrated probabilities and return ranges are not available yet.</p>
        <details><summary>Corporate-action evidence</summary>{quality.data.corporate_actions.map(event => <p key={event.id}><a className="link" href={event.source} target="_blank" rel="noreferrer">{event.symbols.join(", ")}</a> · {event.note}</p>)}</details>
      </Card>}
      <Card title="Build a paper plan" subtitle="Defaults are scenarios to test, not optimized trading rules">
        <p className="muted">Use total capital including existing holdings. Model scores rank stocks; they do not provide a probability of profit or a guaranteed target. The planner can return no suitable setup.</p>
        <p className="muted"><Link className="link" to="/forecasts">Forecast Lab</Link> measures probability and range reliability. Estimates remain unavailable when evidence or scenario settings do not match.</p>
        <div className="field" style={{ marginBottom: 16 }}><label>Holding window</label><select value={settings.horizon} onChange={e => { setSettings({ ...settings, horizon: Number(e.target.value) }); setChanged(true); }}><option value={21}>21 trading sessions</option><option value={5}>5 trading sessions</option></select></div>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 }}>
          {fields.map(([key, label, step]) => <div className="field" key={key}><label htmlFor={`plan-${key}`}>{label}</label><input id={`plan-${key}`} type="number" step={step} value={settings[key]} onChange={e => { setSettings({ ...settings, [key]: Number(e.target.value) }); setChanged(true); }} /></div>)}
        </div>
        <div className="field" style={{ margin: "16px 0" }}><label htmlFor="plan-holdings">Existing holdings (optional): symbol, shares, current price, industry</label><textarea id="plan-holdings" rows={3} value={holdings} placeholder="RELIANCE.NS,10,1500,Energy" onChange={e => { setHoldings(e.target.value); setChanged(true); }} style={{ width: "100%", background: "#141a2e", color: "inherit", border: "1px solid #26304d", padding: 10, borderRadius: 6 }} /></div>
        <button className="btn primary" onClick={calculate} disabled={mutation.isPending}>{mutation.isPending ? "Calculating…" : "Calculate plan"}</button>
        {inputError && <p className="neg">{inputError}</p>}
        {mutation.error && <ErrorState error={mutation.error} />}
      </Card>
      {mutation.data && <>
        {changed && <p className="muted">Settings changed. Calculate again to update the results below.</p>}
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
          <StatCard label="Qualified allocations" value={mutation.data.candidates.length} sub={`Snapshot checked ${mutation.data.asof}`} />
          <StatCard label="Cash available before plan" value={`₹${fmtNum(mutation.data.cash_before)}`} />
          <StatCard label="Cash remaining" value={`₹${fmtNum(mutation.data.cash_remaining)}`} />
        </div>
        <Card title={mutation.data.status === "no_suitable_setup" ? "No suitable setup" : "Reference-price allocations"} subtitle="Recalculate quantity at the actual entry price; this does not place orders">
          {mutation.data.candidates.length === 0 ? <p className="muted">No allocation passes the current data and risk limits. Check the reasons below and refresh market data if needed.</p> : <div className="table-wrap"><table><thead><tr><th>Stock / context</th><th>Rank</th><th>Shares</th><th>Reference</th><th>Assumed fill</th><th>Cash required</th><th>Stop reference</th><th>Planned risk</th><th>Weight</th><th>Forecast evidence</th></tr></thead><tbody>
            {mutation.data.candidates.map(c => <tr key={c.symbol}><td><Link className="link" to={`/stocks/${encodeURIComponent(c.symbol)}`}>{c.symbol}</Link><div className="tag">{c.industry} · {c.context.join(" · ")}</div></td><td>{c.rank}</td><td>{c.quantity}</td><td>₹{fmtNum(c.reference_price)}</td><td>₹{fmtNum(c.assumed_entry)}</td><td>₹{fmtNum(c.cash_required)}</td><td>₹{fmtNum(c.stop_reference)}</td><td>₹{fmtNum(c.planned_risk)}</td><td>{fmtNum(c.weight_pct, 1)}%</td><td title={c.forecast?.reasons.join("; ")}>{c.forecast?.profit_probability != null ? `${(c.forecast.profit_probability * 100).toFixed(1)}% profit probability` : "Insufficient evidence"}{c.forecast?.net_return_band && <div className="tag">Band {(c.forecast.net_return_band.low * 100).toFixed(1)}% to {(c.forecast.net_return_band.high * 100).toFixed(1)}%</div>}</td></tr>)}
          </tbody></table></div>}
          <p className="muted">{mutation.data.method} A stop is a reference, not a maximum-loss guarantee. Momentum and RSI values are descriptive context, not an explanation of model causality.</p>
        </Card>
        <Card title="Candidates not allocated" subtitle="Reasons remain visible; the planner does not force a full basket"><div className="table-wrap"><table><thead><tr><th>Stock</th><th>Reason</th></tr></thead><tbody>{mutation.data.skipped.map(c => <tr key={c.symbol}><td>{c.symbol}</td><td>{c.reasons.join("; ")}</td></tr>)}</tbody></table></div></Card>
      </>}
    </div>
  </>;
}
