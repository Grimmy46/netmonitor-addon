"""Pydantic API schemas."""
from app.schemas.agents import (
    AgentCreate,
    AgentOut,
    AgentReport,
    AgentReportResult,
    EnrollAddIn,
    EnrollClaimIn,
    EnrollmentPinOut,
    EnrollResult,
    EnrollStationOut,
    EnrollStationsIn,
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
    "AgentOut",
    "AgentReport",
    "AgentReportResult",
    "EnrollAddIn",
    "EnrollClaimIn",
    "EnrollResult",
    "EnrollStationOut",
    "EnrollStationsIn",
    "EnrollmentPinOut",
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
