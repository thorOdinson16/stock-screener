import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import TopPicks from "./pages/TopPicks";
import Screener from "./pages/Screener";
import StockDetail from "./pages/StockDetail";
import Model from "./pages/Model";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="picks" element={<TopPicks />} />
        <Route path="screener" element={<Screener />} />
        <Route path="stocks/:symbol" element={<StockDetail />} />
        <Route path="model" element={<Model />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
