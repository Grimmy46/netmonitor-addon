"""Client for a single UniFi **console** via the Network Integration API.

This is a DIFFERENT API from the account-scoped Site Manager one in `unifi.py`.
It talks straight to one console's own hosting URL, e.g.

    https://<console-id>.unifi-hosting.ui.com/proxy/network/integration/v1

Auth is the console's Network API key in an `X-API-KEY` header. The big win:
this key reaches **every site adopted on that console** — including sites the
Site Manager account doesn't *own* — so it's how we see the full RCS_Hosted
fleet and each site's real device up/down.

Endpoints used (Network Integration API v1):
  GET /sites                       every site on the console
  GET /sites/{siteId}/devices      that site's adopted devices (with `state`)

Pagination is offset/limit: responses look like
  {"offset":0,"limit":25,"count":25,"totalCount":142,"data":[...]}
so we page by advancing `offset` until we've collected `totalCount`.

TLS: UniFi hosting URLs (and local UDM/UXG consoles) present a certificate that
doesn't validate against the public CA chain / hostname, so verification is off
by default — the same as `curl -k`. It's toggleable per console.
"""
from typing import Any
from urllib.parse import urlparse

import httpx

# Fixed path segment every UniFi Network Integration API lives under.
INTEGRATION_PATH = "/proxy/network/integration/v1"


class UnifiConsoleError(RuntimeError):
    pass


def normalize_base_url(raw: str) -> str:
    """Turn whatever the user pastes into a canonical integration base URL.

    Accepts the bare host, the host with a trailing slash, or the full
    integration URL (with or without `/sites` on the end) and always returns
    `<scheme>://<host[:port]>/proxy/network/integration/v1` (no trailing slash).
    """
    raw = (raw or "").strip()
    if not raw:
        raise UnifiConsoleError("Console URL is empty.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise UnifiConsoleError(f"Could not parse console URL: {raw!r}")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin + INTEGRATION_PATH


class UnifiConsoleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        verify_tls: bool = False,
        timeout: float = 30.0,
    ):
        self._base = normalize_base_url(base_url)
        self._headers = {"X-API-KEY": api_key, "Accept": "application/json"}
        self._verify = verify_tls
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base

    async def _request(self, path: str, params: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify) as client:
            resp = await client.get(url, headers=self._headers, params=params)
        if resp.status_code in (401, 403):
            raise UnifiConsoleError(
                f"UniFi console key rejected ({resp.status_code}). "
                "Check the Network API key and that it belongs to this console."
            )
        if resp.status_code == 404:
            raise UnifiConsoleError(
                f"UniFi console endpoint not found (404): {path}. "
                "Check the console URL — it should be the hosting URL, e.g. "
                "https://<id>.unifi-hosting.ui.com"
            )
        if resp.status_code == 429:
            raise UnifiConsoleError("UniFi console rate limit hit (429). Back off and retry.")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _data(payload: Any) -> list:
        if isinstance(payload, dict):
            d = payload.get("data")
            if isinstance(d, list):
                return d
            # Some deployments return a bare list or an {"items": [...]} shape.
            items = payload.get("items")
            return items if isinstance(items, list) else []
        return payload if isinstance(payload, list) else []

    async def _get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow offset/limit pagination, accumulating every `data` row."""
        params = dict(params or {})
        params.setdefault("limit", 200)
        offset = 0
        out: list[dict] = []
        for _ in range(1000):  # hard safety cap; a fleet is nowhere near this
            params["offset"] = offset
            payload = await self._request(path, params)
            rows = self._data(payload)
            out.extend(rows)
            total = payload.get("totalCount") if isinstance(payload, dict) else None
            count = payload.get("count") if isinstance(payload, dict) else len(rows)
            limit = payload.get("limit") if isinstance(payload, dict) else params["limit"]
            if not rows:
                break
            if total is not None and len(out) >= int(total):
                break
            # No total reported: stop once a short page comes back.
            if total is None and (not count or int(count) < int(limit)):
                break
            offset += int(count or len(rows) or limit)
        return out

    # ── endpoints ────────────────────────────────────────────────────────────
    async def verify(self) -> bool:
        """Cheap auth check: list sites with a tiny page."""
        await self._request("/sites", {"limit": 1, "offset": 0})
        return True

    async def list_sites(self) -> list[dict]:
        return await self._get_all("/sites")

    async def list_devices(self, site_id: str) -> list[dict]:
        return await self._get_all(f"/sites/{site_id}/devices")
