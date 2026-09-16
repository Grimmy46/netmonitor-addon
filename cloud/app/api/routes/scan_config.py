"""The Scan Station's category schema — one server-side copy for every handheld.

Guarded by the same shared token as /scan-batches: the Scan Station is a static
page with no user session. The token is readable in that page's source, so this
stops drive-by writes and nothing more.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.scan_batches import require_scan_token
from app.core.db import get_db
from app.models import ScanConfig

router = APIRouter(prefix="/scan-config", tags=["scan-config"])

# Read field-for-field off the inventory app's own Add-device form, so a scanned
# row carries the keys the app would have written. This is the seed only — once a
# row exists it is authoritative and edits come through PUT.
DEFAULT_CONFIG = {
    "order": ["battery", "scanner", "network", "pos", "printer", "payment", "kds", "kiosk", "other"],
    "categories": {
        "battery": {
            "label": "Battery", "on": True, "serial": False,
            "models": [{"mf": "Zebra", "md": "TC21"}, {"mf": "Zebra", "md": "TC22"}],
            "fields": [],
        },
        "scanner": {
            "label": "Scanner", "on": True, "serial": True,
            "models": [{"mf": "Zebra", "md": "TC21"}, {"mf": "Zebra", "md": "TC22"}],
            "fields": [
                {"key": "wifi_mac", "label": "Wi-Fi MAC"},
                {"key": "bluetooth_mac", "label": "Bluetooth MAC"},
                {"key": "firmware_version", "label": "Firmware Version"},
            ],
        },
        "network": {
            "label": "Network", "on": True, "serial": True,
            "models": [
                {"mf": "Meraki", "md": "MS125-24P"}, {"mf": "Meraki", "md": "MR86"},
                {"mf": "UniFi", "md": "USW PRO MAX"}, {"mf": "UniFi", "md": "U7 Pro Outdoor"},
                {"mf": "Meraki", "md": "MS120-8"}, {"mf": "UniFi", "md": "UAP-AC-M"},
                {"mf": "UniFi", "md": "UXG GATEWAY MAX"}, {"mf": "Verizon", "md": "XC46BE"},
                {"mf": "UniFi", "md": "MESH AP CYL"}, {"mf": "UniFi", "md": "USW SWITCH 16 PoE"},
                {"mf": "Meraki", "md": "MS130-12X"}, {"mf": "UniFi", "md": "USW-WAN HIGH AVAILABILITY"},
                {"mf": "UniFi", "md": "USW ULTRA 60W"}, {"mf": "UniFi", "md": "UXG ENTERPRISE"},
                {"mf": "UniFi", "md": "BASE STATION"}, {"mf": "PAX", "md": "E600M"},
                {"mf": "UniFi", "md": "U6 Lite"}, {"mf": "UniFi", "md": "USW SWITCH PRO XG AGGREGATION"},
                {"mf": "Inseego", "md": "FX4240"},
            ],
            "fields": [
                {"key": "wifi_mac", "label": "Wi-Fi MAC"}, {"key": "lan_mac", "label": "LAN MAC"},
                {"key": "imei", "label": "IMEI"}, {"key": "carrier", "label": "Carrier"},
                {"key": "ip_address", "label": "IP Address"},
                {"key": "firmware_version", "label": "Firmware Version"},
            ],
        },
        "pos": {
            "label": "POS", "on": True, "serial": True,
            "models": [
                {"mf": "PAX", "md": "E700M"}, {"mf": "Nexus", "md": "TM21"},
                {"mf": "Sunmi", "md": "L3561"}, {"mf": "PAX", "md": "E600M"},
                {"mf": "PAX", "md": "A35"},
            ],
            "fields": [
                {"key": "lan_mac", "label": "LAN MAC"}, {"key": "wifi_mac", "label": "Wi-Fi MAC"},
                {"key": "imei", "label": "IMEI"},
            ],
        },
        "printer": {
            "label": "Printer", "on": True, "serial": True,
            "models": [
                {"mf": "Epson", "md": "M267D"}, {"mf": "Epson", "md": "M374C"},
                {"mf": "Citizen", "md": "CT-E351"},
            ],
            "fields": [
                {"key": "lan_mac", "label": "LAN MAC"}, {"key": "wifi_mac", "label": "Wi-Fi MAC"},
                {"key": "paper_spec", "label": "Paper Spec"}, {"key": "ribbon_spec", "label": "Ribbon Spec"},
            ],
        },
        "payment": {
            "label": "Payment", "on": True, "serial": True,
            "models": [{"mf": "PAX", "md": "A35"}],
            "fields": [
                {"key": "lan_mac", "label": "LAN MAC"}, {"key": "wifi_mac", "label": "Wi-Fi MAC"},
                {"key": "imei", "label": "IMEI"}, {"key": "merchant_id", "label": "Merchant ID"},
                {"key": "terminal_id", "label": "Terminal ID"},
                {"key": "pci_compliance_date", "label": "PCI Compliance Date"},
            ],
        },
        "kds": {
            "label": "KDS", "on": False, "serial": True, "models": [],
            "fields": [
                {"key": "wifi_mac", "label": "Wi-Fi MAC"}, {"key": "firmware_version", "label": "Firmware Version"},
                {"key": "os_version", "label": "OS Version"}, {"key": "screen_size", "label": "Screen Size"},
            ],
        },
        "kiosk": {
            "label": "Kiosk", "on": False, "serial": True, "models": [],
            "fields": [
                {"key": "wifi_mac", "label": "Wi-Fi MAC"}, {"key": "firmware_version", "label": "Firmware Version"},
                {"key": "os_version", "label": "OS Version"}, {"key": "screen_size", "label": "Screen Size"},
            ],
        },
        "other": {"label": "Other", "on": False, "serial": True, "models": [], "fields": []},
    },
}


class ConfigIn(BaseModel):
    data: dict = Field(default_factory=dict)


async def _row(db: AsyncSession) -> ScanConfig:
    res = await db.execute(select(ScanConfig).order_by(ScanConfig.created_at).limit(1))
    row = res.scalar_one_or_none()
    if row is None:
        row = ScanConfig(version=1, data=DEFAULT_CONFIG)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("", dependencies=[Depends(require_scan_token)])
async def get_config(db: AsyncSession = Depends(get_db)) -> dict:
    row = await _row(db)
    return {"version": row.version, "updated_at": row.updated_at.isoformat(), "data": row.data}


@router.put("", dependencies=[Depends(require_scan_token)])
async def put_config(payload: ConfigIn, db: AsyncSession = Depends(get_db)) -> dict:
    cats = (payload.data or {}).get("categories")
    if not isinstance(cats, dict) or not cats:
        raise HTTPException(status_code=400, detail="config needs a non-empty categories object")
    row = await _row(db)
    row.data = payload.data
    row.version = (row.version or 0) + 1
    await db.commit()
    await db.refresh(row)
    return {"version": row.version, "updated_at": row.updated_at.isoformat()}


@router.post("/reset", dependencies=[Depends(require_scan_token)])
async def reset_config(db: AsyncSession = Depends(get_db)) -> dict:
    """Put the schema back to the inventory app's own shape, for every device at
    once — the server-side equivalent of the per-device reset button."""
    row = await _row(db)
    row.data = DEFAULT_CONFIG
    row.version = (row.version or 0) + 1
    await db.commit()
    await db.refresh(row)
    return {"version": row.version, "reset": True}
