const BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface Site {
  id: string;
  name: string;
  isp_name: string | null;
  status: "online" | "degraded" | "offline" | "unknown";
  device_count: number;
  online_device_count: number;
  latency_ms: number | null;
  packet_loss_pct: number | null;
  uptime_pct: number | null;
  download_mbps: number | null;
  upload_mbps: number | null;
  dormant_device_count: number;
  map_x: number | null;
  map_y: number | null;
}

export interface Device {
  id: string;
  name: string;
  model: string | null;
  device_type: string | null;
  ip: string | null;
  mac: string | null;
  is_online: boolean | null;
  offline_since: string | null;
  last_online_at: string | null;
  down_seconds: number | null;
  dormant: boolean;
  // Local LAN reachability from an on-site agent (null = never probed).
  local_reachable: boolean | null;
  local_rtt_ms: number | null;
  local_checked_at: string | null;
}

export interface DormantDevice {
  id: string;
  name: string;
  model: string | null;
  device_type: string | null;
  ip: string | null;
  mac: string | null;
  site_id: string;
  site_name: string;
  offline_since: string | null;
  down_seconds: number | null;
}

export type DeviceStatusFilter = "active" | "dormant" | "offline" | "online" | "all";

export interface Agent {
  id: string;
  name: string;
  site_id: string | null;
  site_name: string | null;
  status: "online" | "offline" | "pending";
  online: boolean;
  claimed: boolean;
  machine_id: string | null;
  version: string | null;
  hostname: string | null;
  os: string | null;
  last_ip: string | null;
  last_target: string | null;
  last_seen_at: string | null;
  latest_rtt_ms: number | null;
}

export interface PingPoint {
  ts: string;
  rtt_ms: number | null;
  gateway_rtt_ms: number | null;
}

/** One per-minute sparkline bucket (from /agents/pings/recent). */
export interface SparkPoint {
  ts: string;
  rtt: number | null;
  loss: boolean;
}

export interface PingSummaryBucket {
  ts: string;
  avg_rtt_ms: number | null;
  max_rtt_ms: number | null;
  loss_pct: number;
  n: number;
}

export interface PingSummary {
  hours: number;
  generated_at: string;
  target: string | null;
  first_ts: string | null;
  last_ts: string | null;
  stats: {
    samples: number;
    loss_pct: number;
    uptime_pct: number;
    avg_rtt_ms: number | null;
    min_rtt_ms: number | null;
    max_rtt_ms: number | null;
    p95_rtt_ms: number | null;
    avg_gateway_rtt_ms: number | null;
  };
  buckets: PingSummaryBucket[];
}

export interface MetricPoint {
  ts: string;
  latency_ms: number | null;
  packet_loss_pct: number | null;
  download_mbps: number | null;
  upload_mbps: number | null;
}

export interface UnifiStatus {
  configured: boolean;
  label: string | null;
  key_hint: string | null;
  last_synced_at: string | null;
}

export interface UnifiConsole {
  id: string;
  label: string;
  base_url: string;
  key_hint: string | null;
  verify_tls: boolean;
  last_synced_at: string | null;
  last_error: string | null;
  site_count: number;
}

export interface ConsoleSyncResult {
  consoles: number;
  sites: number;
  devices: number;
  errors: { console: string; error: string }[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<{ status: string; version: string }>("/health"),
  sites: () => req<Site[]>("/sites"),
  site: (siteId: string) => req<Site>(`/sites/${siteId}`),
  devices: (siteId: string, status: DeviceStatusFilter = "active") =>
    req<Device[]>(`/sites/${siteId}/devices?status=${status}`),
  dormantDevices: () => req<DormantDevice[]>("/sites/dormant-devices"),
  metrics: (siteId: string) => req<MetricPoint[]>(`/sites/${siteId}/metrics`),

  unifiStatus: () => req<UnifiStatus>("/integrations/unifi/status"),
  setUnifiKey: (apiKey: string) =>
    req<UnifiStatus>("/integrations/unifi/key", {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteUnifiKey: () => req<void>("/integrations/unifi/key", { method: "DELETE" }),
  syncUnifi: () =>
    req<{ sites: number; devices: number; metrics: number }>(
      "/integrations/unifi/sync",
      { method: "POST" },
    ),

  // UniFi console connections (Network Integration API — reaches every site on
  // a console, including ones the Site Manager account doesn't own).
  consoles: () => req<UnifiConsole[]>("/integrations/unifi/consoles"),
  addConsole: (baseUrl: string, apiKey: string, label: string, verifyTls: boolean) =>
    req<UnifiConsole>("/integrations/unifi/consoles", {
      method: "POST",
      body: JSON.stringify({
        base_url: baseUrl,
        api_key: apiKey,
        label,
        verify_tls: verifyTls,
      }),
    }),
  deleteConsole: (id: string) =>
    req<void>(`/integrations/unifi/consoles/${id}`, { method: "DELETE" }),
  syncConsoles: () =>
    req<ConsoleSyncResult>("/integrations/unifi/consoles/sync", { method: "POST" }),

  saveMapPositions: (positions: { site_id: string; x: number; y: number }[]) =>
    req<void>("/map/positions", {
      method: "PUT",
      body: JSON.stringify({ positions }),
    }),

  // Site agents / stations (Kiosks tab + enrollment).
  agents: () => req<Agent[]>("/agents"),
  createAgent: (name: string, siteId: string | null) =>
    req<Agent>("/agents", {
      method: "POST",
      body: JSON.stringify({ name, site_id: siteId }),
    }),
  deleteAgent: (id: string) => req<void>(`/agents/${id}`, { method: "DELETE" }),
  releaseAgent: (id: string) => req<Agent>(`/agents/${id}/release`, { method: "POST" }),
  setAgentSite: (id: string, siteId: string | null) =>
    req<Agent>(`/agents/${id}/site`, {
      method: "POST",
      body: JSON.stringify({ site_id: siteId }),
    }),
  bulkCreateStations: (names: string[]) =>
    req<{ created: number; skipped: number }>("/agents/bulk", {
      method: "POST",
      body: JSON.stringify({ names }),
    }),
  agentPings: (id: string) => req<PingPoint[]>(`/agents/${id}/pings`),
  agentSparklines: (minutes = 45) =>
    req<Record<string, SparkPoint[]>>(`/agents/pings/recent?minutes=${minutes}`),
  agentPingSummary: (id: string, hours = 24) =>
    req<PingSummary>(`/agents/${id}/pings/summary?hours=${hours}`),

  enrollmentPin: () => req<{ pin: string }>("/agents/enrollment"),
  regenerateEnrollmentPin: () =>
    req<{ pin: string }>("/agents/enrollment/regenerate", { method: "POST" }),
};
