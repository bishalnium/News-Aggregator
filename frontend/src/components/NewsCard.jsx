function formatRelativeTime(value) {
  if (!value) return "unknown";
  const now = Date.now();
  const ts = new Date(value).getTime();
  const diff = Math.max(1, Math.floor((now - ts) / 1000));

  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function cleanText(value) {
  if (!value) return "";
  return value
    .replace(/\.{3,}|…+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function urgencyClass(urgency) {
  if (urgency === "HIGH") return "urgency-chip urgency-high";
  if (urgency === "MEDIUM") return "urgency-chip urgency-medium";
  return "urgency-chip urgency-low";
}

function NewsCard({ item }) {
  const isProvisional = Boolean(item.provisional);
  const normalizedRaw = cleanText(item.raw_text || "");
  const normalizedSummary = cleanText(item.summary || "");
  const showSummary = Boolean(
    normalizedSummary
    && !isProvisional
    && normalizedSummary.toLowerCase() !== normalizedRaw.toLowerCase()
  );

  return (
    <article className="news-card">
      <header className="news-card-head">
        <span className="news-time-badge">{formatRelativeTime(item.fetched_at)}</span>
        <span
          className={urgencyClass(item.urgency)}
          title={
            isProvisional
              ? "Live incoming news item. AI classification and summary enrichment are in progress."
              : `Urgency Level: ${item.urgency || "LOW"}. AI-determined level representing expected market impact or geopolitical significance: LOW (routine reports/updates), MEDIUM (market-moving statements/data), HIGH (extreme emergency events/major attacks).`
          }
        >
          {isProvisional ? "LIVE" : (item.urgency || "LOW")}
        </span>
      </header>

      <p className="news-meta">
        <strong>{item.source_channel || "unknown channel"}</strong>
      </p>

      <p className="news-raw">{normalizedRaw}</p>

      {isProvisional && (
        <p className="news-summary">Incoming live update. Enriching classification...</p>
      )}

      {showSummary && <p className="news-summary">{normalizedSummary}</p>}

      {item.instruments_affected?.length > 0 && (
        <div className="tag-wrap">
          {item.instruments_affected.map((instrument) => (
            <span key={instrument} className="tag-item">
              {instrument}
            </span>
          ))}
        </div>
      )}

      {item.url && (
        <a className="card-link" href={item.url} target="_blank" rel="noreferrer">
          Open source
        </a>
      )}
    </article>
  );
}

export default NewsCard;
