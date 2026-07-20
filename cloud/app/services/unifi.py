"""Client for the official UniFi Site Manager API.

Base: https://api.ui.com   Auth: `X-API-KEY` header.
One API key covers every site in the account.
Docs: https://developer.ui.com/site-manager-api/

Endpoints used:
  GET  /v1/hosts                    consoles / controllers
  GET  /v1/sites                    sites (name in meta, counts in statistics)
  GET  /v1/devices                  devices grouped by host; each has siteId
  GET  /v1/isp-metrics/{interval}   WAN health; falls back to /ea/ if /v1/ 404s

Responses wrap collections as {"data": [...], "nextToken": "..."}; we follow
`nextToken` to page through. Field access is defensive because the API is young.
"""
from typing import Any

import httpx

from app.core.config import get_settings


class UnifiError(RuntimeError):
    pass


class UnifiSiteManagerClient:
    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 30.0):
        self._base = (base_url or get_settings().unifi_api_base).rstrip("/")
        self._headers = {"X-API-KEY": api_key, "Accept": "application/json"}
        self._timeout = timeout

    async def _request(self, path: str, params: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers, params=params)
        if resp.status_code == 401:
            raise UnifiError("UniFi API key rejected (401). Check the key.")
        if resp.status_code == 429:
            raise UnifiError("UniFi API rate limit hit (429). Back off and retry.")
        if resp.status_code == 404:
            raise UnifiError(f"UniFi endpoint not found (404): {path}")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _data(payload: Any) -> Any:
        if isinstance(payload, dict):
            return payload.get("data", payload.get("items", []))
        return payload

    async def _get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow nextToken pagination, accumulating the `data` arrays."""
        params = dict(params or {})
        out: list[dict] = []
        while True:
            payload = await self._request(path, params)
            data = self._data(payload)
            if isinstance(data, list):
                out.extend(data)
            token = payload.get("nextToken") if isinstance(payload, dict) else None
            if not token:
                break
            params["nextToken"] = token
        return out

    # ── endpoints ────────────────────────────────────────────────────────────
    async def verify(self) -> bool:
        await self._request("/v1/hosts", {"pageSize": 1})
        return True

    async def list_hosts(self) -> list[dict]:
        return await self._get_all("/v1/hosts")

    async def list_sites(self) -> list[dict]:
        return await self._get_all("/v1/sites")

    async def list_devices(self) -> list[dict]:
        """Return a flat device list. The API groups devices per host; each host
        block looks like {hostId, hostName, devices: [...]}. We flatten and make
        sure every device carries hostId (siteId is already on each device)."""
        groups = await self._get_all("/v1/devices")
        flat: list[dict] = []
        for group in groups:
            host_id = group.get("hostId") or group.get("hostID")
            for dev in group.get("devices", []) if isinstance(group, dict) else []:
                dev.setdefault("hostId", host_id)
                flat.append(dev)
            # Some tenants may return ungrouped device dicts directly.
            if isinstance(group, dict) and "devices" not in group and group.get("id"):
                flat.append(group)
        return flat

    async def get_isp_metrics(self, interval: str = "1h") -> list[dict]:
        """WAN/ISP metrics. Endpoint moved between /v1 and /ea across releases,
        so try /v1 first and fall back to /ea."""
        for prefix in ("/v1", "/ea"):
            try:
                return await self._get_all(f"{prefix}/isp-metrics/{interval}")
            except UnifiError as exc:
                if "404" in str(exc):
                    continue
                raise
        return []
