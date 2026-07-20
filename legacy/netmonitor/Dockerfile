ARG BUILD_FROM
FROM $BUILD_FROM

# traceroute is optional — script falls back gracefully if not found
RUN apk add --no-cache traceroute || true

WORKDIR /config/netmonitor
COPY network_tester.py /app/network_tester.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
