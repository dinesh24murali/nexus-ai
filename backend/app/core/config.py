"""Application settings, read from the environment (and the repo-root `.env` in local dev).

Field names map 1:1 to the variables in `.env.example`. Variables this module does not know about
are ignored, so one shared `.env` can serve the API, worker, CLI and Compose.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repo root. Absent inside containers, where Compose injects env.
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"

_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore", case_sensitive=False)

    # App
    app_env: Literal["development", "test", "production"] = "development"
    app_port: int = 8000
    app_secret: SecretStr = SecretStr("")
    registration_mode: Literal["invite", "open"] = "invite"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # True only when every request comes through our Caddy (it sets X-Forwarded-For): core/net.py.
    trusted_proxy: bool = False

    # Database (psycopg 3 is the only driver)
    database_url: str = Field(
        default="postgresql+psycopg://nexus:nexus@localhost:5432/nexus", repr=False
    )

    # Auth
    auth_secret: SecretStr = SecretStr("")

    # AI (the model factory arrives with TASK-009; only the mode is needed to guard production)
    llm_mode: Literal["real", "fake"] = "real"

    # Vector DB
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = SecretStr("")

    # LLM observability (Langfuse Cloud, EU). Tracing is best-effort and optional.
    langfuse_public_key: SecretStr = SecretStr("")
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"

    # Kill-switch BOOT defaults (docs/02_outputs/10 section 3). At runtime the `runtime_flags` table
    # wins, read through a 60 s cache (core/runtime_flags.py); see `flag_defaults()`.
    tools_disabled: str = ""
    memory_writes_disabled: str = "0"
    chat_model_override: str = ""
    read_only_mode: str = "0"
    router_disabled: str = "0"
    intents_disabled: str = ""

    def flag_defaults(self) -> dict[str, str]:
        return {
            "TOOLS_DISABLED": self.tools_disabled,
            "MEMORY_WRITES_DISABLED": self.memory_writes_disabled,
            "CHAT_MODEL_OVERRIDE": self.chat_model_override,
            "READ_ONLY_MODE": self.read_only_mode,
            "ROUTER_DISABLED": self.router_disabled,
            "INTENTS_DISABLED": self.intents_disabled,
        }

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def langfuse_configured(self) -> bool:
        return bool(
            self.langfuse_public_key.get_secret_value()
            and self.langfuse_secret_key.get_secret_value()
        )

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> Self:
        if not self.is_production:
            return self
        if self.llm_mode == "fake":
            raise ValueError(
                "Refusing to start: LLM_MODE=fake is not allowed with APP_ENV=production"
            )
        for env_name, secret in (
            ("AUTH_SECRET", self.auth_secret),
            ("APP_SECRET", self.app_secret),
        ):
            value = secret.get_secret_value()
            if len(value) < _MIN_SECRET_LENGTH or value.lower() == "changeme":
                raise ValueError(
                    f"Refusing to start: {env_name} must be at least {_MIN_SECRET_LENGTH} "
                    "characters in production (generate with `openssl rand -hex 32`)"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
