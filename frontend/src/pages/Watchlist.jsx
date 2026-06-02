import { useEffect, useState } from "react";

import {
  createTopic,
  deleteTopic,
  fetchTopics,
  proposeAlertTopic,
  sendTestAlert,
  updateTopic,
} from "../api";

const ALERT_AI_MODEL_ID = "groq_gpt_oss";

function parseKeywordText(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function keywordTextFromList(value) {
  return Array.isArray(value) ? value.join(", ") : "";
}

function Watchlist() {
  const [topics, setTopics] = useState([]);
  const [topicName, setTopicName] = useState("");
  const [keywords, setKeywords] = useState("");
  const [threshold, setThreshold] = useState("MEDIUM");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiProposal, setAiProposal] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [status, setStatus] = useState("");

  async function loadTopics() {
    const rows = await fetchTopics();
    setTopics(rows || []);
  }

  useEffect(() => {
    loadTopics().catch((err) => setStatus(err.message || "Failed to load topics"));
  }, []);

  async function createTopicFromValues({
    topicNameValue,
    keywordList,
    thresholdValue,
    successMessage,
  }) {
    const cleanTopicName = topicNameValue.trim();
    const cleanKeywords = keywordList.map((item) => item.trim()).filter(Boolean);

    if (!cleanTopicName || cleanKeywords.length === 0) {
      setStatus("Topic name and keywords are required");
      return false;
    }

    try {
      await createTopic({
        topic_name: cleanTopicName,
        keywords: cleanKeywords,
        alert_urgency_threshold: thresholdValue,
        active: true,
      });
      setStatus(successMessage);
      await loadTopics();
      return true;
    } catch (err) {
      setStatus(err.message || "Failed to create topic");
      return false;
    }
  }

  async function handleCreate(event) {
    event.preventDefault();
    const created = await createTopicFromValues({
      topicNameValue: topicName,
      keywordList: parseKeywordText(keywords),
      thresholdValue: threshold,
      successMessage: "Alert topic created",
    });

    if (created) {
      setTopicName("");
      setKeywords("");
      setThreshold("MEDIUM");
    }
  }

  async function handleAiDraft(event) {
    event.preventDefault();
    const prompt = aiPrompt.trim();
    if (!prompt) {
      setStatus("Describe the alert you want the AI to draft");
      return;
    }

    setAiLoading(true);
    setAiProposal(null);
    setStatus("");

    try {
      const proposal = await proposeAlertTopic(prompt, ALERT_AI_MODEL_ID);
      setAiProposal(proposal);
      setStatus("Review the AI draft before saving it as an active topic");
    } catch (err) {
      setStatus(err.message || "Failed to draft alert topic");
    } finally {
      setAiLoading(false);
    }
  }

  async function handleCreateFromProposal() {
    if (!aiProposal) return;

    const created = await createTopicFromValues({
      topicNameValue: aiProposal.topic_name || "",
      keywordList: aiProposal.keywords || [],
      thresholdValue: aiProposal.alert_urgency_threshold || "MEDIUM",
      successMessage: "AI alert topic created",
    });

    if (created) {
      setAiProposal(null);
      setAiPrompt("");
    }
  }

  function handleCopyProposalToManual() {
    if (!aiProposal) return;
    setTopicName(aiProposal.topic_name || "");
    setKeywords(keywordTextFromList(aiProposal.keywords));
    setThreshold(aiProposal.alert_urgency_threshold || "MEDIUM");
    setStatus("AI draft copied into the manual form");
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
            Create topics manually or let GPT-OSS draft the topic, keywords, and urgency before saving.
          </p>
          <p className="muted">
            Saved topics are checked against every incoming Telegram message in the backend.
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
        <div className="watchlist-editor-stack">
          <section className="panel topic-form ai-topic-panel">
            <h3>AI Alert Builder</h3>
            <form onSubmit={handleAiDraft} className="ai-topic-form">
              <div className="topic-form-field">
                <label htmlFor="aiPrompt">Describe the alert</label>
                <textarea
                  id="aiPrompt"
                  rows={4}
                  value={aiPrompt}
                  onChange={(event) => setAiPrompt(event.target.value)}
                  placeholder="Create a high urgency alert for Donald Trump, Iran ceasefire, and official statements"
                />
              </div>
              <button className="primary-btn" type="submit" disabled={aiLoading}>
                {aiLoading ? "Drafting..." : "Draft With GPT-OSS 120B"}
              </button>
            </form>

            {aiProposal && (
              <div className="ai-proposal-card">
                <div className="ai-proposal-head">
                  <strong>{aiProposal.topic_name}</strong>
                  <span className="status-dot on">
                    {aiProposal.alert_urgency_threshold || "MEDIUM"}
                  </span>
                </div>
                <p className="muted topic-keywords">
                  Keywords: {keywordTextFromList(aiProposal.keywords)}
                </p>
                {aiProposal.rationale && (
                  <p className="muted">{aiProposal.rationale}</p>
                )}
                <p className="ai-confirm-question">
                  Should I create this active topic?
                </p>
                <div className="topic-actions">
                  <button className="primary-btn" type="button" onClick={handleCreateFromProposal}>
                    Create Topic
                  </button>
                  <button type="button" onClick={handleCopyProposalToManual}>
                    Edit Manually
                  </button>
                  <button type="button" onClick={() => setAiProposal(null)}>
                    Dismiss
                  </button>
                </div>
                <p className="chat-meta">
                  {aiProposal.model_label} | Context: {aiProposal.context_items} records
                </p>
              </div>
            )}
          </section>

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
        </div>

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

                <p className="muted topic-keywords">
                  Keywords: {(topic.keywords || []).join(", ")}
                </p>

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
