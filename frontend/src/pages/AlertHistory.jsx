import { useEffect, useState } from "react";

import { fetchAlerts } from "../api";

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function AlertHistory() {
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchAlerts(200)
      .then((rows) => setAlerts(rows || []))
      .catch((err) => setError(err.message || "Failed to load alerts"));
  }, []);

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>Alert History</h2>
          <p className="muted">Every Telegram alert triggered by NLP keyword matching.</p>
        </div>
      </header>

      {error && <div className="error-note">{error}</div>}

      <section className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Topic</th>
                <th>Urgency</th>
                <th>Summary</th>
                <th>Channel</th>
              </tr>
            </thead>
            <tbody>
              {alerts.length === 0 && (
                <tr>
                  <td colSpan={5}>No alerts sent yet.</td>
                </tr>
              )}
              {alerts.map((item) => (
                <tr key={item.id}>
                  <td>{formatDate(item.sent_at)}</td>
                  <td>{item.topic_name || "Unknown topic"}</td>
                  <td>{item.urgency || "LOW"}</td>
                  <td>{item.news_summary || item.message_text}</td>
                  <td>{item.channel}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

export default AlertHistory;
