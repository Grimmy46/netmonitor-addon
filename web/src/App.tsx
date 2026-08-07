import { useEffect, useState } from "react";
import { api, session, type AuthUser } from "./api/client";
import { Dashboard } from "./pages/Dashboard";
import { LoginPage } from "./pages/LoginPage";

type Gate =
  | { s: "loading" }
  | { s: "login"; setupRequired: boolean }
  | { s: "ready"; user: AuthUser };

export function App() {
  const [gate, setGate] = useState<Gate>({ s: "loading" });

  useEffect(() => {
    api
      .authStatus()
      .then((st) => {
        if (st.authenticated && st.user) {
          session.user = st.user;
          setGate({ s: "ready", user: st.user });
        } else {
          setGate({ s: "login", setupRequired: st.setup_required });
        }
      })
      .catch(() => setGate({ s: "login", setupRequired: false }));

    const onUnauthorized = () => {
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
  return <Dashboard />;
}
