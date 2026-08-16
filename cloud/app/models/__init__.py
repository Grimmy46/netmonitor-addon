"""ORM models. Import all here so Alembic autogenerate sees them."""
from app.models.account import Account
from app.models.agent import Agent
from app.models.agent_binary import AgentBinary
from app.models.agent_command import AgentCommand
from app.models.device import Device
from app.models.isp_metric import IspMetric
from app.models.ping_sample import PingSample
from app.models.printer_event import PrinterEvent
from app.models.probe import ProbeSample, ProbeTarget
from app.models.push_subscription import PushSubscription
from app.models.site import Site
from app.models.site_plan import SitePlan
from app.models.unifi_console import UnifiConsole
from app.models.unifi_credential import UnifiCredential
from app.models.user import User

__all__ = [
    "Account",
    "Agent",
    "AgentBinary",
    "AgentCommand",
    "Device",
    "IspMetric",
    "PingSample",
    "PrinterEvent",
    "ProbeSample",
    "ProbeTarget",
    "PushSubscription",
    "Site",
    "SitePlan",
    "UnifiConsole",
    "UnifiCredential",
    "User",
]
