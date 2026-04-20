import { useState } from "react";

import { askChat } from "../api";

function ChatAssistant() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    const prompt = question.trim();
    if (!prompt) return;

    setMessages((prev) => [...prev, { role: "user", text: prompt }]);
    setQuestion("");
    setLoading(true);

    try {
      const result = await askChat(prompt);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: result.answer,
          meta: `Context: ${result.used_news_items} records from ${result.window_used}`,
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
      </header>

      <section className="panel chat-panel">
        <div className="chat-stream">
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
            rows={3}
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
