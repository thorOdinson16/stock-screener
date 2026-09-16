import { NavLink, Outlet } from "react-router-dom";
import { useSettings } from "../settings";

const NAV = [
  { to: "/", label: "Dashboard", icon: "▦" },
  { to: "/picks", label: "Top Picks", icon: "★" },
  { to: "/screener", label: "Screener", icon: "⚲" },
  { to: "/model", label: "Model", icon: "◈" },
  { to: "/ops", label: "Ops", icon: "⚙" },
];

export default function Layout() {
  const { autoRefresh, setAutoRefresh } = useSettings();

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
          <label className="toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            <span>Auto-refresh 30s</span>
          </label>
          <p className="muted">Manual by default. Toggle to poll live.</p>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
