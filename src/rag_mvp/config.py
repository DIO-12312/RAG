"""进程配置：导入时不产生 I/O 或连接副作用，便于各运行角色显式装配。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

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


@dataclass(frozen=True, slots=True)
class ElasticsearchProfile:
    """Validated HTTPS credentials consumed only when creating the ES client."""

    endpoint: str
    username: str
    password: SecretStr
    ca_cert: Path


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
    migrations_root: Path = Path(".")
    elasticsearch_url: str = "https://elasticsearch:9200"
    elasticsearch_username: str = "rag_mvp"
    elasticsearch_password: SecretStr | None = None
    elasticsearch_password_file: Path | None = None
    elasticsearch_ca_cert: Path | None = None
    elasticsearch_index: str = "rag-chunks-v1"
    nats_url: str = "nats://nats:4222"
    nats_stream: str = "RAG_TASKS"
    nats_consumer: str = "rag-ingestion-worker"
    nats_subject: str = "rag.tasks"
    nats_ack_wait_seconds: float = Field(default=60.0, gt=0)
    nats_max_deliver: int = Field(default=3, ge=1)
    object_root: Path = Path("data/objects")

    max_upload_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    parser_version: str = "source-router-v1"
    chunk_size: int = Field(default=800, ge=1)
    chunk_overlap: int = Field(default=120, ge=0)
    max_user_retries: int = Field(default=3, ge=1)
    worker_idle_interval_seconds: float = Field(default=0.1, gt=0)
    outbox_poll_interval_seconds: float = Field(default=0.25, gt=0)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    max_finalize_attempts: int = Field(default=5, ge=1)
    staging_sweep_interval_seconds: float = Field(default=60.0, gt=0)
    staging_ttl_seconds: float = Field(default=3600.0, gt=0)
    failpoint_root: Path | None = None
    failpoint_checkpoints: str = ""

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
    # 实现 grpc_address 对应的局部职责。
    def grpc_address(self) -> str:
        """Return the host:port address accepted by gRPC."""

        return f"{self.grpc_host}:{self.grpc_port}"

    # 实现 require_embedding_profile 对应的局部职责。
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

    # 实现 require_elasticsearch_profile 对应的局部职责。
    def require_elasticsearch_profile(self) -> ElasticsearchProfile:
        """Return fail-closed HTTPS credentials for the Elasticsearch adapter."""

        endpoint = self.elasticsearch_url.strip()
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Elasticsearch URL must be a complete HTTPS endpoint")
        username = self.elasticsearch_username.strip()
        if not username:
            raise ValueError("Elasticsearch username must not be empty")
        configured_password = self.elasticsearch_password
        password_file = self.elasticsearch_password_file
        if configured_password is not None and password_file is not None:
            raise ValueError("Elasticsearch password must use exactly one secret source")
        if configured_password is None and password_file is None:
            raise ValueError("Elasticsearch password is not configured")
        if password_file is not None:
            try:
                value = password_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError("Elasticsearch password file is unavailable") from exc
            password = SecretStr(value)
        else:
            assert configured_password is not None
            password = configured_password
        if not password.get_secret_value().strip():
            raise ValueError("Elasticsearch password must not be empty")
        ca_cert = self.elasticsearch_ca_cert
        if ca_cert is None or not str(ca_cert).strip():
            raise ValueError("Elasticsearch CA certificate path is not configured")
        return ElasticsearchProfile(
            endpoint=endpoint,
            username=username,
            password=password,
            ca_cert=ca_cert,
        )

    @property
    # 实现 failpoint_checkpoint_names 对应的局部职责。
    def failpoint_checkpoint_names(self) -> frozenset[str]:
        """Return the explicitly configured test-only checkpoint names."""

        return frozenset(
            name.strip() for name in self.failpoint_checkpoints.split(",") if name.strip()
        )

    @model_validator(mode="after")
    # 校验该方法负责的领域数据或基础设施状态。
    def validate_production_safety(self) -> Self:
        """Reject development-only settings in production."""

        if not self.parser_version.strip():
            raise ValueError("parser_version must not be empty")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        checkpoint_names = self.failpoint_checkpoint_names
        failpoints_configured = self.failpoint_root is not None or bool(checkpoint_names)
        if failpoints_configured:
            if self.environment is not Environment.TEST:
                raise ValueError("failpoints are allowed only in test environment")
            if self.failpoint_root is None or not checkpoint_names:
                raise ValueError("failpoint root and checkpoints must be configured together")
            from rag_mvp.ingestion.checkpoints import Checkpoint

            supported = {checkpoint.value for checkpoint in Checkpoint}
            unknown = checkpoint_names - supported
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(f"unknown failpoint checkpoints: {names}")
        if self.environment is not Environment.PRODUCTION:
            return self
        if self.grpc_reflection:
            raise ValueError("gRPC reflection is disabled in production")
        if self.mysql_dsn == DEFAULT_MYSQL_DSN:
            raise ValueError("production MySQL credentials must not use development defaults")
        return self


# 加载该方法负责的领域数据或基础设施状态。
def load_settings() -> Settings:
    """Load settings at an explicit process boundary."""

    return Settings()
