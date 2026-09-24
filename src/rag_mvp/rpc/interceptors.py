"""传输层业务错误转换辅助函数，保持领域错误码在 gRPC 响应中的稳定性。"""

from rag_mvp.domain.errors import DomainFailure
from rag_mvp.rpc.generated import rag_service_pb2


# 将领域失败对象映射为带请求 ID 的 protobuf 业务错误。
def business_error(failure: DomainFailure, request_id: str) -> rag_service_pb2.BusinessError:
    return rag_service_pb2.BusinessError(
        code=failure.code,
        message=failure.message,
        retryable=failure.retryable,
        request_id=request_id,
    )
