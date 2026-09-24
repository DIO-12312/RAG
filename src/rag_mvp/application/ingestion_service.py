"""有条件执行单个摄取 Task 的应用用例：负责状态栅栏，不消费消息。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ports.metadata import MetadataRepository


@dataclass(frozen=True, slots=True)
class IngestionExecution:
    claimed: bool
    completed: bool
    failure: DomainFailure | None = None
    # 认领结果携带的关联字段：Worker 是唯一记录投递结果的位置，
    # 没有这些字段就无法把一条失败日志定位到具体的 Job/Document。
    job_id: str | None = None
    document_id: str | None = None
    dataset_id: str | None = None
    index_version: int | None = None


class IngestionService:
    # 保存元数据端口和单 Task 摄取流水线。
    def __init__(self, metadata: MetadataRepository, pipeline: IngestionPipeline) -> None:
        self._metadata = metadata
        self._pipeline = pipeline

    # 条件认领会同时检查取消标记与 generation fence，防止过期投递写回新版本。
    async def execute(
        self,
        task_id: str,
        delivery_sequence: int,
        now: datetime,
    ) -> IngestionExecution:
        claim = await self._metadata.claim_task(task_id, delivery_sequence, now)
        if claim is None:
            return IngestionExecution(claimed=False, completed=False)

        def result_of(
            *,
            completed: bool,
            failure: DomainFailure | None = None,
        ) -> IngestionExecution:
            """带上本次认领的关联字段，供 Worker 记录可定位的日志。"""

            return IngestionExecution(
                claimed=True,
                completed=completed,
                failure=failure,
                job_id=claim.job.id,
                document_id=claim.document.id if claim.document is not None else None,
                dataset_id=claim.dataset.id,
                index_version=claim.job.index_version,
            )

        async def report(progress: float) -> None:
            """把流水线阶段进度写回 Job，让长耗时摄取对用户可见。"""

            await self._metadata.set_job_progress(task_id, progress, now)

        try:
            chunks = await self._pipeline.execute(claim, on_progress=report)
        except DomainError as error:
            if not error.failure.retryable:
                await self._metadata.fail_task(task_id, error.failure, now)
            return result_of(completed=False, failure=error.failure)
        except Exception as error:
            failure = DomainFailure(
                code="INGESTION_RETRYABLE",
                message=str(error) or type(error).__name__,
                retryable=True,
            )
            return result_of(completed=False, failure=failure)

        if await self._metadata.complete_ingestion(task_id, chunks, now):
            return result_of(completed=True)
        return result_of(
            completed=False,
            failure=DomainFailure(
                code="COMPLETION_FENCE_MISMATCH",
                message="task completion was rejected by its state or document generation fence",
                retryable=False,
            ),
        )
