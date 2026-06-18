import { useEffect, useRef, useState } from "react";

import { askChat, fetchChatModels } from "../api";

const FALLBACK_MODELS = [
  { id: "groq_gpt_oss", label: "Groq GPT-OSS 120B" },
  { id: "cerebras_glm_4_7", label: "Cerebras GLM 4.7" },
];

const getInitialTimes = () => {
  const now = new Date();
  const past24h = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  
  const formatLocal = (date) => {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };
  
  return {
    now: formatLocal(now),
    past24h: formatLocal(past24h)
  };
};

function ChatAssistant() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [models, setModels] = useState(FALLBACK_MODELS);
  const [selectedModelId, setSelectedModelId] = useState("groq_gpt_oss");

  // RAG timeframe & keyword settings
  const initialTimes = getInitialTimes();
  const [timeframeMode, setTimeframeMode] = useState("dynamic");
  const [startTime, setStartTime] = useState(initialTimes.past24h);
  const [endTime, setEndTime] = useState(initialTimes.now);
  const [enableSearch, setEnableSearch] = useState(true);

  const chatStreamRef = useRef(null);

  // Auto-scroll to bottom of the chat stream
  useEffect(() => {
    if (chatStreamRef.current) {
      chatStreamRef.current.scrollTop = chatStreamRef.current.scrollHeight;
    }
  }, [messages, loading]);

  useEffect(() => {
    let cancelled = false;

    fetchChatModels()
      .then((rows) => {
        if (cancelled || !Array.isArray(rows) || rows.length === 0) return;
        setModels(rows);
        setSelectedModelId((current) => {
          if (rows.some((item) => item.id === current)) return current;
          return rows[0].id;
        });
      })
      .catch(() => {
        if (!cancelled) {
          setModels(FALLBACK_MODELS);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    const prompt = question.trim();
    if (!prompt) return;

    const selectedModel = models.find((item) => item.id === selectedModelId);

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        text: prompt,
        meta: selectedModel ? `Model: ${selectedModel.label}` : "",
      },
    ]);
    setQuestion("");
    setLoading(true);

    try {
      const result = await askChat(
        prompt,
        selectedModelId,
        timeframeMode,
        timeframeMode === "custom" ? startTime : null,
        timeframeMode === "custom" ? endTime : null,
        enableSearch
      );
      const responseMeta = result.used_news_items > 0
        ? `Context: ${result.used_news_items} records from ${result.window_used} | ${result.model_label}`
        : result.model_label;
        
      const keywordsMeta = result.keywords_used && result.keywords_used.length > 0
        ? ` | Keywords: ${result.keywords_used.join(", ")}`
        : "";

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: result.answer,
          meta: responseMeta + keywordsMeta,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: err.message || "Chat failed",
          meta: "",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>AI Chat Over Stored News</h2>
          <p className="muted">
            Ask historical questions like: what happened last week in gold, inflation, or war headlines?
          </p>
        </div>
        <div className="chat-model-picker">
          <label htmlFor="chatModel">Model</label>
          <select
            id="chatModel"
            value={selectedModelId}
            onChange={(event) => setSelectedModelId(event.target.value)}
            disabled={loading}
          >
            {models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* Timeframe & RAG Settings Bar */}
      <div className="chat-settings-bar" style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "16px",
        alignItems: "center",
        backgroundColor: "var(--panel)",
        border: "1px solid var(--line)",
        borderRadius: "12px",
        padding: "12px 16px",
        marginBottom: "16px"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--muted)" }}>Timeframe Scope:</span>
          <select
            value={timeframeMode}
            onChange={(e) => setTimeframeMode(e.target.value)}
            style={{
              padding: "6px 12px",
              borderRadius: "8px",
              border: "1px solid var(--line)",
              background: "var(--surface)",
              color: "var(--ink)",
              fontSize: "0.88rem",
              fontWeight: 500,
              cursor: "pointer"
            }}
          >
            <option value="dynamic">💡 Auto-detect from message</option>
            <option value="all">🌐 Search Entire Database (All Time)</option>
            <option value="custom">📅 Custom Date-Time Range</option>
          </select>
        </div>

        {timeframeMode === "custom" && (
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px" }}>
            <input
              type="datetime-local"
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              style={{
                padding: "6px 8px",
                borderRadius: "8px",
                border: "1px solid var(--line)",
                background: "var(--surface)",
                color: "var(--ink)",
                fontSize: "0.85rem"
              }}
            />
            <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>to</span>
            <input
              type="datetime-local"
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              style={{
                padding: "6px 8px",
                borderRadius: "8px",
                border: "1px solid var(--line)",
                background: "var(--surface)",
                color: "var(--ink)",
                fontSize: "0.85rem"
              }}
            />
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginLeft: "auto" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "0.85rem", cursor: "pointer", userSelect: "none" }}>
            <input
              type="checkbox"
              checked={enableSearch}
              onChange={(e) => setEnableSearch(e.target.checked)}
              style={{ width: "16px", height: "16px", accentColor: "var(--brand)" }}
            />
            <span>Enable Keyword Search RAG</span>
          </label>
        </div>
      </div>

      <section className="panel chat-panel">
        <div className="chat-stream" ref={chatStreamRef}>
          {messages.length === 0 && (
            <p className="muted">No messages yet. Ask your first question.</p>
          )}
          {messages.map((item, index) => (
            <article
              key={`${item.role}-${index}`}
              className={`chat-bubble ${item.role === "user" ? "chat-user" : "chat-assistant"}`}
            >
              <p>{item.text}</p>
              {item.meta && <p className="chat-meta">{item.meta}</p>}
            </article>
          ))}
          {loading && <p className="muted">Thinking with database context...</p>}
        </div>

        <form onSubmit={handleSubmit} className="chat-input-wrap">
          <textarea
            rows={2}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Example: what happened in the Russia Ukraine conflict in the last 2 months?"
          />
          <button className="primary-btn" type="submit" disabled={loading}>
            Send
          </button>
        </form>
      </section>
    </section>
  );
}

export default ChatAssistant;
