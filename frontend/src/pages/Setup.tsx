import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api, setToken, setWorkspace } from "../api";
import { Banner, SecretReveal } from "../ui";

export function SetupPage({ needsSetup, onReady }: { needsSetup: boolean; onReady: () => void }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [key, setKey] = useState("");
  const [applicationName, setApplicationName] = useState("");
  const [issued, setIssued] = useState("");
  const [issuedApplication, setIssuedApplication] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [directory, setDirectory] = useState(false);

  useEffect(() => {
    api<{ oidc: boolean }>("/api/settings/auth-public")
      .then((body) => setDirectory(body.oidc))
      .catch(() => setDirectory(false));
  }, []);

  async function createAdmin(event: FormEvent) {
    event.preventDefault();
    const name = applicationName.trim();
    if (!name) {
      setError("Name the application.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      const body = await api<{ plaintext: string; applicationName: string }>("/api/bootstrap", {
        method: "POST",
        body: JSON.stringify({ applicationName: name }),
      });
      setIssued(body.plaintext);
      setIssuedApplication(body.applicationName);
      setToken(body.plaintext);
      setWorkspace("");
      onReady();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Setup failed.");
    } finally {
      setBusy(false);
    }
  }

  function signIn(event: FormEvent) {
    event.preventDefault();
    if (!key.trim()) {
      setError("Paste an admin key.");
      return;
    }
    setToken(key);
    setWorkspace("");
    navigate("/");
  }

  function directorySignIn() {
    window.location.assign("/auth/login");
  }

  return (
    <div className="setup-screen">
      <aside className="setup-aside">
        <svg className="setup-line" viewBox="0 0 220 160" aria-hidden="true">
          <path d="M210 8 C 70 36, 160 96, 8 152" />
        </svg>
        <div className="setup-copy">
          <div className="mark-halo">
            <img className="mark" src="/bollard-mark.png" alt="" />
          </div>
          <p className="eyebrow">Control point</p>
          <h1>Bollard</h1>
          <p>Every model call is made fast here. The person, the key, and the spend stay on one line.</p>
          <ul className="setup-points">
            <li><span>01</span><p>A person gets a key and a monthly budget</p></li>
            <li><span>02</span><p>A route holds the call to one model, with a fallback</p></li>
            <li><span>03</span><p>Audit keeps the prompt, what was sent, and the reply</p></li>
          </ul>
        </div>
      </aside>
      <main className="setup-panel">
        <section className="card setup-card">
          {issued ? (
            <>
              <h2>Save the admin key</h2>
              <p className="lede">This is the only time it is shown. The key belongs to {issuedApplication}.</p>
              <div className="field">
                <SecretReveal label="Admin key" value={issued} />
              </div>
              <div className="modal-actions">
                <button className="primary" type="button" onClick={() => { setWorkspace(""); navigate("/"); }}>
                  Open the console
                </button>
              </div>
            </>
          ) : needsSetup ? (
            <>
              <form onSubmit={(event) => void createAdmin(event)}>
                <h2>Create the admin key</h2>
                <p className="lede">Name the application, then create the key. The key is shown once and belongs to that application.</p>
                {error ? <Banner tone="error">{error}</Banner> : null}
                <div className="field">
                  <label htmlFor="application">Application</label>
                  <input
                    id="application"
                    value={applicationName}
                    onChange={(event) => setApplicationName(event.target.value)}
                    placeholder="Billing"
                    maxLength={128}
                    autoComplete="off"
                    autoFocus
                    required
                  />
                </div>
                <div className="modal-actions">
                  <button className="primary" type="submit" disabled={busy}>
                    {busy ? "Creating…" : "Create admin access"}
                  </button>
                </div>
              </form>
            </>
          ) : (
            <form onSubmit={signIn}>
              <h2>Sign in</h2>
              <p className="lede">Paste a key. An admin opens the full console. An auditor opens review. An inference key opens its own usage.</p>
              {params.get("reason") === "expired" ? (
                <Banner tone="error">That key was rejected. Sign in with a current admin key.</Banner>
              ) : null}
              {error ? <Banner tone="error">{error}</Banner> : null}
              <div className="field">
                <label htmlFor="key">Admin key</label>
                <input
                  id="key"
                  value={key}
                  onChange={(event) => setKey(event.target.value)}
                  placeholder="aigw_live_…"
                  autoComplete="off"
                  autoFocus
                />
              </div>
              <div className="modal-actions">
                <button className="primary" type="submit">Sign in</button>
                {directory ? (
                  <button className="ghost" type="button" onClick={directorySignIn}>Sign in with directory</button>
                ) : null}
              </div>
            </form>
          )}
        </section>
      </main>
    </div>
  );
}
