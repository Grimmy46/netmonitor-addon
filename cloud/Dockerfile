FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; asyncpg wheels avoid needing build tools.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
