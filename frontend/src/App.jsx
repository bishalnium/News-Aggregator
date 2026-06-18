import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Sidebar from "./components/Sidebar";
import AlertHistory from "./pages/AlertHistory";
import ChatAssistant from "./pages/ChatAssistant";
import NewsFeed from "./pages/NewsFeed";
import Watchlist from "./pages/Watchlist";
import Settings from "./pages/Settings";
import { verifyPasscode, verifyBypassToken } from "./api";

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
        <h2>NewsBuddy</h2>
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
  const [verifyingBypass, setVerifyingBypass] = useState(() => {
    if (window.sessionStorage.getItem("newscodex-auth") === "true") {
      return false;
    }
    const params = new URLSearchParams(window.location.search);
    return !!params.get("bypass");
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const bypassToken = params.get("bypass");

    if (bypassToken && !isAuthenticated) {
      verifyBypassToken(bypassToken)
        .then((res) => {
          if (res && res.ok) {
            window.sessionStorage.setItem("newscodex-auth", "true");
            setIsAuthenticated(true);
          }
        })
        .catch((err) => {
          console.error("Failed to verify bypass token:", err);
        })
        .finally(() => {
          setVerifyingBypass(false);
        });
    }
  }, [isAuthenticated]);

  function toggleTheme() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }

  function handleUnlock() {
    window.sessionStorage.setItem("newscodex-auth", "true");
    setIsAuthenticated(true);
  }

  if (verifyingBypass) {
    return (
      <div className="passcode-container">
        <div className="passcode-card" style={{ textAlign: "center" }}>
          <h2>Authenticating Secure Session...</h2>
          <p>Connecting to NewsBuddy mobile gateway.</p>
        </div>
      </div>
    );
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
          <Route path="/settings" element={<Settings theme={theme} onToggleTheme={toggleTheme} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
