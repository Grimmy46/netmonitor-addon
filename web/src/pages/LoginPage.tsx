import { useState } from "react";
import { api, type AuthUser } from "../api/client";
import { PulseLogo } from "../components/PulseLogo";

/**
 * The landing gate: sign-in normally, or — the very first time the dashboard
 * runs with zero accounts — a one-time "create your admin account" form.
 */
export function LoginPage({
  setupRequired,
  onSignedIn,
}: {
  setupRequired: boolean;
  onSignedIn: (u: AuthUser) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    if (setupRequired) {
      if (password.length < 8) return setError("Password needs at least 8 characters.");
      if (password !== confirm) return setError("Passwords don't match.");
    }
    setBusy(true);
    try {
      const user = setupRequired
        ? await api.setup(email.trim(), password)
        : await api.login(email.trim(), password);
      onSignedIn(user);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      background: "var(--page)", padding: 20,
    }}>
      <div className="card" style={{ width: "min(380px, 94vw)", padding: 28 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <PulseLogo size={30} />
          <h1 style={{ margin: 0, fontSize: 22 }}>NetMonitor</h1>
        </div>
        <p className="sub" style={{ marginTop: 0, marginBottom: 20 }}>
          {setupRequired
            ? "Welcome — create the first admin account to get started."
            : "Sign in to the RCS fleet dashboard."}
        </p>

        <div className="field">
          <label htmlFor="lg-email">Email</label>
          <input
            id="lg-email" type="email" autoComplete="username" autoFocus
            value={email} onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>
        <div className="field">
          <label htmlFor="lg-pass">Password</label>
          <input
            id="lg-pass" type="password"
            autoComplete={setupRequired ? "new-password" : "current-password"}
            value={password} onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>
        {setupRequired ? (
          <div className="field">
            <label htmlFor="lg-confirm">Confirm password</label>
            <input
              id="lg-confirm" type="password" autoComplete="new-password"
              value={confirm} onChange={(e) => setConfirm(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
        ) : null}

        {error ? <div className="banner err" style={{ marginBottom: 12 }}>{error}</div> : null}

        <button
          className="btn btn-primary" style={{ width: "100%", padding: "10px 0" }}
          onClick={submit}
          disabled={busy || !email.trim() || !password}
        >
          {busy ? "…" : setupRequired ? "Create admin account" : "Sign in"}
        </button>
      </div>
    </div>
  );
}
