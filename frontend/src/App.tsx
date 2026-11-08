import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import TopPicks from "./pages/TopPicks";
import Screener from "./pages/Screener";
import StockDetail from "./pages/StockDetail";
import Model from "./pages/Model";
import Ops from "./pages/Ops";
import Performance from "./pages/Performance";
import TradePlan from "./pages/TradePlan";
import ForecastLab from "./pages/ForecastLab";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="picks" element={<TopPicks />} />
        <Route path="screener" element={<Screener />} />
        <Route path="stocks/:symbol" element={<StockDetail />} />
        <Route path="model" element={<Model />} />
        <Route path="performance" element={<Performance />} />
        <Route path="trade-plan" element={<TradePlan />} />
        <Route path="forecasts" element={<ForecastLab />} />
        <Route path="ops" element={<Ops />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
