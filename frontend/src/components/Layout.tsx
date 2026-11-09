import { NavLink, Outlet } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

const NAV = [
  { to: "/", label: "Dashboard", icon: "▦" },
  { to: "/picks", label: "Top Picks", icon: "★" },
  { to: "/trade-plan", label: "Trade Planner", icon: "◇" },
  { to: "/forecasts", label: "Forecast Lab", icon: "◎" },
  { to: "/screener", label: "Screener", icon: "⚲" },
  { to: "/model", label: "Model", icon: "◈" },
  { to: "/performance", label: "Track Record", icon: "◷" },
  { to: "/ops", label: "Ops", icon: "⚙" },
];

export default function Layout() {
  const queryClient = useQueryClient();

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">▲</span>
          <div>
            <div className="brand-name">Stock Screening</div>
            <div className="brand-sub">NIFTY 500 · ML scoring</div>
          </div>
        </div>
        <nav>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => `navlink ${isActive ? "active" : ""}`}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <button className="btn" onClick={() => queryClient.invalidateQueries()}>
            Refresh data
          </button>
          <p className="muted">On-demand: data updates when you run the pipeline, then refresh.</p>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
