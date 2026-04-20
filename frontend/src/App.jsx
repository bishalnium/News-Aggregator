import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Sidebar from "./components/Sidebar";
import AlertHistory from "./pages/AlertHistory";
import ChatAssistant from "./pages/ChatAssistant";
import NewsFeed from "./pages/NewsFeed";
import Watchlist from "./pages/Watchlist";

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

function App() {
  const [theme, setTheme] = useState(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  function toggleTheme() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
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
