FROM python:3.12-slim

WORKDIR /app

# iputils-ping: the Live-page server prober shells out to `ping` (ICMP needs
# either root or the binary's capabilities — the official package ships them).
# tesseract-ocr (+ English data): the teardown bot reads RCS asset tags and
# UniFi labels off crew photos locally (app/services/photo_reader.py).
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

# Teardown/setup bot photos (kept 90 days, then summarized). Mount a volume here.
RUN mkdir -p /data/photos

# System deps kept minimal; asyncpg wheels avoid needing build tools.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
