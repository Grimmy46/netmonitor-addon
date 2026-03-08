#!/usr/bin/with-contenv bashio

# Working directory is /config/netmonitor — persists across HA restarts
mkdir -p /config/netmonitor
cd /config/netmonitor

bashio::log.info "Starting NetMonitor dashboard on port 8088..."
bashio::log.info "Data files stored in /config/netmonitor/"

exec python3 /app/network_tester.py
