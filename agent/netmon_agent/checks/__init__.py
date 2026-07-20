"""Active network checks — ported/adapted from NetMonitor 1.0's test logic.

Each check returns a plain dict the agent batches and pushes to the cloud.
"""
from netmon_agent.checks.http import http_check
from netmon_agent.checks.ping import ping_check

__all__ = ["http_check", "ping_check"]
