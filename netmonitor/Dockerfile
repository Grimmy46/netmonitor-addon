ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python3, traceroute tools
RUN apk add --no-cache python3 py3-pip traceroute iputils

# Create working directory in HA config folder (persists across restarts)
WORKDIR /config/netmonitor

# Copy the application
COPY network_tester.py /app/network_tester.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
