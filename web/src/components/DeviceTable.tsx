import type { Device } from "../api/client";
import { humanizeDuration } from "../lib/duration";

const TYPE_LABEL: Record<string, string> = {
  switch: "Switch",
  ap: "Access Point",
  gateway: "Gateway",
};

export function DeviceTable({ devices }: { devices: Device[] }) {
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
        </tr>
      </thead>
      <tbody>
        {devices.map((d) => {
          const down = d.is_online === false ? humanizeDuration(d.down_seconds) : null;
          return (
            <tr key={d.id}>
              <td>
                <span
                  className="dot"
                  title={d.is_online ? "Online" : d.is_online === false ? "Offline" : "Unknown"}
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: d.is_online
                      ? "var(--good)"
                      : d.is_online === false
                        ? "var(--critical)"
                        : "var(--ink-muted)",
                  }}
                />
              </td>
              <td>{d.name}</td>
              <td>{d.device_type ? (TYPE_LABEL[d.device_type] ?? d.device_type) : "—"}</td>
              <td className="mono">{d.model ?? "—"}</td>
              <td className="mono">{d.ip ?? "—"}</td>
              <td>
                {d.is_online === true ? (
                  <span style={{ color: "var(--good)" }}>Online</span>
                ) : d.is_online === false ? (
                  <span style={{ color: "var(--critical)" }}>
                    Down{down ? ` ${down}` : ""}
                  </span>
                ) : (
                  <span style={{ color: "var(--ink-muted)" }}>—</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
