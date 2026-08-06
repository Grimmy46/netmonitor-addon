import type { Device } from "../api/client";

export type DeviceState =
  | "online"       // UniFi up + LAN reachable (or not yet probed)
  | "unreachable"  // UniFi up but not answering a LAN ping
  | "degraded"     // UniFi down but the LAN ping still answers (conflict)
  | "offline"      // UniFi down
  | "unknown";     // no status at all

export interface DeviceStateInfo {
  key: DeviceState;
  label: string;
  color: string;
  fault: boolean; // should it surface to the top of the list?
}

const COLORS: Record<DeviceState, string> = {
  online: "var(--good)",
  unreachable: "#f97316", // orange — the "up in UniFi, not reachable" signal
  degraded: "#eab308", // amber
  offline: "var(--critical)",
  unknown: "var(--ink-muted)",
};

/**
 * The 5-state model: reconcile UniFi's is_online with the on-site agent's LAN
 * ping (local_reachable). "unreachable" — up in the controller but not answering
 * a local ping — is the signal Dawid cares about most.
 */
export function deviceState(d: Device): DeviceStateInfo {
  let key: DeviceState;
  if (d.is_online === true) {
    key = d.local_reachable === false ? "unreachable" : "online";
  } else if (d.is_online === false) {
    key = d.local_reachable === true ? "degraded" : "offline";
  } else {
    key = "unknown";
  }
  const LABELS: Record<DeviceState, string> = {
    online: "Online",
    unreachable: "Unreachable",
    degraded: "Up (local)",
    offline: "Offline",
    unknown: "—",
  };
  return {
    key,
    label: LABELS[key],
    color: COLORS[key],
    fault: key === "unreachable" || key === "offline",
  };
}
