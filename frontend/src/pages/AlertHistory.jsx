import { useEffect, useState } from "react";
import { fetchAlerts } from "../api";

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function cleanHtml(str) {
  if (!str) return "";
  return str.replace(/<[^>]*>/g, "").trim();
}

function parseAlertMessage(item) {
  const text = item.message_text || "";

  if (item.channel === "telegram-context") {
    // Context Alerts:
    // Format: 🎯 <b>SITUATION ALERT</b>\n\n<b>Summary:</b>\n- summary...\n\n<b>Matched Alert:</b> description
    const matchIndex = text.indexOf("<b>Matched Alert:</b>");
    let matchedAlert = "";
    let summary = item.news_summary || "";

    if (matchIndex !== -1) {
      matchedAlert = cleanHtml(text.substring(matchIndex + "<b>Matched Alert:</b>".length));
    } else {
      const altMatchIndex = text.indexOf("Matched Alert:");
      if (altMatchIndex !== -1) {
        matchedAlert = cleanHtml(text.substring(altMatchIndex + "Matched Alert:".length));
      }
    }

    if (!summary) {
      const summaryStart = text.indexOf("<b>Summary:</b>");
      if (summaryStart !== -1) {
        const summaryEnd = matchIndex !== -1 ? matchIndex : text.length;
        summary = cleanHtml(text.substring(summaryStart + "<b>Summary:</b>".length, summaryEnd));
      }
    }

    return {
      title: "Situation Context Match",
      matchedAlert: matchedAlert || "Context Match",
      summary: summary || cleanHtml(text)
    };
  } else {
    // Keyword Alerts:
    // Format: <b>⚡ INSTANT ALERT</b>\n<b>Topic:</b> name\n<b>Matched Keywords:</b> word1, word2\n<b>Message:</b>\nraw_text
    const keywordIndex = text.indexOf("<b>Matched Keywords:</b>");
    const messageIndex = text.indexOf("<b>Message:</b>");
    let matchedKeywords = "";
    let rawMessage = "";

    if (keywordIndex !== -1) {
      const endIdx = messageIndex !== -1 ? messageIndex : text.length;
      matchedKeywords = cleanHtml(text.substring(keywordIndex + "<b>Matched Keywords:</b>".length, endIdx));
    }

    if (messageIndex !== -1) {
      rawMessage = cleanHtml(text.substring(messageIndex + "<b>Message:</b>".length));
    }

    return {
      title: item.topic_name || "Keyword Alert",
      matchedKeywords: matchedKeywords,
      summary: item.news_summary || rawMessage || cleanHtml(text)
    };
  }
}

function AlertHistory() {
  const [alerts, setAlerts] = useState([]);
  const [activeTab, setActiveTab] = useState("all"); // "all", "keyword", "context"
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadAlerts();
  }, [activeTab]);

  async function loadAlerts() {
    try {
      setLoading(true);
      setError("");
      // Map frontend tab names to backend API filter parameter
      const apiType = activeTab === "all" ? null : activeTab;
      const data = await fetchAlerts(200, apiType);
      setAlerts(data || []);
    } catch (err) {
      setError(err.message || "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }

  function getChannelBadge(channel) {
    switch (channel) {
      case "telegram-instant":
        return <span className="alert-channel-badge instant">⚡ Instant</span>;
      case "telegram-alert":
        return <span className="alert-channel-badge alert">🔔 Alert</span>;
      case "telegram-signal":
        return <span className="alert-channel-badge signal">📡 Signal</span>;
      case "telegram-context":
        return <span className="alert-channel-badge context">🎯 Context</span>;
      default:
        return <span className="alert-channel-badge default">{channel}</span>;
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>Alert History</h2>
          <p className="muted">
            Chronological archive of all notifications dispatched to Telegram.
          </p>
        </div>
      </header>

      <div className="tab-switcher">
        <button
          className={activeTab === "all" ? "active" : ""}
          onClick={() => setActiveTab("all")}
        >
          All Alerts
        </button>
        <button
          className={activeTab === "keyword" ? "active" : ""}
          onClick={() => setActiveTab("keyword")}
        >
          Keyword Alerts
        </button>
        <button
          className={activeTab === "context" ? "active" : ""}
          onClick={() => setActiveTab("context")}
        >
          Context Alerts
        </button>
      </div>

      {error && <div className="banner-note error">{error}</div>}

      {loading ? (
        <div className="loading-state">
          <p className="muted">Loading alert archive...</p>
        </div>
      ) : alerts.length === 0 ? (
        <div className="empty-state-panel">
          <div className="empty-state-icon">📭</div>
          <h3>No Alerts Found</h3>
          <p className="muted">
            {activeTab === "all"
              ? "No alerts have been recorded yet."
              : `No ${activeTab} alerts matched the criteria in the selected view.`}
          </p>
        </div>
      ) : (
        <div className="alerts-grid">
          {alerts.map((item) => {
            const parsed = parseAlertMessage(item);
            return (
              <article key={item.id} className="alert-history-card">
                <div className="alert-card-header">
                  <div className="alert-card-title-group">
                    <h4 className="alert-card-title">{parsed.title}</h4>
                    <div className="alert-card-badges">
                      {getChannelBadge(item.channel)}
                      {item.urgency && (
                        <span className={`urgency-tag ${item.urgency.toLowerCase()}`}>
                          {item.urgency}
                        </span>
                      )}
                    </div>
                  </div>
                  <time className="alert-card-time">{formatDate(item.sent_at)}</time>
                </div>

                <div className="alert-card-body">
                  <div className="alert-card-section summary-section">
                    <span className="section-label">Summary / Content</span>
                    <p className="section-text">{parsed.summary}</p>
                  </div>

                  {parsed.matchedKeywords && (
                    <div className="alert-card-section keywords-section">
                      <span className="section-label">Matched Keywords</span>
                      <p className="keyword-hits">{parsed.matchedKeywords}</p>
                    </div>
                  )}

                  {parsed.matchedAlert && (
                    <div className="alert-card-section context-section">
                      <span className="section-label">Matched Situation Context Rule</span>
                      <div className="context-rule-box">
                        <span className="context-rule-icon">🎯</span>
                        <p className="context-rule-text">{parsed.matchedAlert}</p>
                      </div>
                    </div>
                  )}
                </div>

                {item.news_id && (
                  <div className="alert-card-footer">
                    <span className="news-id-label">News ID: #{item.news_id}</span>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default AlertHistory;
