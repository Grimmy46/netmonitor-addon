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
  manual_dormant: boolean;
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
  is_online: boolean | null;
  manual_dormant: boolean;
}

export type DeviceStatusFilter = "active" | "dormant" | "offline" | "online" | "all";

export interface Agent {
  id: string;
  name: string;
  site_id: string | null;
  site_name: string | null;
  station_group: "kiosk" | "ticketbox";
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
  bootstrap_version: string | null;
  exe_rollout: boolean;
  printer_status: "ok" | "paper_out" | "cover_open" | "error" | "unknown" | null;
  printer_status_at: string | null;
  printer_detail: string | null;
  printer_raw: string | null;
  // Predictive paper (roll usage from the printer's cut count).
  printer_cut_count: number | null;
  printer_roll_percent: number | null;      // % of the roll used (0–100)
  printer_cuts_remaining: number | null;     // est. tickets left
  printer_cuts_per_roll: number | null;      // effective yield (learned or seed)
  printer_roll_learned: boolean;             // yield measured from a real run-out?
  printer_roll_partial: boolean;             // anchor set mid-roll (estimate only)
}

export interface PrinterEvent {
  id: string;
  agent_id: string;
  agent_name: string | null;
  state: string;
  prev_state: string | null;
  detail: string | null;
  raw: string | null;
  at: string;
}

export interface AgentExeMeta {
  present: boolean;
  version: string | null;
  sha256: string | null;
  size: number;
  filename: string | null;
  uploaded_at: string | null;
  rollout_count: number;
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

export interface WanIncident {
  id: string;
  kind: string;
  started_at: string;
  ended_at: string | null;
  ongoing: boolean;
  duration_seconds: number | null;
  peak_loss_pct: number | null;
  peak_latency_ms: number | null;
  worst_target: string | null;
  detail: string | null;
}

export interface WanStatus {
  state: "clear" | "brownout" | "unknown";
  since: string | null;
  detail: string | null;
  incident: WanIncident | null;
}

export interface WanMetricSeries {
  wan: string;
  label: string;
  primary: boolean;
  points: MetricPoint[];
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
    credentials: "same-origin",
    ...init,
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    // Session gone (expired / signed out elsewhere) — bounce to the login page.
    window.dispatchEvent(new Event("nm-unauthorized"));
  }
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
  setDeviceDormant: (siteId: string, deviceId: string, dormant: boolean) =>
    req<Device>(`/sites/${siteId}/devices/${deviceId}/dormant`, {
      method: "POST",
      body: JSON.stringify({ dormant }),
    }),
  metrics: (siteId: string) => req<MetricPoint[]>(`/sites/${siteId}/metrics`),
  wanMetrics: (siteId: string) => req<WanMetricSeries[]>(`/sites/${siteId}/wan-metrics`),

  // WAN brownout incident log (from our own on-lot probes).
  wanIncidents: (days = 30) => req<WanIncident[]>(`/live/wan-incidents?days=${days}`),
  wanStatus: () => req<WanStatus>("/live/wan-status"),

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
  bulkCreateStations: (names: string[], stationGroup = "kiosk") =>
    req<{ created: number; skipped: number }>("/agents/bulk", {
      method: "POST",
      body: JSON.stringify({ names, station_group: stationGroup }),
    }),
  setAgentGroup: (id: string, group: string) =>
    req<Agent>(`/agents/${id}/group`, {
      method: "POST",
      body: JSON.stringify({ station_group: group }),
    }),
  queueCommand: (agentId: string, kind: string, args: object = {}) =>
    req<AgentCommand>(`/agents/${agentId}/commands`, {
      method: "POST",
      body: JSON.stringify({ kind, args }),
    }),
  agentCommands: (agentId: string, limit = 10) =>
    req<AgentCommand[]>(`/agents/${agentId}/commands?limit=${limit}`),
  getNotice: () => req<{ notice: string | null; at: string | null }>("/agents/notice"),
  dismissNotice: () => req<void>("/agents/notice/dismiss", { method: "POST" }),
  scheduleRollout: (at: string | null) =>
    req<{ at: string | null }>("/agents/exe-rollout/schedule", {
      method: "POST",
      body: JSON.stringify({ at }),
    }),
  agentPrinterLog: (id: string, limit = 40) =>
    req<PrinterEvent[]>(`/agents/${id}/printer-log?limit=${limit}`),
  markNewRoll: (id: string) =>
    req<Agent>(`/agents/${id}/printer/new-roll`, { method: "POST" }),
  fleetPrinterLog: (hours = 168, limit = 5000) =>
    req<PrinterEvent[]>(`/agents/printer-log?hours=${hours}&limit=${limit}`),
  agentPings: (id: string) => req<PingPoint[]>(`/agents/${id}/pings`),
  agentSparklines: (minutes = 45) =>
    req<Record<string, SparkPoint[]>>(`/agents/pings/recent?minutes=${minutes}`),
  agentPingSummary: (id: string, hours = 24) =>
    req<PingSummary>(`/agents/${id}/pings/summary?hours=${hours}`),

