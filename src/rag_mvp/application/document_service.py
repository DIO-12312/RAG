"""Dataset creation and document submission use cases."""

from __future__ import annotations

import hashlib
import uuid

from rag_mvp.application.dto import (
    CreateDatasetCommand,
    CreateDatasetResult,
    SubmitDocumentCommand,
    SubmitDocumentResult,
)
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.ids import config_digest, file_sha256
from rag_mvp.domain.models import Dataset
from rag_mvp.ports.metadata import MetadataRepository, SubmitIngestion
from rag_mvp.ports.storage import ObjectStorage


class DocumentService:
    def __init__(
        self,
        metadata: MetadataRepository,
        storage: ObjectStorage,
        *,
        max_upload_bytes: int,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be at least 1")
        self._metadata = metadata
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes

    async def create_dataset(self, command: CreateDatasetCommand) -> CreateDatasetResult:
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
            )
        )
        return CreateDatasetResult(
            dataset_id=dataset.id,
            name=dataset.name,
            embedding_model=dataset.embedding_model,
            embedding_dimension=dataset.embedding_dimension,
        )

    @staticmethod
    def staging_key(idempotency_key: str) -> str:
        if not idempotency_key:
            raise DomainError(
                DomainFailure("IDEMPOTENCY_KEY_REQUIRED", "idempotency key is required")
            )
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"staging/{digest}"

    async def submit_document(self, command: SubmitDocumentCommand) -> SubmitDocumentResult:
        self._validate_upload(command)
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
                "embedding_model": command.embedding_model,
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
        return SubmitDocumentResult(
            document_id=submitted.document_id,
            job_id=submitted.job_id,
            reused=submitted.reused,
        )

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
