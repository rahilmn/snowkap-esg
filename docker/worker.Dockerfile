# SNOWKAP ESG Celery Worker — Dockerfile
# Per CLAUDE.md: Celery 5.4+ for background processing

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["celery", "-A", "backend.tasks.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
