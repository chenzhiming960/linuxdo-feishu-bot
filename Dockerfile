FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN mkdir -p /config /data /logs

ENV CONFIG_PATH=/config/config.json \
    DB_PATH=/data/monitor.db \
    LOG_DIR=/logs

EXPOSE 8080

CMD ["linuxdo-monitor", "run", "--config", "/config/config.json"]
