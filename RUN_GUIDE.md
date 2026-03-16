# SNOWKAP ESG — Local Run Guide

## Prerequisites

- **Python 3.12+** (with pip)
- **Node.js 18+** (with npm)
- **Docker Desktop** (for Redis; Postgres can be local or Docker)
- **Local PostgreSQL 18** (installed at port 5432, superuser `postgres/postgres`)

---

## Step-by-Step

### 1. Database Setup (one-time)

Using local Postgres (recommended — avoids Docker auth issues):

```bash
python -c "
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
conn = psycopg2.connect(host='127.0.0.1', port=5432, dbname='postgres', user='postgres', password='postgres')
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute(\"CREATE USER esg_user WITH PASSWORD 'esg_password'\")
cur.execute('CREATE DATABASE esg_platform OWNER esg_user')
conn.close()
"
```

### 2. Start Redis (Docker)

```bash
cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg
docker compose up -d redis
```

### 3. Install Python Dependencies

```bash
pip install psycopg2-binary  # if not already installed
pip install -r requirements.txt
```

### 4. Run Alembic Migrations

```bash
cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg
PYTHONPATH=. alembic -c backend/migrations/alembic.ini upgrade head
```

### 5. Start FastAPI Backend

```bash
cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg
PYTHONPATH=. python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
```

Health check:
```bash
curl http://127.0.0.1:8001/api/health
# Expected: {"status":"healthy"}
```

> **Note:** Port 8000 may conflict with other local services; 8001 is the working port.

### 6. Start Vite Frontend

```bash
cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg/client
npm run dev
```

### 7. Open Browser

- **App:** http://localhost:5173
- **API docs:** http://localhost:8001/docs

---

## Port Map

| Service          | Port | Notes                              |
| ---------------- | ---- | ---------------------------------- |
| Frontend (Vite)  | 5173 | Proxies `/api` → backend           |
| Backend (FastAPI)| 8001 | Changed from 8000 due to conflict  |
| PostgreSQL       | 5432 | Local install (`postgres/postgres`)|
| Redis            | 6379 | Docker container `esg-redis`       |

## Optional Services (not required for core app)

| Service      | Port | How to start                                                          |
| ------------ | ---- | --------------------------------------------------------------------- |
| Jena Fuseki  | 3030 | `docker compose up -d jena-fuseki` (enables SPARQL/knowledge graph)   |
| MinIO        | 9000 | `docker compose up -d minio` (enables file uploads)                   |
| MiroFish     | 5001 | `PYTHONPATH=. python -m uvicorn prediction.main:app --port 5001`      |




## Known Issues

- **Port 8000 conflict** — A local process may hold port 8000. Use 8001 (`vite.config.ts` proxy already points there).
- **pgvector not available** — Local Postgres doesn't have the pgvector extension. Embedding/vector search features are unavailable but the app runs fine without them.
- **Docker Postgres auth** — Docker Desktop + WSL2 can have password auth issues. Using local Postgres avoids this entirely.
- **Docker image pull failures** — `stain/jena-fuseki` and `minio/minio` images may fail to pull if Docker Desktop is unstable. These are optional services.

---

## .env File

Already configured at `D:/ClaudePowerofnow/snowkap-esg/snowkap-esg/.env` with:

- OpenAI API key (for LLM features)
- JWT secret (for auth)
- All database/Redis/service URLs

---

## Verification Checklist

1. `curl http://127.0.0.1:8001/api/health` returns `{"status":"healthy"}`
2. http://localhost:5173 loads the React app
3. Login flow works
