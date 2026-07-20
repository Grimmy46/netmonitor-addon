#!/usr/bin/with-contenv bashio

# Export the ingress path so network_tester.py can strip it from requests
export INGRESS_PATH=$(bashio::addon.ingress_entry)

bashio::log.info "NetMonitor starting on port 8088"
bashio::log.info "Ingress path: ${INGRESS_PATH}"

cd /config/netmonitor
exec python3 /app/network_tester.py
