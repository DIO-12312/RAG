"""Process settings with no import-time I/O or connection side effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MYSQL_DSN = "mysql+asyncmy://rag:rag@mysql:3306/rag"


class Environment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """Complete provider configuration required by Server and Worker roles."""

    endpoint: str
    model: str
    api_key: SecretStr
    dimension: int
    batch_size: int
    timeout_seconds: float
    max_retries: int


class Settings(BaseSettings):
    """Configuration passed explicitly to process entry points and the container."""

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
    )

    environment: Environment = Environment.DEVELOPMENT

    grpc_host: str = "0.0.0.0"  # noqa: S104 - server bind address is configurable
    grpc_port: int = Field(default=50051, ge=1, le=65535)
    grpc_shutdown_timeout_seconds: float = Field(default=10.0, gt=0)
    grpc_reflection: bool = True

    mysql_dsn: str = DEFAULT_MYSQL_DSN
    elasticsearch_url: str = "http://elasticsearch:9200"
    elasticsearch_index: str = "rag-chunks-v1"
    nats_url: str = "nats://nats:4222"
    nats_stream: str = "RAG_TASKS"
    nats_consumer: str = "rag-ingestion-worker"
    nats_subject: str = "rag.tasks"
    nats_ack_wait_seconds: float = Field(default=60.0, gt=0)
    nats_max_deliver: int = Field(default=3, ge=1)
    object_root: Path = Path("data/objects")

    default_tenant_id: str = "default_tenant"
    embedding_model_url: str | None = Field(
        default=None,
        validation_alias="EMBEDDING_MODEL_URL",
    )
    embedding_model_name: str | None = Field(
        default=None,
        validation_alias="EMBEDDING_MODEL_NAME",
    )
    embedding_model_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="EMBEDDING_MODEL_API_KEY",
    )
    embedding_model_dimension: int | None = Field(
        default=None,
        gt=0,
        validation_alias="EMBEDDING_MODEL_DIMENSION",
    )
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    embedding_max_retries: int = Field(default=3, ge=0, le=10)

    log_level: str = "INFO"
    log_json: bool = True

    @property
    def grpc_address(self) -> str:
        """Return the host:port address accepted by gRPC."""

        return f"{self.grpc_host}:{self.grpc_port}"

    def require_embedding_profile(self) -> EmbeddingProfile:
        """Return complete model settings or reject missing/partial role configuration."""

        api_key = self.embedding_model_api_key
        values_present = (
            bool(self.embedding_model_url and self.embedding_model_url.strip()),
            bool(self.embedding_model_name and self.embedding_model_name.strip()),
            bool(api_key and api_key.get_secret_value().strip()),
            self.embedding_model_dimension is not None,
        )
        if not all(values_present):
            raise ValueError("embedding model configuration is incomplete or missing")

        assert self.embedding_model_url is not None
        assert self.embedding_model_name is not None
        assert api_key is not None
        assert self.embedding_model_dimension is not None
        endpoint = self.embedding_model_url.rstrip("/")
        if not endpoint.endswith("/embeddings"):
            endpoint = f"{endpoint}/embeddings"
        return EmbeddingProfile(
            endpoint=endpoint,
            model=self.embedding_model_name,
            api_key=api_key,
            dimension=self.embedding_model_dimension,
            batch_size=self.embedding_batch_size,
            timeout_seconds=self.embedding_timeout_seconds,
            max_retries=self.embedding_max_retries,
        )

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
