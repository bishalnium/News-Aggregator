import { useEffect, useRef, useState } from "react";

import { askChat, fetchChatModels } from "../api";

const FALLBACK_MODELS = [
  { id: "groq_gpt_oss", label: "Groq GPT-OSS 120B" },
  { id: "cerebras_glm_4_7", label: "Cerebras GLM 4.7" },
];

function ChatAssistant() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [models, setModels] = useState(FALLBACK_MODELS);
  const [selectedModelId, setSelectedModelId] = useState("groq_gpt_oss");

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
      const result = await askChat(prompt, selectedModelId);
      const responseMeta = result.used_news_items > 0
        ? `Context: ${result.used_news_items} records from ${result.window_used} | ${result.model_label}`
        : result.model_label;
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: result.answer,
          meta: responseMeta,
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
