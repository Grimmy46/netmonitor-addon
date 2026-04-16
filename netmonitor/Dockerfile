ARG BUILD_FROM
FROM $BUILD_FROM

# python3 and traceroute only — busybox ping is already in the HA base image
RUN apk add --no-cache python3 traceroute

WORKDIR /config/netmonitor

COPY network_tester.py /app/network_tester.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
