"""Application settings, loaded from environment."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres
    postgres_user: str = "netmonitor"
    postgres_password: str = "netmonitor"
    postgres_db: str = "netmonitor"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Security
    secret_key: str = "dev-secret"
    # Fernet key for encrypting stored third-party credentials (e.g. UniFi API key).
    encryption_key: str = ""

    # CORS (comma-separated)
    cors_origins: str = "http://localhost:5173"

    # UniFi Site Manager API (root; version prefixes are added per-endpoint)
    unifi_api_base: str = "https://api.ui.com"
    # How often the background poller re-syncs the fleet, in seconds.
    unifi_sync_interval: int = 300
    # A device offline longer than this many days is classified "dormant" and
    # moved out of the active view into the Dormant tab.
    dormant_after_days: int = 4
    # An agent that hasn't checked in within this many seconds counts as offline.
    agent_offline_after_seconds: int = 120
    # Suggested seconds between an on-site agent's full LAN device-ping sweeps.
    agent_probe_interval_seconds: int = 120
    # Stations with no probe site are auto-linked to the site with this name
    # (kiosks all live at Main). Manual per-station overrides still stick.
    default_probe_site_name: str = "Main"
    # Multi-vantage merge: a "reachable" sighting from ANY kiosk protects a
    # device from "unreachable" reports by other kiosks for this many seconds.
    probe_positive_grace_seconds: int = 300

    # ── Push alerting (24/7, faults only) ──────────────────────────────────
    # How often the alert sweep runs.
    alert_sweep_interval_seconds: int = 60
    # A kiosk silent this long counts as a fault (kiosks check in ~every 60s;
    # 5 min rides out reboots and brief network blips without flapping).
    alert_kiosk_offline_seconds: int = 300
    # A device fault must persist this long before it alerts (debounce).
    alert_confirm_seconds: int = 180
    # Faults older than this when first seen never alert (e.g. right after a
    # deploy/restart) — they're history, not news.
    alert_fresh_window_seconds: int = 1800
    # This many NEW faults in one sweep = mass power-down/outage: individual
    # pushes are suppressed and one summary push is sent instead.
    alert_mass_threshold: int = 6
    # Which site's devices can alert (empty = same as default_probe_site_name).
    # Kiosk agents alert regardless of site.
    alert_site_name: str = ""
    # Whole-site outages alert for EVERY site (not just Main): a site that was
    # seen online and then reports offline for this long pushes "SITE DOWN".
    # (UniFi refreshes site status ~every 5 min, so detection is ~5-10 min.)
    alert_site_confirm_seconds: int = 240
    # A ticket-printer fault (paper out / cover open / error) must persist this
    # long before it pushes — rides out a quick paper reload without flapping.
    alert_printer_confirm_seconds: int = 120
    # Ignore a printer reading older than this (agent offline / stopped polling);
    # a stale status must never alert on its own — the kiosk-offline push covers it.
    alert_printer_fresh_seconds: int = 300
    # VAPID "sub" claim sent to the push services.
    vapid_subject: str = "mailto:dawidrcs@gmail.com"

    # ── Live landing page probes ───────────────────────────────────────────
    # Server-vantage prober cadence (always on; keeps charts alive overnight).
    live_server_probe_interval_seconds: float = 5.0
    # Designated kiosk cadences (sent to the agent via /agents/live-config).
    live_agent_ping_interval_seconds: float = 2.0
    live_agent_http_interval_seconds: float = 10.0
    live_agent_post_interval_seconds: float = 10.0
    # Samples older than this are pruned.
    live_retention_hours: int = 48
    # A local (kiosk) sample newer than this makes "local" the preferred vantage.
    live_local_fresh_seconds: int = 45

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
