import { useEffect, useState } from "react";
import { api, isAdmin, type DormantDevice } from "../api/client";
import { humanizeDuration } from "../lib/duration";

const TYPE_LABEL: Record<string, string> = {
  switch: "Switch",
  ap: "Access Point",
  gateway: "Gateway",
};

/**
 * Fleet-wide Dormant tab: every device that's been offline past the dormant
 * threshold (default 4 days), longest-dead first — packed-up or decommissioned
 * gear pulled out of the active views so it doesn't mask real outages.
 */
export function DormantView() {
  const [rows, setRows] = useState<DormantDevice[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .dormantDevices()
        .then((d) => alive && setRows(d))
        .catch((e) => alive && setError(String(e instanceof Error ? e.message : e)));
    load();
    const id = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (error) return <div className="banner err">{error}</div>;
  if (rows === null) return <p className="hint">Loading…</p>;

  if (rows.length === 0) {
    return (
      <div className="empty">
        <p>Nothing dormant. Every device has been seen within the last few days.</p>
      </div>
    );
  }

  return (
    <>
      <p className="sub" style={{ marginBottom: 12 }}>
        {rows.length} device{rows.length === 1 ? "" : "s"} dormant — offline longer than the
        threshold, or parked here manually. Auto-dormant gear comes back the moment it
        reports in; parked gear stays until you restore it.
      </p>
      <div className="panel">
        <table className="devices">
          <thead>
            <tr>
              <th>Device</th>
              <th>Site</th>
              <th>Type</th>
              <th>Model</th>
              <th>IP</th>
              <th>Down for</th>
              <th>Since</th>
              {isAdmin() ? <th></th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td>
                <td>
                  <a href={`#/site/${d.site_id}`}>{d.site_name}</a>
                </td>
                <td>{d.device_type ? (TYPE_LABEL[d.device_type] ?? d.device_type) : "—"}</td>
                <td className="mono">{d.model ?? "—"}</td>
                <td className="mono">{d.ip ?? "—"}</td>
                <td style={d.is_online ? { color: "var(--good)" } : { color: "var(--critical)", fontWeight: 600 }}>
                  {d.is_online
                    ? "online (parked)"
                    : humanizeDuration(d.down_seconds) ?? "—"}
                </td>
                <td className="mono">
                  {d.manual_dormant
                    ? "parked"
                    : d.offline_since
                      ? new Date(d.offline_since).toLocaleDateString()
                      : "—"}
                </td>
                {isAdmin() ? (
                  <td style={{ textAlign: "right" }}>
                    {d.manual_dormant ? (
                      <button
                        className="btn"
                        style={{ fontSize: 12, padding: "3px 8px" }}
                        title="Bring this device back into the active views"
                        onClick={() =>
                          api
                            .setDeviceDormant(d.site_id, d.id, false)
                            .then(() => api.dormantDevices().then(setRows))
                            .catch((e) => setError(String(e instanceof Error ? e.message : e)))
                        }
                      >
                        ↩ Restore
                      </button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
