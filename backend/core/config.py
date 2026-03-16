"""Application configuration via Pydantic Settings.

Per CLAUDE.md: Pydantic v2 for all schemas, strict type hints.
"""

from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Application ---
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    APP_NAME: str = "SNOWKAP ESG Platform"
    APP_VERSION: str = "2.0.0"
    SECRET_KEY: str = "change-me-to-a-random-64-char-string-in-production"

    # --- Database (PostgreSQL 16 + pgvector + asyncpg) ---
    DATABASE_URL: str = "postgresql+asyncpg://esg_user:esg_password@localhost:5432/esg_platform"
    DATABASE_URL_SYNC: str = "postgresql://esg_user:esg_password@localhost:5432/esg_platform"

    # --- Redis 7 ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Apache Jena Fuseki ---
    JENA_FUSEKI_URL: str = "http://localhost:3030"
    JENA_DATASET: str = "esg"

    # --- MinIO ---
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "esg-files"
    MINIO_SECURE: bool = False

    # --- AI / LLM ---
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # --- Zep Cloud ---
    ZEP_API_KEY: str = ""

    # --- MiroFish ---
    MIROFISH_URL: str = "http://localhost:5001"

    # --- Email (Resend) ---
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@snowkap.com"

    # --- Auth (JWT + Magic Links — no passwords, no OTP) ---
    JWT_SECRET: str = "change-me-to-a-random-64-char-string-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 hours
    MAGIC_LINK_EXPIRE_MINUTES: int = 15

    # --- CORS ---
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Sentry ---
    SENTRY_DSN: str = ""

    # --- News ---
    NEWS_API_KEY: str = ""
    GNEWS_API_KEY: str = ""

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def ensure_asyncpg_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if "sslmode=" in v:
            v = v.split("?")[0]
        return v

    @field_validator("DATABASE_URL_SYNC", mode="before")
    @classmethod
    def ensure_sync_driver(cls, v: str) -> str:
        import os
        db_url = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL") or v
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if not db_url.startswith("postgresql://"):
            db_url = "postgresql://" + db_url.split("://", 1)[-1]
        return db_url

    @field_validator("JWT_SECRET", mode="after")
    @classmethod
    def reject_default_secret_in_production(cls, v: str, info) -> str:
        """Refuse to start if JWT_SECRET is the default placeholder in non-debug mode."""
        default = "change-me-to-a-random-64-char-string-in-production"
        if v == default:
            import os
            debug = os.environ.get("DEBUG", "true").lower() in ("true", "1", "yes")
            env = os.environ.get("ENVIRONMENT", "development").lower()
            if not debug or env == "production":
                raise ValueError(
                    "FATAL: JWT_SECRET is set to the default placeholder. "
                    "Set a random 64-char secret in production."
                )
        return v

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def reject_default_app_secret_in_production(cls, v: str, info) -> str:
        """Refuse to start if SECRET_KEY is the default placeholder in non-debug mode."""
        default = "change-me-to-a-random-64-char-string-in-production"
        if v == default:
            import os
            debug = os.environ.get("DEBUG", "true").lower() in ("true", "1", "yes")
            env = os.environ.get("ENVIRONMENT", "development").lower()
            if not debug or env == "production":
                raise ValueError(
                    "FATAL: SECRET_KEY is set to the default placeholder. "
                    "Set a random 64-char secret in production."
                )
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | List[str]) -> List[str]:
        import os
        if isinstance(v, str):
            import json
            origins = json.loads(v)
        else:
            origins = v
        replit_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
        if replit_domain:
            origins.append(f"https://{replit_domain}")
        replit_domains = os.environ.get("REPLIT_DOMAINS", "")
        if replit_domains:
            for d in replit_domains.split(","):
                origins.append(f"https://{d.strip()}")
        return origins


settings = Settings()
