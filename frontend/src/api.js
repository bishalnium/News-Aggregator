const API_BASE = import.meta.env.VITE_API_URL || (typeof window !== 'undefined' && window.location.protocol === 'https:' ? '' : 'http://localhost:8000');
const API_PREFIX = import.meta.env.VITE_API_URL ? `${API_BASE}/api` : (typeof window !== 'undefined' && window.location.protocol === 'https:' ? '/backend/api' : `${API_BASE}/api`);


async function request(path, options = {}) {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {
      // Ignore JSON parsing errors.
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export function getWebSocketUrl() {
  if (typeof window !== 'undefined' && window.location.protocol === 'https:' && !import.meta.env.VITE_API_URL) {
    return `wss://${window.location.host}/backend/api/ws/live`;
  }
  const wsBase = API_BASE.replace("http://", "ws://").replace("https://", "wss://");
  return `${wsBase}/api/ws/live`;
}


export function fetchNews(params = {}) {
  const searchParams = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.append(key, String(value));
    }
  });
  const query = searchParams.toString();
  return request(`/news${query ? `?${query}` : ""}`);
}

export function fetchSummaryBatches(limit = 50) {
  return request(`/settings/summary-batches?limit=${limit}`);
}

export function getSettings() {
  return request("/settings");
}

export function setSummaryInterval(intervalSeconds) {
  return request("/settings/summary-interval", {
    method: "POST",
    body: JSON.stringify({ interval_seconds: intervalSeconds }),
  });
}

export function sendTestAlert() {
  return request("/settings/test-alert", {
    method: "POST",
  });
}

export function fetchTopics() {
  return request("/topics");
}

export function createTopic(payload) {
  return request("/topics", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function proposeAlertTopic(message, modelId = "groq_gpt_oss") {
  return request("/topics/ai-proposal", {
    method: "POST",
    body: JSON.stringify({ message, model_id: modelId }),
  });
}

export function updateTopic(topicId, payload) {
  return request(`/topics/${topicId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteTopic(topicId) {
  return request(`/topics/${topicId}`, {
    method: "DELETE",
  });
}

export function fetchAlerts(limit = 120) {
  return request(`/alerts?limit=${limit}`);
}

export function fetchChatModels() {
  return request("/chat/models");
}

export function askChat(message, modelId) {
  return request("/chat", {
    method: "POST",
    body: JSON.stringify({ message, model_id: modelId }),
  });
}