  enrollmentPin: () => req<{ pin: string }>("/agents/enrollment"),
  regenerateEnrollmentPin: () =>
    req<{ pin: string }>("/agents/enrollment/regenerate", { method: "POST" }),

  // Agent exe self-update (staged rollout)
  agentExeMeta: () => req<AgentExeMeta>("/agents/agent-exe"),
  uploadAgentExe: (file: File, version: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("version", version);
    // NOTE: no Content-Type header — the browser sets the multipart boundary.
    return req<AgentExeMeta>("/agents/agent-exe", { method: "POST", body: fd, headers: {} });
  },
  setExeRollout: (opts: { agentIds?: string[]; all?: boolean; enabled: boolean }) =>
    req<{ updated: number }>("/agents/exe-rollout", {
      method: "POST",
      body: JSON.stringify({ agent_ids: opts.agentIds ?? null, all: !!opts.all, enabled: opts.enabled }),
    }),

  // Live landing page.
  liveFeed: (minutes = 10) => req<LiveFeed>(`/live/feed?minutes=${minutes}`),
  liveTargets: () => req<LiveTarget[]>("/live/targets"),
  addLiveTarget: (t: { kind: string; label: string; target: string }) =>
    req<LiveTarget>("/live/targets", { method: "POST", body: JSON.stringify(t) }),
  updateLiveTarget: (id: string, patch: Partial<Pick<LiveTarget, "kind" | "label" | "target" | "enabled" | "sort">>) =>
    req<LiveTarget>(`/live/targets/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteLiveTarget: (id: string) => req<void>(`/live/targets/${id}`, { method: "DELETE" }),
  setProbeAgent: (agentId: string | null) =>
    req<{ probe_agent_id: string | null }>("/live/probe-agent", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId }),
    }),

  // Push notifications.
  vapidKey: () => req<{ public_key: string }>("/notifications/vapid"),
  pushStatus: (endpoint?: string) =>
    req<{ subscription_count: number; mine: number; this_device: boolean }>(
      `/notifications/status${endpoint ? `?endpoint=${encodeURIComponent(endpoint)}` : ""}`,
    ),
  pushSubscribe: (sub: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    req<{ subscription_count: number; mine: number }>("/notifications/subscribe", {
      method: "POST",
      body: JSON.stringify(sub),
    }),
  pushUnsubscribe: (endpoint: string) =>
    req<{ subscription_count: number; mine: number }>("/notifications/unsubscribe", {
      method: "POST",
      body: JSON.stringify({ endpoint }),
    }),
  pushTest: () => req<{ sent: number }>("/notifications/test", { method: "POST" }),

  // Accounts & sessions.
  authStatus: () => req<AuthStatus>("/auth/status"),
  setup: (email: string, password: string) =>
    req<AuthUser>("/auth/setup", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    req<AuthUser>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => req<void>("/auth/logout", { method: "POST" }),
  me: () => req<AuthUser>("/auth/me"),
  users: () => req<AuthUser[]>("/auth/users"),
  createUser: (email: string, password: string, role: string) =>
    req<AuthUser>("/auth/users", { method: "POST", body: JSON.stringify({ email, password, role }) }),
  deleteUser: (id: string) => req<void>(`/auth/users/${id}`, { method: "DELETE" }),
  setUserPassword: (id: string, password: string) =>
    req<void>(`/auth/users/${id}/password`, { method: "POST", body: JSON.stringify({ password }) }),
  setUserRole: (id: string, role: string) =>
    req<AuthUser>(`/auth/users/${id}/role`, { method: "POST", body: JSON.stringify({ role }) }),
};

export interface LiveTarget {
  id: string;
  kind: "ping" | "http";
  label: string;
  target: string;
  enabled: boolean;
  sort: number;
}

export interface LiveSample {
  ts: number; // epoch seconds
  ms: number | null;
}

export interface LiveFeedTarget extends LiveTarget {
  vantage: "local" | "cloud" | "none";
  ok: boolean | null;
  last_ms: number | null;
  loss_pct: number;
  samples: LiveSample[];
}

export interface LiveFeed {
  generated_at: string;
  window_minutes: number;
  probe_agent: { id: string; name: string; online: boolean } | null;
  targets: LiveFeedTarget[];
}

export interface AgentCommand {
  id: string;
  agent_id: string;
  kind: string;
  args: object | null;
  status: "queued" | "sent" | "done" | "error";
  requested_by: string;
  result: Record<string, unknown> | null;
  created_at: string | null;
  sent_at: string | null;
  completed_at: string | null;
}

export interface AuthUser {
  id: string;
  email: string;
  role: "admin" | "viewer";
  is_active: boolean;
}

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  user: AuthUser | null;
}

/** Set once at app bootstrap; components read the role for UI gating.
 * (The SERVER enforces permissions regardless — this only shapes the UI.) */
export const session: { user: AuthUser | null } = { user: null };
export const isAdmin = () => session.user?.role === "admin";
