import { useEffect, useState } from "react";
import { getProxyStatus, toggleProxy, sendTestAlert, getFcmPreferences, updateFcmPreferences } from "../api";

function Settings() {
  const [proxyStatus, setProxyStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [statusMsg, setStatusMsg] = useState("");
  const [isError, setIsError] = useState(false);
  const [testingKeyword, setTestingKeyword] = useState(false);
  const [testingContext, setTestingContext] = useState(false);

  // Mobile push preferences states
  const [fcmToken, setFcmToken] = useState(null);
  const [pushKeyword, setPushKeyword] = useState(true);
  const [pushContext, setPushContext] = useState(true);
  const [loadingPrefs, setLoadingPrefs] = useState(false);

  useEffect(() => {
    fetchProxyStatus();

    // Check if we are running in the Android App wrapper with Javascript interface
    if (window.AndroidInterface && typeof window.AndroidInterface.getFcmToken === "function") {
      const token = window.AndroidInterface.getFcmToken();
      if (token) {
        setFcmToken(token);
        fetchFcmPreferences(token);
      } else {
        // Retry in case token is still loading async
        const interval = setInterval(() => {
          const t = window.AndroidInterface.getFcmToken();
          if (t) {
            setFcmToken(t);
            fetchFcmPreferences(t);
            clearInterval(interval);
          }
        }, 1000);
        return () => clearInterval(interval);
      }
    }
  }, []);

  async function fetchProxyStatus() {
    try {
      setLoading(true);
      const data = await getProxyStatus();
      setProxyStatus(data);
    } catch (err) {
      showStatus(err.message || "Failed to load proxy status", true);
    } finally {
      setLoading(false);
    }
  }

  async function fetchFcmPreferences(token) {
    try {
      setLoadingPrefs(true);
      const prefs = await getFcmPreferences(token);
      if (prefs) {
        setPushKeyword(prefs.push_keyword);
        setPushContext(prefs.push_context);
      }
    } catch (err) {
      console.error("Failed to load FCM preferences", err);
    } finally {
      setLoadingPrefs(false);
    }
  }

  async function handleToggleFcm(type) {
    if (!fcmToken) return;
    const nextKeyword = type === "keyword" ? !pushKeyword : pushKeyword;
    const nextContext = type === "context" ? !pushContext : pushContext;
    
    // Optimistic UI update
    if (type === "keyword") setPushKeyword(nextKeyword);
    else setPushContext(nextContext);

    try {
      await updateFcmPreferences(fcmToken, nextKeyword, nextContext);
      showStatus("Notification settings updated.");
    } catch (err) {
      // Revert state on error
      if (type === "keyword") setPushKeyword(!nextKeyword);
      else setPushContext(!nextContext);
      showStatus("Failed to update notification settings.", true);
    }
  }

  function showStatus(msg, error = false) {
    setStatusMsg(msg);
    setIsError(error);
    // Auto-clear message after 5 seconds
    setTimeout(() => {
      setStatusMsg("");
    }, 5000);
  }

  async function handleToggle() {
    if (!proxyStatus) return;
    const nextState = !proxyStatus.proxy_enabled;
    try {
      // Optimistic update
      setProxyStatus((prev) => ({ ...prev, proxy_enabled: nextState }));
      const response = await toggleProxy(nextState);
      if (response && response.ok) {
        showStatus(response.message || "Proxy setting updated successfully!");
      } else {
        throw new Error(response.message || "Failed to toggle proxy");
      }
    } catch (err) {
      // Revert state on error
      setProxyStatus((prev) => ({ ...prev, proxy_enabled: !nextState }));
      showStatus(err.message || "Error toggling proxy", true);
    }
  }

  async function handleTest(type) {
    if (type === "keyword") {
      setTestingKeyword(true);
    } else {
      setTestingContext(true);
    }
    try {
      const response = await sendTestAlert(type);
      showStatus(response.message || `Test alert delivered to ${type} bot.`);
    } catch (err) {
      showStatus(err.message || `Failed to send test alert to ${type} bot.`, true);
    } finally {
      if (type === "keyword") {
        setTestingKeyword(false);
      } else {
        setTestingContext(false);
      }
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h2>System Settings</h2>
          <p className="muted">
            Configure system settings, proxy controls, and verify bot connectivity.
          </p>
        </div>
      </header>

      {statusMsg && (
        <div className={`banner-note ${isError ? "error" : "success"}`} style={{
          backgroundColor: isError ? "rgba(214, 40, 40, 0.1)" : "rgba(0, 133, 122, 0.1)",
          color: isError ? "var(--high)" : "var(--brand-strong)",
          borderColor: isError ? "rgba(214, 40, 40, 0.2)" : "rgba(0, 133, 122, 0.2)",
          marginBottom: "16px",
          padding: "12px 16px",
          borderRadius: "8px",
          border: "1px solid",
          fontWeight: 500,
          fontSize: "0.9rem"
        }}>
          {statusMsg}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: "24px", marginTop: "16px" }}>
        <section className="panel" style={{ maxWidth: "650px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <div>
              <h3 style={{ margin: 0 }}>Telegram Proxy (SOCKS5)</h3>
              <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.88rem" }}>
                Reroute Telegram ingestion and notifications through a Japan SOCKS5 proxy to bypass local blocks.
              </p>
            </div>
            {loading ? (
              <div className="muted" style={{ fontSize: "0.9rem" }}>Loading...</div>
            ) : (
              <div className="proxy-toggle-wrap">
                <button
                  type="button"
                  onClick={handleToggle}
                  className={`toggle-switch ${proxyStatus?.proxy_enabled ? "on" : "off"}`}
                  aria-label="Toggle SOCKS5 Proxy"
                >
                  <span className="toggle-slider"></span>
                  <span className="toggle-label">{proxyStatus?.proxy_enabled ? "ON" : "OFF"}</span>
                </button>
              </div>
            )}
          </div>

          {proxyStatus && (
            <div className="proxy-info-grid">
              <div>
                <div className="info-label">Proxy Host</div>
                <div className="info-value">
                  {proxyStatus.proxy_host || "Not Set"}
                </div>
              </div>
              <div>
                <div className="info-label">Proxy Port</div>
                <div className="info-value">
                  {proxyStatus.proxy_port || "Not Set"}
                </div>
              </div>
              <div>
                <div className="info-label">Proxy Type</div>
                <div className="info-value">
                  {proxyStatus.proxy_type ? proxyStatus.proxy_type.toUpperCase() : "SOCKS5"}
                </div>
              </div>
              <div>
                <div className="info-label">Status</div>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "4px" }}>
                  <span className={`status-dot ${proxyStatus.proxy_enabled ? "on" : "off"}`}>
                    {proxyStatus.proxy_enabled ? "Active" : "Inactive"}
                  </span>
                </div>
              </div>
            </div>
          )}
        </section>

        {fcmToken && (
          <section className="panel" style={{ maxWidth: "650px" }}>
            <h3>Mobile Notification Preferences</h3>
            <p className="muted" style={{ marginBottom: "16px" }}>
              Configure which alerts trigger push notifications on this device.
            </p>
            {loadingPrefs ? (
              <div className="muted" style={{ fontSize: "0.9rem" }}>Loading preferences...</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <h4 style={{ margin: 0, fontSize: "0.95rem" }}>Keyword Alerts</h4>
                    <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.82rem" }}>
                      Receive notifications when critical keywords are found in headlines.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleToggleFcm("keyword")}
                    className={`toggle-switch ${pushKeyword ? "on" : "off"}`}
                    aria-label="Toggle Keyword Push Notifications"
                  >
                    <span className="toggle-slider"></span>
                    <span className="toggle-label">{pushKeyword ? "ON" : "OFF"}</span>
                  </button>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--line)", paddingTop: "16px" }}>
                  <div>
                    <h4 style={{ margin: 0, fontSize: "0.95rem" }}>Context / Situation Alerts</h4>
                    <p className="muted" style={{ margin: "4px 0 0 0", fontSize: "0.82rem" }}>
                      Receive alerts when complex contextual intelligence or risk patterns are identified.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleToggleFcm("context")}
                    className={`toggle-switch ${pushContext ? "on" : "off"}`}
                    aria-label="Toggle Context Push Notifications"
                  >
                    <span className="toggle-slider"></span>
                    <span className="toggle-label">{pushContext ? "ON" : "OFF"}</span>
                  </button>
                </div>
              </div>
            )}
          </section>
        )}

        <section className="panel" style={{ maxWidth: "650px" }}>
          <h3>Connectivity Diagnostics</h3>
          <p className="muted" style={{ marginBottom: "16px" }}>
            Send a silent test notification payload to verify if the alert bots are online and receiving data.
          </p>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            <button
              type="button"
              className="primary-btn"
              onClick={() => handleTest("keyword")}
              disabled={testingKeyword}
            >
              {testingKeyword ? "Sending..." : "Test Keyword Bot"}
            </button>
            <button
              type="button"
              className="primary-btn"
              onClick={() => handleTest("context")}
              disabled={testingContext}
              style={{ background: "var(--surface)", border: "1px solid var(--brand)", color: "var(--brand)" }}
            >
              {testingContext ? "Sending..." : "Test Context Bot"}
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}

export default Settings;
