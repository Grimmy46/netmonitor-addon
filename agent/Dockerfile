FROM python:3.12-slim

# traceroute is optional — checks fall back gracefully if absent.
RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping traceroute \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
COPY . .

CMD ["python", "-m", "netmon_agent.main"]
