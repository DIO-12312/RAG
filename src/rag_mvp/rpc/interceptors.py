"""Transport-level business error conversion helpers."""

from rag_mvp.domain.errors import DomainFailure
from rag_mvp.rpc.generated import rag_service_pb2


def business_error(failure: DomainFailure, request_id: str) -> rag_service_pb2.BusinessError:
    return rag_service_pb2.BusinessError(
        code=failure.code,
        message=failure.message,
        retryable=failure.retryable,
        request_id=request_id,
    )
