const BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface Site {
  id: string;
  name: string;
  isp_name: string | null;
  status: string;
  device_count: number;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<{ status: string; version: string }>("/health"),
  sites: () => get<Site[]>("/sites"),
};
