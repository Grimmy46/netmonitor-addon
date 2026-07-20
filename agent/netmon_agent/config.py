"""Agent configuration — minimal: where the cloud is, and who this agent is."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETMON_", env_file=".env", extra="ignore")

    # Cloud endpoint the agent reports to (outbound only).
    cloud_url: str = "http://localhost:8000"
    # Per-agent auth token issued at enrollment.
    agent_token: str = ""
    agent_name: str = "optiplex-main"

    # How often to run the active-check cycle, in seconds.
    interval: int = 30
