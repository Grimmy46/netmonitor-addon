"""Client for the official UniFi Site Manager API (https://api.ui.com/v1).

One API key covers every site in the account. Auth is the `X-API-KEY` header.
Docs: https://developer.ui.com/site-manager-api/

This client is intentionally thin: it fetches raw pages and normalizes the few
fields we persist. Field names on the UniFi side are defensively accessed because
the API is young and response shapes may shift.
"""
from typing import Any

import httpx

from app.core.config import get_settings


class UnifiError(RuntimeError):
    pass


class UnifiSiteManagerClient:
    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 20.0):
        self._base = (base_url or get_settings().unifi_api_base).rstrip("/")
        self._headers = {"X-API-KEY": api_key, "Accept": "application/json"}
        self._timeout = timeout

    async def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers, params=params)
        if resp.status_code == 401:
            raise UnifiError("UniFi API key rejected (401). Check the key.")
        if resp.status_code == 429:
            raise UnifiError("UniFi API rate limit hit (429). Back off and retry.")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _items(payload: Any) -> list[dict]:
        """UniFi wraps collections as {"data": [...]} (sometimes paginated)."""
        if isinstance(payload, dict):
            return payload.get("data") or payload.get("items") or []
        if isinstance(payload, list):
            return payload
        return []

    async def verify(self) -> bool:
        """Cheap call to confirm the key works."""
        await self._get("hosts")
        return True

    async def list_hosts(self) -> list[dict]:
        return self._items(await self._get("hosts"))

    async def list_sites(self) -> list[dict]:
        return self._items(await self._get("sites"))

    async def list_devices(self) -> list[dict]:
        return self._items(await self._get("devices"))

    async def get_isp_metrics(self, interval: str = "1h") -> list[dict]:
        return self._items(await self._get("isp-metrics", params={"interval": interval}))
