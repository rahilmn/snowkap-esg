"""MiroFish prediction engine configuration.

Per MASTER_BUILD_PLAN: Config from tenant settings + news context.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MiroFishSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        env_prefix="MIROFISH_",
    )

    # Service
    HOST: str = "0.0.0.0"
    PORT: int = 5001
    DEBUG: bool = True

    # LLM
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = "claude-sonnet-4-6-20250514"

    # Zep Cloud (agent memory)
    ZEP_API_KEY: str = ""

    # Jena Fuseki (knowledge graph seed data)
    JENA_FUSEKI_URL: str = "http://localhost:3030"
    JENA_DATASET: str = "esg"

    # Database (for storing results)
    DATABASE_URL: str = "postgresql://esg_user:esg_password@localhost:5432/esg_platform"

    # Simulation defaults
    DEFAULT_AGENT_COUNT: int = 20
    MAX_AGENT_COUNT: int = 50
    DEFAULT_ROUNDS: int = 10
    MAX_ROUNDS: int = 40
    SIMULATION_TIMEOUT_SECONDS: int = 300


mirofish_settings = MiroFishSettings()
