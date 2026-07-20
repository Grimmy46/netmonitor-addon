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
}

export interface Device {
  id: string;
  name: string;
  model: string | null;
  device_type: string | null;
  ip: string | null;
  mac: string | null;
  is_online: boolean | null;
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
  devices: (siteId: string) => req<Device[]>(`/sites/${siteId}/devices`),
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
};
