import { useEffect, useState } from "react";
import { api, session, type AuthUser } from "./api/client";
import { Dashboard } from "./pages/Dashboard";
import { EmployeeApp } from "./pages/EmployeeApp";
import { LoginPage } from "./pages/LoginPage";
import { SharedMapPage } from "./components/GeoMap";

type Gate =
  | { s: "loading" }
  | { s: "login"; setupRequired: boolean }
  | { s: "ready"; user: AuthUser }
  | { s: "guestgone" };

export function App() {
  // Public view-only map link: no sign-in at all.
  const share = window.location.hash.match(/^#\/share\/([\w-]+)/);
  if (share) return <SharedMapPage token={share[1]} />;
  return <AuthedApp />;
}

function AuthedApp() {
  const [gate, setGate] = useState<Gate>({ s: "loading" });

  useEffect(() => {
    // Guest view link: #/guest/<token> → view-only guest session, no sign-in.
    const g = window.location.hash.match(/^#\/guest\/([\w-]+)/);
    if (g) {
      api.guestSignIn(g[1])
        .then((u) => { session.user = u; setGate({ s: "ready", user: u }); })
        .catch(() => setGate({ s: "guestgone" }));
    }
    const statusCall = g ? Promise.resolve(null) : api.authStatus();
    statusCall
      .then((st) => {
        if (st === null) return;
        if (st.authenticated && st.user) {
          session.user = st.user;
          setGate({ s: "ready", user: st.user });
        } else {
          setGate({ s: "login", setupRequired: st.setup_required });
        }
      })
      .catch(() => setGate({ s: "login", setupRequired: false }));

    const onUnauthorized = () => {
      if (/^#\/guest\//.test(window.location.hash)) { session.user = null; setGate({ s: "guestgone" }); return; }
      session.user = null;
      setGate({ s: "login", setupRequired: false });
    };
    window.addEventListener("nm-unauthorized", onUnauthorized);
    return () => window.removeEventListener("nm-unauthorized", onUnauthorized);
  }, []);

  if (gate.s === "loading") {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ink-muted)" }}>
        …
      </div>
    );
  }
  if (gate.s === "guestgone") {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24, textAlign: "center" }}>
        <div>
          <img src="/rcs-logo.png" alt="RCS" style={{ width: 140, height: "auto" }} />
          <h2 style={{ margin: "16px 0 6px" }}>This guest link is no longer active</h2>
          <p style={{ color: "var(--ink-muted)", margin: 0 }}>Ask the RCS IT team for a new link.</p>
        </div>
      </div>
    );
  }
  if (gate.s === "login") {
    return (
      <LoginPage
        setupRequired={gate.setupRequired}
        onSignedIn={(u) => {
          session.user = u;
          setGate({ s: "ready", user: u });
        }}
      />
    );
  }
  if (gate.user.role === "employee" || gate.user.role === "guest") {
    return (
      <EmployeeApp
        guest={gate.user.role === "guest"}
        onSignOut={async () => {
          try { await api.logout(); } catch { /* ignore */ }
          // Leave the guest link so the staff login page shows (not "link inactive").
          if (/^#\/guest\//.test(window.location.hash)) history.replaceState(null, "", window.location.pathname);
          window.dispatchEvent(new Event("nm-unauthorized"));
        }}
      />
    );
  }
  return <Dashboard />;
}
