import { useEffect, useMemo, useState } from "react";

import {
  fetchNews,
  fetchSummaryBatches,
  getSettings,
  getWebSocketUrl,
  setSummaryInterval,
} from "../api";
import NewsCard from "../components/NewsCard";

const SUMMARY_INTERVAL_OPTIONS = [
  30,
  60,
  120,
  300,
  600,
  900,
  1800,
  3600,
  7200,
  86400,
];

const NEWS_PAGE_SIZE = 200;
const SUMMARY_STEP = 80;

function prettyDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return date.toLocaleString();
}

function intervalLabel(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${seconds / 60}m`;
  if (seconds < 86400) return `${seconds / 3600}h`;
  return `${seconds / 86400}d`;
}

function cleanSummaryText(value) {
  if (!value) return "";
  return value
    .split("\n")
    .map((line) => line.replace(/^\s*[-*]\s*(telegram|twitter|source)\s*:\s*/i, "- "))
    .filter((line) => !/^\s*sources?\s*:/i.test(line))
    .join("\n")
    .replace(/\.{3,}|…+/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function mergeNewsRows(previous, incoming) {
  const incomingId = incoming?.id;
  const incomingHash = incoming?.content_hash;
  const incomingTempId = incoming?.client_temp_id;

  const deduped = previous.filter((item) => {
    if (incomingId !== undefined && incomingId !== null && item.id === incomingId) {
      return false;
    }
    if (incomingHash && item.content_hash === incomingHash) {
      return false;
    }
    if (incomingTempId && item.client_temp_id === incomingTempId) {
      return false;
    }
    return true;
  });

  return [incoming, ...deduped].slice(0, 1000);
}

function getNewsItemIdentity(item) {
  return (
    item.id
    ?? item.content_hash
    ?? item.client_temp_id
    ?? `${item.source || "unknown"}-${item.fetched_at || item.published_at || "na"}-${item.raw_text || ""}`
  );
}

function appendOlderNews(previous, incomingRows) {
  const seen = new Set(previous.map((item) => getNewsItemIdentity(item)));
  const merged = [...previous];

  for (const row of incomingRows) {
    const identity = getNewsItemIdentity(row);
    if (seen.has(identity)) {
      continue;
    }
    seen.add(identity);
    merged.push(row);
  }

  return merged.slice(0, 3000);
}

function getNewsItemKey(item, index) {
  const identity = getNewsItemIdentity(item);
  return `${identity}-${index}`;
}

function NewsFeed() {
  const [news, setNews] = useState([]);
  const [summaries, setSummaries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isSocketLive, setIsSocketLive] = useState(false);
  const [interval, setInterval] = useState(120);
  const [banner, setBanner] = useState("");
  const [search, setSearch] = useState("");
  const [summarySearch, setSummarySearch] = useState("");
  const [newsPage, setNewsPage] = useState(1);
  const [hasMoreNews, setHasMoreNews] = useState(true);
  const [loadingMoreNews, setLoadingMoreNews] = useState(false);
  const [summaryLimit, setSummaryLimit] = useState(SUMMARY_STEP);
  const [hasMoreSummaries, setHasMoreSummaries] = useState(true);
  const [loadingMoreSummaries, setLoadingMoreSummaries] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadInitial() {
      setLoading(true);
      setError("");
      try {
        const [newsData, summaryData, settingsData] = await Promise.all([
          fetchNews({ page: 1, limit: NEWS_PAGE_SIZE }),
          fetchSummaryBatches(SUMMARY_STEP),
          getSettings(),
        ]);
        if (cancelled) return;
        setNews(newsData || []);
        setSummaries(summaryData || []);
        setNewsPage(1);
        setHasMoreNews((newsData || []).length === NEWS_PAGE_SIZE);
        setSummaryLimit(SUMMARY_STEP);
        setHasMoreSummaries((summaryData || []).length === SUMMARY_STEP);
        setInterval(settingsData.interval_seconds || 120);
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Failed to load feed");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadInitial();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let socket;
    let reconnectTimer;
    let active = true;

    const connect = () => {
      socket = new WebSocket(getWebSocketUrl());

      socket.onopen = () => {
        if (!active) return;
        setIsSocketLive(true);
      };

      socket.onmessage = (event) => {
        if (!active) return;
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "news_item" && payload.data) {
            setNews((prev) => mergeNewsRows(prev, payload.data));
          }
          if (payload.type === "summary_batch" && payload.data) {
            setSummaries((prev) => {
              const targetSize = Math.max(prev.length, SUMMARY_STEP);
              return [payload.data, ...prev.filter((item) => item.id !== payload.data.id)].slice(0, targetSize);
            });
          }
        } catch {
          // Ignore malformed payload.
        }
      };

      socket.onclose = () => {
        if (!active) return;
        setIsSocketLive(false);
        reconnectTimer = setTimeout(connect, 500);
      };

      socket.onerror = () => {
        setIsSocketLive(false);
      };
    };

    connect();

    return () => {
      active = false;
      setIsSocketLive(false);
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      if (socket && socket.readyState <= 1) {
        socket.close();
      }
    };
  }, []);

  const filteredNews = useMemo(() => {
    return news.filter((item) => {
      const text = `${item.raw_text || ""} ${item.summary || ""}`.toLowerCase();
      const matchSearch = search ? text.includes(search.toLowerCase()) : true;
      return matchSearch;
    });
  }, [news, search]);

  const filteredSummaries = useMemo(() => {
    return summaries.filter((batch) => {
      const searchBlob = [
        batch.summary_text || "",
        String(batch.window_seconds || ""),
      ]
        .join(" ")
        .toLowerCase();

      const matchSearch = summarySearch
        ? searchBlob.includes(summarySearch.toLowerCase())
        : true;

      return matchSearch;
    });
  }, [summaries, summarySearch]);

  async function handleIntervalClick(value) {
    try {
      const response = await setSummaryInterval(value);
      setInterval(response.interval_seconds);
      setBanner(response.message);
      setTimeout(() => setBanner(""), 3000);
    } catch (err) {
      setBanner(err.message || "Unable to change summary interval");
      setTimeout(() => setBanner(""), 3500);
    }
  }

  async function handleLoadMoreNews() {
    if (loadingMoreNews || !hasMoreNews) {
      return;
    }

    const nextPage = newsPage + 1;
    setLoadingMoreNews(true);
    try {
      const rows = await fetchNews({ page: nextPage, limit: NEWS_PAGE_SIZE });
      const list = rows || [];
      setNews((prev) => appendOlderNews(prev, list));
      setNewsPage(nextPage);
      setHasMoreNews(list.length === NEWS_PAGE_SIZE);
    } catch (err) {
      setBanner(err.message || "Unable to load more news");
      setTimeout(() => setBanner(""), 3000);
    } finally {
      setLoadingMoreNews(false);
    }
  }

  async function handleLoadMoreSummaries() {
    if (loadingMoreSummaries || !hasMoreSummaries) {
      return;
    }

    const nextLimit = summaryLimit + SUMMARY_STEP;
    setLoadingMoreSummaries(true);
    try {
      const rows = await fetchSummaryBatches(nextLimit);
      const list = rows || [];
      setSummaries(list);
      setSummaryLimit(nextLimit);
      setHasMoreSummaries(list.length === nextLimit);
    } catch (err) {
      setBanner(err.message || "Unable to load more summaries");
      setTimeout(() => setBanner(""), 3000);
    } finally {
      setLoadingMoreSummaries(false);
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>Real-Time News Stream</h2>
          <p className="muted">
            Direct ingest from Telegram with backend-synced summary windows.
          </p>
        </div>
        <div className={`status-pill ${isSocketLive ? "status-live" : "status-down"}`}>
          {isSocketLive ? "Live" : "Reconnecting"}
        </div>
      </header>

      {banner && <div className="banner-note">{banner}</div>}
      {error && <div className="error-note">{error}</div>}

      <div className="news-layout">
        <section className="panel panel-feed">
          <h3>News Feed</h3>
          <p className="muted">Raw incoming messages from your configured channels.</p>

          <div className="control-row raw-control-row">
            <input
              className="search-input"
              placeholder="Search source history"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>

          {loading && <p className="muted">Loading feed...</p>}
          {!loading && filteredNews.length === 0 && (
            <p className="muted">No news received yet for this filter.</p>
          )}
          <div className="feed-list">
            {filteredNews.map((item, index) => (
              <NewsCard key={getNewsItemKey(item, index)} item={item} />
            ))}
          </div>

          <div className="list-footer-action">
            {hasMoreNews ? (
              <button type="button" onClick={handleLoadMoreNews} disabled={loadingMoreNews}>
                {loadingMoreNews ? "Loading..." : "Load Older News"}
              </button>
            ) : (
              <p className="muted">Reached oldest available news for now.</p>
            )}
          </div>
        </section>

        <section className="panel panel-summary">
          <h3>Summarized Feed</h3>
          <p className="muted">
            Live summary batches grouped by selected window. Changing interval applies
            from now onward only.
          </p>

          <div className="summary-controls summary-controls-right">
            <p>Summary timer</p>
            {SUMMARY_INTERVAL_OPTIONS.map((value) => (
              <button
                key={value}
                type="button"
                className={`interval-btn ${interval === value ? "interval-btn-active" : ""}`}
                onClick={() => handleIntervalClick(value)}
              >
                {intervalLabel(value)}
              </button>
            ))}
          </div>

          <div className="control-row summary-control-row">
            <input
              className="search-input summary-search-input"
              placeholder="Search summaries"
              value={summarySearch}
              onChange={(event) => setSummarySearch(event.target.value)}
            />
          </div>

          <div className="summary-list">
            {filteredSummaries.length === 0 && <p className="muted">No summaries yet.</p>}
            {filteredSummaries.map((batch) => (
              <article className="summary-card" key={batch.id}>
                <header>
                  <strong>{intervalLabel(batch.window_seconds)} window</strong>
                  <span>{batch.item_count} items</span>
                </header>
                <p className="summary-time">
                  {prettyDate(batch.window_start)} to {prettyDate(batch.window_end)}
                </p>
                <p className="summary-text">{cleanSummaryText(batch.summary_text)}</p>
              </article>
            ))}
          </div>

          <div className="list-footer-action">
            {hasMoreSummaries ? (
              <button
                type="button"
                onClick={handleLoadMoreSummaries}
                disabled={loadingMoreSummaries}
              >
                {loadingMoreSummaries ? "Loading..." : "Load Older Summaries"}
              </button>
            ) : (
              <p className="muted">Reached oldest available summaries for now.</p>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}

export default NewsFeed;
