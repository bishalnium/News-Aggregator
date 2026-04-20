import { useEffect, useState } from "react";

import {
  createTopic,
  deleteTopic,
  fetchTopics,
  sendTestAlert,
  updateTopic,
} from "../api";

function Watchlist() {
  const [topics, setTopics] = useState([]);
  const [topicName, setTopicName] = useState("");
  const [keywords, setKeywords] = useState("");
  const [threshold, setThreshold] = useState("MEDIUM");
  const [status, setStatus] = useState("");

  async function loadTopics() {
    const rows = await fetchTopics();
    setTopics(rows || []);
  }

  useEffect(() => {
    loadTopics().catch((err) => setStatus(err.message || "Failed to load topics"));
  }, []);

  async function handleCreate(event) {
    event.preventDefault();
    const parsedKeywords = keywords
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

    if (!topicName.trim() || parsedKeywords.length === 0) {
      setStatus("Topic name and keywords are required");
      return;
    }

    try {
      await createTopic({
        topic_name: topicName,
        keywords: parsedKeywords,
        alert_urgency_threshold: threshold,
        active: true,
      });
      setTopicName("");
      setKeywords("");
      setThreshold("MEDIUM");
      setStatus("Alert topic created");
      await loadTopics();
    } catch (err) {
      setStatus(err.message || "Failed to create topic");
    }
  }

  async function handleToggle(topic) {
    try {
      await updateTopic(topic.id, { active: !topic.active });
      await loadTopics();
    } catch (err) {
      setStatus(err.message || "Failed to toggle topic");
    }
  }

  async function handleThreshold(topic, nextThreshold) {
    try {
      await updateTopic(topic.id, { alert_urgency_threshold: nextThreshold });
      await loadTopics();
    } catch (err) {
      setStatus(err.message || "Failed to update threshold");
    }
  }

  async function handleDelete(topicId) {
    try {
      await deleteTopic(topicId);
      await loadTopics();
    } catch (err) {
      setStatus(err.message || "Failed to delete topic");
    }
  }

  async function handleTestAlert() {
    try {
      const response = await sendTestAlert();
      setStatus(response.message || "Alert bot test sent");
    } catch (err) {
      setStatus(err.message || "Failed to send test alert");
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>Alert Setup</h2>
          <p className="muted">
            Separate NLP alert engine. No AI model required for trigger matching.
          </p>
          <p className="muted">
            Use a separate bot by setting ALERT_BOT_TOKEN and ALERT_CHAT_ID in backend/.env.
          </p>
        </div>
        <button type="button" onClick={handleTestAlert}>
          Send Test Alert
        </button>
      </header>

      {status && <div className="banner-note">{status}</div>}

      <div className="watchlist-grid">
        <form className="panel topic-form" onSubmit={handleCreate}>
          <h3>Create Alert Topic</h3>

          <div className="topic-form-field">
            <label htmlFor="topicName">Topic</label>
            <input
              id="topicName"
              value={topicName}
              onChange={(event) => setTopicName(event.target.value)}
              placeholder="Russia Ukraine war"
            />
          </div>

          <div className="topic-form-field">
            <label htmlFor="keywords">Keywords (comma separated)</label>
            <textarea
              id="keywords"
              rows={4}
              value={keywords}
              onChange={(event) => setKeywords(event.target.value)}
              placeholder="russia, ukraine, ceasefire, missile, peace talks"
            />
          </div>

          <div className="topic-form-footer">
            <div className="topic-form-field topic-form-threshold">
              <label htmlFor="threshold">Minimum urgency</label>
              <select
                id="threshold"
                value={threshold}
                onChange={(event) => setThreshold(event.target.value)}
              >
                <option value="LOW">LOW</option>
                <option value="MEDIUM">MEDIUM</option>
                <option value="HIGH">HIGH</option>
              </select>
            </div>

            <button className="primary-btn" type="submit">
              Create Topic
            </button>
          </div>
        </form>

        <section className="panel">
          <h3>Active Topics</h3>
          <div className="topic-list">
            {topics.length === 0 && <p className="muted">No topics created yet.</p>}
            {topics.map((topic) => (
              <article className="topic-card" key={topic.id}>
                <header>
                  <strong>{topic.topic_name}</strong>
                  <span className={topic.active ? "status-dot on" : "status-dot off"}>
                    {topic.active ? "Active" : "Paused"}
                  </span>
                </header>

                <p className="muted">Keywords: {topic.keywords.join(", ")}</p>

                <div className="topic-actions">
                  <select
                    value={topic.alert_urgency_threshold}
                    onChange={(event) => handleThreshold(topic, event.target.value)}
                  >
                    <option value="LOW">LOW</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="HIGH">HIGH</option>
                  </select>

                  <button type="button" onClick={() => handleToggle(topic)}>
                    {topic.active ? "Pause" : "Resume"}
                  </button>

                  <button type="button" className="danger-btn" onClick={() => handleDelete(topic.id)}>
                    Delete
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

export default Watchlist;
