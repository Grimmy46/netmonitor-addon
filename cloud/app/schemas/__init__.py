"""Pydantic API schemas."""
from app.schemas.agents import (
    AgentCreate,
    AgentCreated,
    AgentOut,
    AgentReport,
    AgentReportResult,
    PingPoint,
    PingSampleIn,
)
from app.schemas.integrations import (
    UnifiConsoleIn,
    UnifiConsoleOut,
    UnifiConsoleSyncResult,
    UnifiKeyIn,
    UnifiKeyStatus,
    UnifiSyncResult,
)
from app.schemas.network import DeviceOut, DormantDeviceOut, MetricPoint, SiteOut

__all__ = [
    "AgentCreate",
    "AgentCreated",
    "AgentOut",
    "AgentReport",
    "AgentReportResult",
    "DeviceOut",
    "DormantDeviceOut",
    "PingPoint",
    "PingSampleIn",
    "MetricPoint",
    "SiteOut",
    "UnifiConsoleIn",
    "UnifiConsoleOut",
    "UnifiConsoleSyncResult",
    "UnifiKeyIn",
    "UnifiKeyStatus",
    "UnifiSyncResult",
]
