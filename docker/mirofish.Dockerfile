# MiroFish Prediction Engine — Dockerfile
# Per CLAUDE.md: Separate microservice on port 5001 (AGPL-3.0 process isolation)

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# MiroFish dependencies
COPY prediction/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prediction/ ./prediction/

RUN adduser --disabled-password --gecos "" appuser
USER appuser

EXPOSE 5001

CMD ["uvicorn", "prediction.app:app", "--host", "0.0.0.0", "--port", "5001"]
