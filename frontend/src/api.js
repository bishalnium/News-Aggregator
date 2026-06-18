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

export function sendTestAlert(alertType = "keyword") {
  return request(`/settings/test-alert?alert_type=${alertType}`, {
    method: "POST",
  });
}

export function getProxyStatus() {
  return request("/settings/proxy");
}

export function toggleProxy(enabled) {
  return request("/settings/proxy-toggle", {
    method: "POST",
    body: JSON.stringify({ enabled }),
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

export function proposeAlertTopic(message, modelId = "cerebras_glm_4_7") {
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

export function fetchAlerts(limit = 120, alertType = null) {
  let url = `/alerts?limit=${limit}`;
  if (alertType) {
    url += `&alert_type=${alertType}`;
  }
  return request(url);
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

export function verifyPasscode(passcode) {
  return request("/settings/verify-passcode", {
    method: "POST",
    body: JSON.stringify({ passcode }),
  });
}

export function verifyBypassToken(token) {
  return request("/settings/verify-bypass", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function registerFcmToken(fcmToken, deviceName = null) {
  return request("/settings/register-fcm-token", {
    method: "POST",
    body: JSON.stringify({ fcm_token: fcmToken, device_name: deviceName }),
  });
}

export function fetchContextAlerts() {
  return request("/topics/context");
}

export function createContextAlert(payload) {
  return request("/topics/context", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateContextAlert(alertId, payload) {
  return request(`/topics/context/${alertId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteContextAlert(alertId) {
  return request(`/topics/context/${alertId}`, {
    method: "DELETE",
  });
}

export function proposeContextAlert(instruction) {
  return request("/topics/context/ai-proposal", {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

export function getFcmPreferences(token) {
  return request(`/settings/fcm-preferences?token=${encodeURIComponent(token)}`);
}

export function updateFcmPreferences(token, pushKeyword, pushContext) {
  return request("/settings/fcm-preferences", {
    method: "POST",
    body: JSON.stringify({
      fcm_token: token,
      push_keyword: pushKeyword,
      push_context: pushContext,
    }),
  });
}

