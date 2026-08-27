"""知识库创建、文档提交与删除用例：在端口之上编排幂等业务流程。"""

from __future__ import annotations

import hashlib
import uuid
from time import perf_counter

from rag_mvp.application.dto import (
    CreateDatasetCommand,
    CreateDatasetResult,
    DeleteDatasetCommand,
    DeleteDatasetResult,
    DeleteDocumentCommand,
    DeleteDocumentResult,
    SubmitDocumentCommand,
    SubmitDocumentResult,
)
from rag_mvp.domain.enums import DatasetStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.ids import config_digest, file_sha256
from rag_mvp.domain.models import Dataset
from rag_mvp.observability import emit_event
from rag_mvp.ports.metadata import (
    DeleteDatasetRequest,
    DeleteDocumentRequest,
    MetadataRepository,
    SubmitIngestion,
)
from rag_mvp.ports.storage import ObjectStorage


class DocumentService:
    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        metadata: MetadataRepository,
        storage: ObjectStorage,
        *,
        max_upload_bytes: int,
        default_tenant_id: str = "default_tenant",
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be at least 1")
        self._metadata = metadata
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes
        if not default_tenant_id.strip():
            raise ValueError("default_tenant_id must not be empty")
        if (embedding_model is None) != (embedding_dimension is None):
            raise ValueError("embedding model and dimension must be configured together")
        if embedding_model is not None and not embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        if embedding_dimension is not None and embedding_dimension < 1:
            raise ValueError("embedding_dimension must be at least 1")
        self._default_tenant_id = default_tenant_id
        self._embedding_model = embedding_model
        self._embedding_dimension = embedding_dimension

    # 创建该方法负责的领域数据或基础设施状态。
    async def create_dataset(self, command: CreateDatasetCommand) -> CreateDatasetResult:
        started_at = perf_counter()
        if not command.idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        if not command.name.strip():
            raise DomainError(DomainFailure("DATASET_NAME_REQUIRED", "dataset name is required"))
        if not command.embedding_model.strip() or command.embedding_dimension < 1:
            raise DomainError(
                DomainFailure(
                    "INVALID_EMBEDDING_CONFIG",
                    "embedding model and positive dimension are required",
                )
            )
        if self._embedding_model is not None and (
            command.embedding_model != self._embedding_model
            or command.embedding_dimension != self._embedding_dimension
        ):
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_CONFIG_MISMATCH",
                    "dataset embedding configuration must match the running model gateway",
                )
            )
        dataset_id = command.dataset_id or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"rag-dataset:{command.idempotency_key}")
        )
        dataset = await self._metadata.create_dataset(
            Dataset(
                id=dataset_id,
                name=command.name,
                embedding_model=command.embedding_model,
                embedding_dimension=command.embedding_dimension,
                created_at=command.now,
                tenant_id=self._default_tenant_id,
            )
        )
        result = CreateDatasetResult(
            dataset_id=dataset.id,
            name=dataset.name,
            embedding_model=dataset.embedding_model,
            embedding_dimension=dataset.embedding_dimension,
        )
        emit_event(
            "dataset_created",
            request_id=command.request_id,
            dataset_id=dataset.id,
            stage="create_dataset",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return result

    @staticmethod
    # 实现 staging_key 对应的局部职责。
    def staging_key(idempotency_key: str) -> str:
        if not idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"staging/{digest}"

    # 提交该方法负责的领域数据或基础设施状态。
    async def submit_document(self, command: SubmitDocumentCommand) -> SubmitDocumentResult:
        started_at = perf_counter()
        self._validate_upload(command)
        dataset = await self._metadata.get_dataset(command.dataset_id)
        if dataset is None:
            raise DomainError(DomainFailure("DATASET_NOT_FOUND", "dataset does not exist"))
        if dataset.status is not DatasetStatus.ACTIVE:
            raise DomainError(DomainFailure("DATASET_DELETING", "dataset is being deleted"))
        if (
            command.embedding_model is not None
            and command.embedding_model != dataset.embedding_model
        ):
            raise DomainError(
                DomainFailure(
                    "EMBEDDING_MODEL_MISMATCH",
                    "document embedding model must match its dataset",
                )
            )
        actual_sha256 = file_sha256(command.content)
        if command.expected_sha256 is not None and command.expected_sha256 != actual_sha256:
            raise DomainError(
                DomainFailure("SHA256_MISMATCH", "uploaded bytes do not match expected SHA-256")
            )

        staging_key = self.staging_key(command.idempotency_key)
        staging_existed = await self._storage.exists(staging_key)
        if staging_existed:
            existing_sha256 = file_sha256(await self._storage.read(staging_key))
            if existing_sha256 != actual_sha256:
                raise DomainError(
                    DomainFailure(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency key was already used with different bytes",
                    )
                )
        else:
            await self._storage.write(staging_key, command.content)

        digest = config_digest(
            {
                "parser_version": command.parser_version,
                "chunker_config": {
                    "chunk_size": command.chunk_size,
                    "overlap": command.chunk_overlap,
                },
                "embedding_model": dataset.embedding_model,
            }
        )
        try:
            submitted = await self._metadata.submit_ingestion(
                SubmitIngestion(
                    idempotency_key=command.idempotency_key,
                    dataset_id=command.dataset_id,
                    source_name=command.source_name,
                    staging_key=staging_key,
                    file_sha256=actual_sha256,
                    config_digest=digest,
                    now=command.now,
                    target_document_id=command.target_document_id,
                )
            )
        except Exception:
            if not staging_existed:
                await self._storage.delete(staging_key)
            raise

        if not submitted.staging_referenced and not staging_existed:
            await self._storage.delete(staging_key)
        result = SubmitDocumentResult(
            document_id=submitted.document_id,
            job_id=submitted.job_id,
            reused=submitted.reused,
        )
        emit_event(
            "document_submitted",
            request_id=command.request_id,
            job_id=submitted.job_id,
            document_id=submitted.document_id,
            dataset_id=command.dataset_id,
            stage="submit_document",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return result

    # 内部辅助：完成 validate_upload 所需的局部转换或校验。
    def _validate_upload(self, command: SubmitDocumentCommand) -> None:
        if len(command.content) > self._max_upload_bytes:
            raise DomainError(
                DomainFailure("UPLOAD_TOO_LARGE", "upload exceeds configured byte limit")
            )
        if not command.source_name.strip():
            raise DomainError(DomainFailure("SOURCE_NAME_REQUIRED", "source name is required"))
        if command.chunk_size < 1:
            raise DomainError(DomainFailure("INVALID_CHUNK_CONFIG", "chunk size must be positive"))
        if command.chunk_overlap < 0 or command.chunk_overlap >= command.chunk_size:
            raise DomainError(
                DomainFailure(
                    "INVALID_CHUNK_CONFIG",
                    "chunk overlap must be non-negative and smaller than chunk size",
                )
            )

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_document(self, command: DeleteDocumentCommand) -> DeleteDocumentResult:
        if not command.idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        deleted = await self._metadata.delete_document(
            DeleteDocumentRequest(
                idempotency_key=command.idempotency_key,
                document_id=command.document_id,
                now=command.now,
            )
        )
        emit_event(
            "document_deleted",
            request_id=command.request_id,
            job_id=deleted.job_id,
            document_id=deleted.document_id,
            stage="delete_document",
        )
        return DeleteDocumentResult(deleted.document_id, deleted.job_id, deleted.reused)

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_dataset(self, command: DeleteDatasetCommand) -> DeleteDatasetResult:
        if not command.idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        deleted = await self._metadata.delete_dataset(
            DeleteDatasetRequest(command.idempotency_key, command.dataset_id, command.now)
        )
        emit_event(
            "dataset_deleted",
            request_id=command.request_id,
            job_id=deleted.job_id,
            dataset_id=deleted.dataset_id,
            stage="delete_dataset",
        )
        return DeleteDatasetResult(deleted.dataset_id, deleted.job_id, deleted.reused)
