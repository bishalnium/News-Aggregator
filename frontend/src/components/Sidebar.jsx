import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Live Feed" },
  { to: "/watchlist", label: "Alert Setup" },
  { to: "/alerts", label: "Alert History" },
  { to: "/chat", label: "AI Chat" },
  { to: "/settings", label: "Settings" },
];

function Sidebar({ theme, onToggleTheme }) {
  return (
    <aside className="sidebar">
      <div className="brand-wrap">
        <p className="brand-kicker">Reliable Source Engine</p>
        <h1 className="brand-title">News Codex</h1>
        <button type="button" className="theme-toggle-btn" onClick={onToggleTheme}>
          {theme === "dark" ? "Switch To Light" : "Switch To Dark"}
        </button>
      </div>

      <nav className="sidebar-nav">
        {links.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `nav-link ${isActive ? "nav-link-active" : ""}`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footnote">
        <p>Telegram Only</p>
        <p>Instant ingest, rolling summaries, NLP alerts.</p>
      </div>
    </aside>
  );
}

export default Sidebar;
