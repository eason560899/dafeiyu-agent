from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, sourced from environment variables prefixed with ``AI_``."""

    model_config = SettingsConfigDict(
        env_prefix="AI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(default="sqlite:///./agent.db",
                               validation_alias=AliasChoices("DAFEIYU_DATABASE_URL", "AI_DATABASE_URL"))
    provider_base_url: str = Field(default="https://api.deepseek.com",
                                   validation_alias=AliasChoices("DAFEIYU_BASE_URL", "DAFEIYU_PROVIDER_BASE_URL",
                                                                 "AI_PROVIDER_BASE_URL"))
    provider_api_key: str = Field(default="",
                                  validation_alias=AliasChoices("DAFEIYU_API_KEY", "DAFEIYU_PROVIDER_API_KEY",
                                                                "AI_PROVIDER_API_KEY"))
    provider_model: str = Field(default="deepseek-flash",
                                validation_alias=AliasChoices("DAFEIYU_MODEL", "DAFEIYU_PROVIDER_MODEL",
                                                              "AI_PROVIDER_MODEL"))
    provider_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    mock_mode: bool = Field(default=True,
                            validation_alias=AliasChoices("DAFEIYU_MOCK_MODE", "AI_MOCK_MODE"))
    cors_origins: list[str] = Field(default=["http://localhost:3000", "http://localhost:5173"],
                                    validation_alias=AliasChoices("DAFEIYU_CORS_ORIGINS", "AI_CORS_ORIGINS"))
    # When set, every browser/device request under /api must include this value in
    # X-Device-Token. Leaving it empty keeps localhost development frictionless.
    device_token: str = Field(default="",
                              validation_alias=AliasChoices("DAFEIYU_DEVICE_TOKEN", "AI_DEVICE_TOKEN"))
    # A simple kill switch shared by the API and adapters. It is deliberately
    # process-local so a restart always requires an explicit re-enable.
    automation_enabled: bool = Field(default=True,
                                     validation_alias=AliasChoices("DAFEIYU_AUTOMATION_ENABLED", "AI_AUTOMATION_ENABLED"))

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def sqlite_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// database URLs are supported")
        path = self.database_url[len(prefix) :]
        return path or ":memory:"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


