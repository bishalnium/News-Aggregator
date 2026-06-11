import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Sidebar from "./components/Sidebar";
import AlertHistory from "./pages/AlertHistory";
import ChatAssistant from "./pages/ChatAssistant";
import NewsFeed from "./pages/NewsFeed";
import Watchlist from "./pages/Watchlist";
import { verifyPasscode } from "./api";

const THEME_STORAGE_KEY = "newscodex-theme";

function getInitialTheme() {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "dark" || stored === "light") {
    return stored;
  }

  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }

  return "light";
}

function PasscodeScreen({ onUnlock }) {
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!passcode) return;
    setLoading(true);
    setError("");
    try {
      const res = await verifyPasscode(passcode);
      if (res && res.ok) {
        onUnlock();
      } else {
        setError("Invalid passcode. Please try again.");
      }
    } catch (err) {
      setError(err.message || "Failed to verify passcode.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="passcode-container">
      <div className="passcode-card">
        <h2>News Codex</h2>
        <p>Enter passcode to access the secure news aggregator stream.</p>
        <form onSubmit={handleSubmit}>
          <input
            type="password"
            placeholder="Enter passcode..."
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            disabled={loading}
            autoFocus
          />
          {error && <div className="passcode-error">{error}</div>}
          <button type="submit" disabled={loading}>
            {loading ? "Unlocking..." : "Unlock Dashboard"}
          </button>
        </form>
      </div>
    </div>
  );
}

function App() {
  const [theme, setTheme] = useState(getInitialTheme);
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    return window.sessionStorage.getItem("newscodex-auth") === "true";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  function toggleTheme() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }

  function handleUnlock() {
    window.sessionStorage.setItem("newscodex-auth", "true");
    setIsAuthenticated(true);
  }

  if (!isAuthenticated) {
    return <PasscodeScreen onUnlock={handleUnlock} />;
  }

  return (
    <div className="app-shell">
      <Sidebar theme={theme} onToggleTheme={toggleTheme} />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<NewsFeed />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/alerts" element={<AlertHistory />} />
          <Route path="/chat" element={<ChatAssistant />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
