"""ORM models. Import all here so Alembic autogenerate sees them."""
from app.models.account import Account
from app.models.agent import Agent
from app.models.agent_binary import AgentBinary
from app.models.agent_command import AgentCommand
from app.models.device import Device
from app.models.geo_map import GeoMap
from app.models.isp_metric import IspMetric
from app.models.notification_log import NotificationLog
from app.models.ping_rollup import PingRollup1h, PingRollup1m
from app.models.ping_sample import PingSample
from app.models.printer_event import PrinterEvent
from app.models.probe import ProbeSample, ProbeTarget
from app.models.push_subscription import PushSubscription
from app.models.scan_batch import ScanBatch
from app.models.scan_config import ScanConfig
from app.models.signal import SignalConfig, SignalMessage
from app.models.site import Site
from app.models.site_plan import SitePlan
from app.models.status_event import StatusEvent
from app.models.unifi_console import UnifiConsole
from app.models.unifi_credential import UnifiCredential
from app.models.user import User
from app.models.wan_incident import WanIncident

__all__ = [
    "Account",
    "Agent",
    "AgentBinary",
    "AgentCommand",
    "Device",
    "GeoMap",
    "IspMetric",
    "NotificationLog",
    "PingRollup1h",
    "PingRollup1m",
    "PingSample",
    "PrinterEvent",
    "ProbeSample",
    "ProbeTarget",
    "PushSubscription",
    "ScanBatch",
    "ScanConfig",
    "SignalConfig",
    "SignalMessage",
    "Site",
    "SitePlan",
    "StatusEvent",
    "UnifiConsole",
    "UnifiCredential",
    "User",
    "WanIncident",
]
