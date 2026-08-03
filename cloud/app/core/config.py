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
