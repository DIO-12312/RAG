"""Protobuf-to-application RPC boundary for the RAG service."""

from __future__ import annotations

from collections.abc import AsyncIterator

from rag_mvp.rpc.generated import rag_service_pb2

FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"


def _unavailable(request_id: str) -> rag_service_pb2.BusinessError:
    return rag_service_pb2.BusinessError(
        code=FEATURE_NOT_AVAILABLE,
        message="This capability is not available in the current milestone.",
        retryable=False,
        request_id=request_id,
    )


class RagService:
    """Milestone A transport adapter with an explicitly closed feature surface."""

    async def CreateDataset(
        self,
        request: rag_service_pb2.CreateDatasetRequest,
        context: object,
    ) -> rag_service_pb2.CreateDatasetResponse:
        del context
        return rag_service_pb2.CreateDatasetResponse(error=_unavailable(request.context.request_id))

    async def SubmitDocument(
        self,
        request_iterator: AsyncIterator[rag_service_pb2.UploadDocumentRequest],
        context: object,
    ) -> rag_service_pb2.SubmitDocumentResponse:
        del request_iterator, context
        return rag_service_pb2.SubmitDocumentResponse(error=_unavailable(""))

    async def GetJob(
        self,
        request: rag_service_pb2.GetJobRequest,
        context: object,
    ) -> rag_service_pb2.GetJobResponse:
        del context
        return rag_service_pb2.GetJobResponse(error=_unavailable(request.request_id))

    async def RetryJob(
        self,
        request: rag_service_pb2.RetryJobRequest,
        context: object,
    ) -> rag_service_pb2.RetryJobResponse:
        del context
        return rag_service_pb2.RetryJobResponse(error=_unavailable(request.context.request_id))

    async def CancelJob(
        self,
        request: rag_service_pb2.CancelJobRequest,
        context: object,
    ) -> rag_service_pb2.CancelJobResponse:
        del context
        return rag_service_pb2.CancelJobResponse(error=_unavailable(request.context.request_id))

    async def Retrieve(
        self,
        request: rag_service_pb2.RetrieveRequest,
        context: object,
    ) -> rag_service_pb2.RetrieveResponse:
        del context
        return rag_service_pb2.RetrieveResponse(error=_unavailable(request.request_id))

    async def DeleteDocument(
        self,
        request: rag_service_pb2.DeleteDocumentRequest,
        context: object,
    ) -> rag_service_pb2.DeleteDocumentResponse:
        del context
        return rag_service_pb2.DeleteDocumentResponse(
            error=_unavailable(request.context.request_id)
        )
