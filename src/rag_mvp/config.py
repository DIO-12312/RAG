"""Process settings with no import-time I/O or connection side effects."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MYSQL_DSN = "mysql+asyncmy://rag:rag@mysql:3306/rag"


class Environment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Configuration passed explicitly to process entry points and the container."""

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    environment: Environment = Environment.DEVELOPMENT

    grpc_host: str = "0.0.0.0"  # noqa: S104 - server bind address is configurable
    grpc_port: int = Field(default=50051, ge=1, le=65535)
    grpc_shutdown_timeout_seconds: float = Field(default=10.0, gt=0)
    grpc_reflection: bool = True

    mysql_dsn: str = DEFAULT_MYSQL_DSN
    elasticsearch_url: str = "http://elasticsearch:9200"
    nats_url: str = "nats://nats:4222"
    nats_stream: str = "RAG_TASKS"
    nats_consumer: str = "rag-ingestion-worker"
    nats_subject: str = "rag.tasks"
    object_root: Path = Path("data/objects")

    log_level: str = "INFO"
    log_json: bool = True

    @property
    def grpc_address(self) -> str:
        """Return the host:port address accepted by gRPC."""

        return f"{self.grpc_host}:{self.grpc_port}"

    @model_validator(mode="after")
    def validate_production_safety(self) -> Self:
        """Reject development-only settings in production."""

        if self.environment is not Environment.PRODUCTION:
            return self
        if self.grpc_reflection:
            raise ValueError("gRPC reflection is disabled in production")
        if self.mysql_dsn == DEFAULT_MYSQL_DSN:
            raise ValueError("production MySQL credentials must not use development defaults")
        return self


def load_settings() -> Settings:
    """Load settings at an explicit process boundary."""

    return Settings()
