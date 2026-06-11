import { useEffect, useState } from "react";

import {
  createTopic,
  deleteTopic,
  fetchTopics,
  proposeAlertTopic,
  sendTestAlert,
  updateTopic,
  fetchContextAlerts,
  createContextAlert,
  updateContextAlert,
  deleteContextAlert,
  proposeContextAlert,
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
  const [activeTab, setActiveTab] = useState("keyword");
  const [status, setStatus] = useState("");

  // Keyword Alerts State
  const [topics, setTopics] = useState([]);
  const [topicName, setTopicName] = useState("");
  const [keywords, setKeywords] = useState("");
  const [threshold, setThreshold] = useState("MEDIUM");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiProposal, setAiProposal] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);

  // Situation Context Alerts State
  const [contextAlerts, setContextAlerts] = useState([]);
  const [customContext, setCustomContext] = useState("");
  const [aiContextPrompt, setAiContextPrompt] = useState("");
  const [proposedContext, setProposedContext] = useState("");
  const [aiContextLoading, setAiContextLoading] = useState(false);

  async function loadTopics() {
    const rows = await fetchTopics();
    setTopics(rows || []);
  }

  async function loadContextAlerts() {
    try {
      const rows = await fetchContextAlerts();
      setContextAlerts(rows || []);
    } catch (err) {
      setStatus(err.message || "Failed to load context alerts");
    }
  }

  useEffect(() => {
    loadTopics().catch((err) => setStatus(err.message || "Failed to load topics"));
    loadContextAlerts();
  }, []);

  // ----------------------------------------------------
  // Keyword Alert Handlers
  // ----------------------------------------------------
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

  // ----------------------------------------------------
  // Situation Context Alert Handlers
  // ----------------------------------------------------
  async function handleCreateContext(event) {
    event.preventDefault();
    const desc = customContext.trim();
    if (!desc) {
      setStatus("Context description is required");
      return;
    }
    try {
      await createContextAlert({
        context_description: desc,
        active: true,
      });
      setCustomContext("");
      setStatus("Context alert created");
      await loadContextAlerts();
    } catch (err) {
      setStatus(err.message || "Failed to create context alert");
    }
  }

  async function handleAiContextDraft(event) {
    event.preventDefault();
    const inst = aiContextPrompt.trim();
    if (!inst) {
      setStatus("Specify what situation you want alerts for");
      return;
    }
    setAiContextLoading(true);
    setProposedContext("");
    setStatus("");
    try {
      const res = await proposeContextAlert(inst);
      if (res && res.proposed_description) {
        setProposedContext(res.proposed_description);
        setStatus("Review proposed context alert details");
      } else {
        setStatus("Failed to generate context alert proposal");
      }
    } catch (err) {
      setStatus(err.message || "Error generating context proposal");
    } finally {
      setAiContextLoading(false);
    }
  }

  async function handleCreateContextFromProposal() {
    if (!proposedContext) return;
    try {
      await createContextAlert({
        context_description: proposedContext,
        active: true,
      });
      setProposedContext("");
      setAiContextPrompt("");
      setStatus("Context alert created from proposal");
      await loadContextAlerts();
    } catch (err) {
      setStatus(err.message || "Failed to create context alert");
    }
  }

  async function handleToggleContext(alert) {
    try {
      await updateContextAlert(alert.id, { active: !alert.active });
      await loadContextAlerts();
    } catch (err) {
      setStatus(err.message || "Failed to toggle context alert");
    }
  }

  async function handleDeleteContext(alertId) {
    try {
      await deleteContextAlert(alertId);
      await loadContextAlerts();
    } catch (err) {
      setStatus(err.message || "Failed to delete context alert");
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>Alert Setup</h2>
          <p className="muted">
            Configure keyword-matching fast alerts or phrase/context-based situational verification alerts.
          </p>
        </div>
        <button type="button" onClick={handleTestAlert}>
          Send Test Alert
        </button>
      </header>

      <div className="tab-switcher">
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
          Situation Context Alerts
        </button>
      </div>

      {status && <div className="banner-note">{status}</div>}

      {activeTab === "keyword" ? (
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
      ) : (
        <div className="watchlist-grid">
          <div className="watchlist-editor-stack">
            <section className="panel topic-form ai-topic-panel">
              <h3>AI Context Builder</h3>
              <p className="muted" style={{ marginBottom: "1rem", fontSize: "0.85rem" }}>
                Describe what situation you want alerts for. GLM 4.7 will draft a clear description to match against incoming news.
              </p>
              <form onSubmit={handleAiContextDraft} className="ai-topic-form">
                <div className="topic-form-field">
                  <label htmlFor="aiContextPrompt">Situation Instruction</label>
                  <textarea
                    id="aiContextPrompt"
                    rows={4}
                    value={aiContextPrompt}
                    onChange={(event) => setAiContextPrompt(event.target.value)}
                    placeholder="e.g. notify me of major interest rate hikes by the Federal Reserve"
                    disabled={aiContextLoading}
                  />
                </div>
                <button className="primary-btn" type="submit" disabled={aiContextLoading}>
                  {aiContextLoading ? "Drafting..." : "Draft with GLM 4.7"}
                </button>
              </form>

              {proposedContext && (
                <div className="ai-proposal-card">
                  <div className="ai-proposal-head">
                    <strong>Proposed Situation Description:</strong>
                  </div>
                  <p className="proposed-context-text" style={{ margin: "0.5rem 0", fontSize: "0.95rem", lineHeight: "1.4", color: "var(--text-color)" }}>
                    {proposedContext}
                  </p>
                  <div className="topic-actions" style={{ marginTop: "1rem" }}>
                    <button className="primary-btn" type="button" onClick={handleCreateContextFromProposal}>
                      Create Alert
                    </button>
                    <button type="button" onClick={() => { setCustomContext(proposedContext); setProposedContext(""); }}>
                      Edit Manually
                    </button>
                    <button type="button" onClick={() => setProposedContext("")}>
                      Dismiss
                    </button>
                  </div>
                </div>
              )}
            </section>

            <form className="panel topic-form" onSubmit={handleCreateContext}>
              <h3>Create Context Alert</h3>
              <p className="muted" style={{ marginBottom: "1rem", fontSize: "0.85rem" }}>
                Define the situation description manually. Keep it descriptive so the LLMs can precisely verify matches.
              </p>

              <div className="topic-form-field">
                <label htmlFor="customContext">Situation Description</label>
                <textarea
                  id="customContext"
                  rows={4}
                  value={customContext}
                  onChange={(event) => setCustomContext(event.target.value)}
                  placeholder="e.g. The European Central Bank announces a rate hike of 50 basis points or more to curb rising inflation"
                />
              </div>

              <button className="primary-btn" type="submit">
                Create Alert
              </button>
            </form>
          </div>

          <section className="panel">
            <h3>Active Situation Alerts</h3>
            <p className="muted" style={{ marginBottom: "1rem", fontSize: "0.85rem" }}>
              Every incoming news item is analyzed by GPT-OSS, double-checked by GLM 4.7, and alerts are instantly routed to Telegram.
            </p>
            <div className="topic-list">
              {contextAlerts.length === 0 && <p className="muted">No context alerts created yet.</p>}
              {contextAlerts.map((alert) => (
                <article className="topic-card" key={alert.id}>
                  <header>
                    <span className={alert.active ? "status-dot on" : "status-dot off"}>
                      {alert.active ? "Active" : "Paused"}
                    </span>
                  </header>

                  <p className="topic-context-desc" style={{ fontSize: "0.95rem", lineHeight: "1.4", margin: "0.5rem 0", color: "var(--text-color)" }}>
                    {alert.context_description}
                  </p>

                  <div className="topic-actions" style={{ justifyContent: "flex-end", gap: "0.5rem" }}>
                    <button type="button" onClick={() => handleToggleContext(alert)}>
                      {alert.active ? "Pause" : "Resume"}
                    </button>

                    <button type="button" className="danger-btn" onClick={() => handleDeleteContext(alert.id)}>
                      Delete
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

export default Watchlist;
