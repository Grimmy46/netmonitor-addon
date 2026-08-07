import { useEffect, useMemo, useState } from "react";
import { api, type Site } from "../api/client";

/**
 * Planner tab: SitePlanner (the proven single-file planning tool) embedded
 * same-origin in an iframe. The ?embedded=1&site=<id> query switches the
 * planner into cloud mode (save/load to the dashboard, live 5-state feed).
 */
export function PlannerView() {
  const [sites, setSites] = useState<Site[]>([]);
  const [siteId, setSiteId] = useState<string>("");

  useEffect(() => {
    api
      .sites()
      .then((ss) => {
        setSites(ss);
        if (ss.length && !siteId) {
          const main = ss.find((s) => s.name.toLowerCase() === "main");
          setSiteId((main ?? ss[0]).id);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const src = useMemo(
    () => `/planner/?embedded=1${siteId ? `&site=${siteId}` : ""}`,
    [siteId],
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 120px)" }}>
      <div className="devices-toolbar" style={{ marginBottom: 10 }}>
        <div className="panel-title" style={{ margin: 0 }}>Site plan</div>
        {sites.length > 1 ? (
          <select
            className="search"
            style={{ marginLeft: 12, padding: "4px 8px" }}
            value={siteId}
            onChange={(e) => setSiteId(e.target.value)}
          >
            {sites.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        ) : null}
        <div className="spacer" />
        <a className="btn" href={src} target="_blank" rel="noreferrer" title="Open the planner full-screen in its own tab">
          ⧉ Open full-screen
        </a>
      </div>
      <iframe
        key={src}
        src={src}
        title="SitePlanner"
        style={{
          flex: 1,
          width: "100%",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          background: "#fff",
        }}
      />
    </div>
  );
}
