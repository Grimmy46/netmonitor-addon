import type { Device } from "../api/client";
import { deviceState } from "../lib/deviceState";
import { humanizeDuration } from "../lib/duration";

const TYPE_LABEL: Record<string, string> = {
  switch: "Switch",
  ap: "Access Point",
  gateway: "Gateway",
};

export function DeviceTable({
  devices,
  onSetDormant,
}: {
  devices: Device[];
  /** Admin-only: passed by the page when the signed-in user may park/restore. */
  onSetDormant?: (d: Device, dormant: boolean) => void;
}) {
  if (devices.length === 0) return <p className="hint">No devices match.</p>;
  return (
    <table className="devices">
      <thead>
        <tr>
          <th></th>
          <th>Name</th>
          <th>Type</th>
          <th>Model</th>
          <th>IP</th>
          <th>Status</th>
          {onSetDormant ? <th></th> : null}
        </tr>
      </thead>
      <tbody>
        {devices.map((d) => {
          const down = d.is_online === false ? humanizeDuration(d.down_seconds) : null;
          const st = deviceState(d);
          const recentlyDown =
            d.is_online === false && !d.dormant && (d.down_seconds ?? 0) < 12 * 3600;
          const highlight = recentlyDown || st.key === "unreachable";
          const localMs =
            d.local_reachable === true && d.local_rtt_ms != null
              ? `${Math.round(d.local_rtt_ms)} ms`
              : null;
          return (
            <tr
              key={d.id}
              style={
                highlight
                  ? { background: `color-mix(in srgb, ${st.color} 9%, transparent)` }
                  : undefined
              }
            >
              <td>
                <span
                  className="dot"
                  title={st.label}
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: st.color,
                  }}
                />
              </td>
              <td>{d.name}</td>
              <td>{d.device_type ? (TYPE_LABEL[d.device_type] ?? d.device_type) : "—"}</td>
              <td className="mono">{d.model ?? "—"}</td>
              <td className="mono">{d.ip ?? "—"}</td>
              <td>
                <span style={{ color: st.color }}>
                  {st.key === "offline" ? `Down${down ? ` ${down}` : ""}` : st.label}
                </span>
                {st.key === "unreachable" ? (
                  <span className="sub" style={{ marginLeft: 6, fontSize: 12 }}>
                    up in UniFi
                  </span>
                ) : localMs ? (
                  <span className="sub" style={{ marginLeft: 6, fontSize: 12, color: "var(--ink-muted)" }}>
                    · LAN {localMs}
                  </span>
                ) : null}
                {d.manual_dormant ? (
                  <span className="sub" style={{ marginLeft: 6, fontSize: 12, color: "var(--ink-muted)" }}>
                    · parked
                  </span>
                ) : null}
              </td>
              {onSetDormant ? (
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {d.manual_dormant ? (
                    <button
                      className="btn"
                      style={{ fontSize: 12, padding: "3px 8px" }}
                      title="Bring this device back into the active views"
                      onClick={() => onSetDormant(d, false)}
                    >
                      ↩ Restore
                    </button>
                  ) : !d.dormant ? (
                    <button
                      className="btn"
                      style={{ fontSize: 12, padding: "3px 8px" }}
                      title="Park this device in the Dormant tab (packed up / spare) — it stops counting as down and never alerts"
                      onClick={() => onSetDormant(d, true)}
                    >
                      ⏾ Dormant
                    </button>
                  ) : null}
                </td>
              ) : null}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
