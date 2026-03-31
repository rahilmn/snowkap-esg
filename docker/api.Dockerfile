# SNOWKAP ESG API — Multi-stage Dockerfile
# Per CLAUDE.md: FastAPI + Python 3.12, port 8000

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Production stage ---
FROM base AS production

COPY backend/ ./backend/

# Non-root user per MASTER_BUILD_PLAN Phase 8
RUN adduser --disabled-password --gecos "" appuser
USER appuser

EXPOSE 8000

# Run migrations before starting the server
CMD ["sh", "-c", "python -m alembic -c backend/migrations/alembic.ini upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
