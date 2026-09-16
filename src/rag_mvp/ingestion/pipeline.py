"""单 Task 摄取流水线：解析、规范化、切块、向量化并写入索引。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace

import structlog

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.ids import chunk_id, content_sha256, es_record_id
from rag_mvp.domain.models import Chunk
from rag_mvp.ingestion.checkpoints import Checkpoint, Failpoint
from rag_mvp.ports.chunker import ChunkDraft, Chunker
from rag_mvp.ports.metadata import TaskClaim
from rag_mvp.ports.model import ModelGateway, model_for_dataset
from rag_mvp.ports.parser import Parser
from rag_mvp.ports.search_engine import IndexedChunk, SearchEngine
from rag_mvp.ports.storage import ObjectStorage

# 进度只用于展示：解析后 5%，Embedding 期间 5%→90%，写入索引后 95%，
# 终态仍由 Job 状态机在完成事务里写为 100%。
_PROGRESS_AFTER_PARSE = 0.05
_PROGRESS_AFTER_EMBEDDING = 0.9
_PROGRESS_AFTER_INDEX = 0.95
_PROGRESS_MAX = 0.95
# 每组 Chunk 数：过小会让进度更新过于频繁，过大则进度条长时间不动。
_EMBEDDING_PROGRESS_CHUNKS = 128


_LOGGER = structlog.get_logger("rag_mvp")


# 进度只用于展示：即使写进度失败也不能让已经完成的摄取失败。
async def _report_progress(
    on_progress: Callable[[float], Awaitable[None]] | None,
    progress: float,
) -> None:
    if on_progress is None:
        return
    with suppress(Exception) as suppressed:
        await on_progress(min(progress, _PROGRESS_MAX))
    if suppressed is not None:
        _LOGGER.info("ingestion_progress_skipped", error=type(suppressed).__name__)


class IngestionPipeline:
    # 初始化该对象的依赖、配置或受控资源。
    def __init__(
        self,
        storage: ObjectStorage,
        parser: Parser,
        chunker: Chunker,
        model: ModelGateway,
        search: SearchEngine,
        *,
        failpoint: Failpoint | None = None,
    ) -> None:
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._model = model
        self._search = search
        self._failpoint = failpoint

    # 关键语义：先确认正式对象，再按固定顺序构造 Chunk/ES record_id；
    # 失败会交由上层 Task 状态机和 JetStream redelivery 收敛，不在此处确认消息。
    async def execute(
        self,
        claim: TaskClaim,
        *,
        on_progress: Callable[[float], Awaitable[None]] | None = None,
    ) -> tuple[Chunk, ...]:
        document = claim.document
        if document is None:
            raise RuntimeError("ingestion claim is missing its document")
        object_key = document.object_key
        if object_key is None:
            raise DomainError(
                DomainFailure(
                    code="OBJECT_NOT_READY",
                    message="document object is not ready for ingestion",
                    retryable=True,
                )
            )

        source = await self._storage.read(object_key)
        segments = await self._parser.parse(document.source_name, source)
        await _report_progress(on_progress, _PROGRESS_AFTER_PARSE)
        await self._checkpoint(Checkpoint.AFTER_PARSE)
        drafts = await self._chunker.split(segments)
        if not drafts:
            raise DomainError(
                DomainFailure(
                    code="EMPTY_DOCUMENT",
                    message="document produced no indexable chunks",
                    retryable=False,
                )
            )

        # RAGFlow 的逻辑 Chunk ID 只取决于最终正文和 document_id。同一文档中
        # 完全相同的正文因此必须折叠为一个逻辑 Chunk，否则 ES 会覆盖同一物理
        # 记录，而 MySQL manifest 会因唯一键重复而在完成阶段失败。按首次出现
        # 顺序去重也能避免为注定折叠的内容重复调用 Embedding。
        unique_drafts: dict[str, ChunkDraft] = {}
        pages: dict[str, set[int]] = {}
        for draft in drafts:
            logical_id = chunk_id(draft.content_with_weight, document.id)
            unique_drafts.setdefault(logical_id, draft)
            if draft.metadata.get("source_type") == "pdf" and draft.locator.page_number is not None:
                pages.setdefault(logical_id, set()).add(draft.locator.page_number)
        for logical_id, page_numbers in pages.items():
            draft = unique_drafts[logical_id]
            unique_drafts[logical_id] = replace(
                draft,
                metadata={**draft.metadata, "page_numbers": json.dumps(sorted(page_numbers))},
            )

        model = model_for_dataset(self._model, claim.dataset)
        # 按固定分组调用 Embedding：既让长文档的进度可见，也让适配器里的
        # 节流与批次自适应状态在整篇文档上持续生效。
        contents = [draft.content_with_weight for draft in unique_drafts.values()]
        vectors: list[tuple[float, ...]] = []
        for offset in range(0, len(contents), _EMBEDDING_PROGRESS_CHUNKS):
            group = contents[offset : offset + _EMBEDDING_PROGRESS_CHUNKS]
            vectors.extend(await model.embed(group))
            done = offset + len(group)
            await _report_progress(
                on_progress,
                _PROGRESS_AFTER_PARSE
                + (_PROGRESS_AFTER_EMBEDDING - _PROGRESS_AFTER_PARSE) * done / len(contents),
            )
        if len(vectors) != len(unique_drafts):
            raise DomainError(
                DomainFailure(
                    code="EMBEDDING_COUNT_MISMATCH",
                    message="embedding result count does not match chunk count",
                    retryable=True,
                )
            )

        chunks = tuple(
            Chunk(
                id=logical_id,
                document_id=document.id,
                index_version=claim.job.index_version,
                ordinal=draft.ordinal,
                content_with_weight=draft.content_with_weight,
                content_sha256=content_sha256(draft.content_with_weight),
                source_name=document.source_name,
                locator=draft.locator,
                metadata=draft.metadata,
            )
            for logical_id, draft in unique_drafts.items()
        )
        indexed = tuple(
            IndexedChunk(
                record_id=es_record_id(chunk.document_id, chunk.index_version, chunk.id),
                dataset_id=document.dataset_id,
                chunk=chunk,
                vector=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        await self._search.upsert_chunks(indexed)
        await _report_progress(on_progress, _PROGRESS_AFTER_INDEX)
        await self._checkpoint(Checkpoint.AFTER_INDEX_WRITE)
        return chunks

    # 内部辅助：完成 checkpoint 所需的局部转换或校验。
    async def _checkpoint(self, checkpoint: Checkpoint) -> None:
        if self._failpoint is not None:
            await self._failpoint(checkpoint)
