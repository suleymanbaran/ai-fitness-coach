from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    demo_mode: bool = False
    openai_api_key: str = ""
    # Two-tier router: fast model for chat/logging/extraction, plan model for program generation.
    fast_model: str = "gpt-6-luna"
    plan_model: str = "gpt-6.1-terra"
    embedding_model: str = "text-embedding-3-small"
    # Explicit 12h breakpoint on the stable prefix + implicit 30m session caching (Responses API).
    enable_prompt_caching: bool = True
    cache_ttl_stable: str = "12h"
    cache_ttl_session: str = "30m"
    chat_history_turns: int = 8
    memory_max_entries: int = 40
    tool_max_iterations: int = 6
    # Built-in OpenAI web_search tool ($10/1k calls): used when FatSecret has no match,
    # e.g. local dishes missing from its US-only free database.
    enable_web_search: bool = True

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/fitness_coach"

    # Set QDRANT_URL to use the Qdrant server; otherwise the embedded on-disk mode at QDRANT_PATH is used.
    qdrant_url: str | None = None
    qdrant_path: str = "./qdrant_data"
    qdrant_collection: str = "protocols"
    rag_top_k: int = 3

    # Off for local use. When on, every /users and /ask request needs `Authorization: Bearer <token>`
    # (issued by scripts/issue_token.py) and can only touch the token owner's data.
    auth_enabled: bool = False
    ask_rate_limit_per_minute: int = 20
    openai_timeout_seconds: float = 30.0
    # Program generation/review produces long structured output on the plan tier.
    plan_timeout_seconds: float = 180.0

    # Integrations. Each provider is enabled only when its credentials are set.
    # TOKEN_ENCRYPTION_KEY encrypts stored OAuth tokens; generate with scripts/generate_key.py.
    token_encryption_key: str = ""
    public_api_url: str = "http://localhost:8000"
    ui_url: str = "http://localhost:8501"
    google_client_id: str = ""
    google_client_secret: str = ""
    fatsecret_consumer_key: str = ""
    fatsecret_consumer_secret: str = ""
    # Premier unlocks foods.search v6 (better relevance); Basic keys must leave this false.
    fatsecret_premier: bool = False

    # Telegram bot (long-polling worker; no public URL needed). The bot serves exactly one
    # Telegram chat and maps it to one coach profile. TELEGRAM_API_TOKEN comes from
    # `python -m scripts.issue_token <bound-user-id>`.
    telegram_bot_token: str = ""
    telegram_api_token: str = ""
    telegram_api_url: str = "http://localhost:8000"
    telegram_bound_user_id: str = "me"
    telegram_allowed_chat_id: int | None = None

    @field_validator("telegram_allowed_chat_id", mode="before")
    @classmethod
    def empty_str_is_none(cls, value):
        # An empty TELEGRAM_ALLOWED_CHAT_ID= line in .env must not break startup.
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
