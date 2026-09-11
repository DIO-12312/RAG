"""Embedding 与 Rerank 模型能力边界，禁止模型 SDK 泄漏到应用层。"""

from typing import Protocol, runtime_checkable

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Dataset


class ModelGateway(Protocol):
    """Provide embedding and reranking without leaking a model SDK."""

    # 实现 embed 对应的局部职责。
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    # 实现 rerank 对应的局部职责。
    async def rerank(self, query: str, passages: list[str]) -> list[float]: ...


@runtime_checkable
class DatasetModelGateway(Protocol):
    def for_dataset(self, dataset: Dataset) -> ModelGateway: ...


@runtime_checkable
class RerankProfileGateway(Protocol):
    def for_rerank(self, encrypted_profile: str, dataset_id: str) -> ModelGateway: ...


def model_for_rerank(model: ModelGateway, encrypted_profile: str, dataset_id: str) -> ModelGateway:
    if not encrypted_profile:
        return model
    if not isinstance(model, RerankProfileGateway):
        raise DomainError(DomainFailure("RERANK_PROFILE_UNSUPPORTED", "rerank gateway unavailable"))
    return model.for_rerank(encrypted_profile, dataset_id)


def model_for_dataset(model: ModelGateway, dataset: Dataset) -> ModelGateway:
    if dataset.encrypted_embedding_profile:
        if not isinstance(model, DatasetModelGateway):
            raise DomainError(
                DomainFailure("EMBEDDING_PROFILE_UNSUPPORTED", "dataset model gateway unavailable")
            )
        return model.for_dataset(dataset)
    return model
