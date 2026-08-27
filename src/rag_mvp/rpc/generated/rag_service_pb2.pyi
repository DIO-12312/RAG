from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class JobStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_STATUS_UNSPECIFIED: _ClassVar[JobStatus]
    JOB_STATUS_PENDING: _ClassVar[JobStatus]
    JOB_STATUS_RUNNING: _ClassVar[JobStatus]
    JOB_STATUS_SUCCEEDED: _ClassVar[JobStatus]
    JOB_STATUS_FAILED: _ClassVar[JobStatus]
    JOB_STATUS_CANCELLED: _ClassVar[JobStatus]

class TaskStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TASK_STATUS_UNSPECIFIED: _ClassVar[TaskStatus]
    TASK_STATUS_PENDING: _ClassVar[TaskStatus]
    TASK_STATUS_RUNNING: _ClassVar[TaskStatus]
    TASK_STATUS_SUCCEEDED: _ClassVar[TaskStatus]
    TASK_STATUS_FAILED: _ClassVar[TaskStatus]
    TASK_STATUS_CANCELLED: _ClassVar[TaskStatus]

class JobType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    JOB_TYPE_UNSPECIFIED: _ClassVar[JobType]
    JOB_TYPE_INGEST_DOCUMENT: _ClassVar[JobType]
    JOB_TYPE_DELETE_DOCUMENT: _ClassVar[JobType]
    JOB_TYPE_CLEANUP_INDEX_VERSION: _ClassVar[JobType]
    JOB_TYPE_DELETE_DATASET: _ClassVar[JobType]

class DocumentStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DOCUMENT_STATUS_UNSPECIFIED: _ClassVar[DocumentStatus]
    DOCUMENT_STATUS_PENDING: _ClassVar[DocumentStatus]
    DOCUMENT_STATUS_READY: _ClassVar[DocumentStatus]
    DOCUMENT_STATUS_FAILED: _ClassVar[DocumentStatus]
    DOCUMENT_STATUS_DELETED: _ClassVar[DocumentStatus]
JOB_STATUS_UNSPECIFIED: JobStatus
JOB_STATUS_PENDING: JobStatus
JOB_STATUS_RUNNING: JobStatus
JOB_STATUS_SUCCEEDED: JobStatus
JOB_STATUS_FAILED: JobStatus
JOB_STATUS_CANCELLED: JobStatus
TASK_STATUS_UNSPECIFIED: TaskStatus
TASK_STATUS_PENDING: TaskStatus
TASK_STATUS_RUNNING: TaskStatus
TASK_STATUS_SUCCEEDED: TaskStatus
TASK_STATUS_FAILED: TaskStatus
TASK_STATUS_CANCELLED: TaskStatus
JOB_TYPE_UNSPECIFIED: JobType
JOB_TYPE_INGEST_DOCUMENT: JobType
JOB_TYPE_DELETE_DOCUMENT: JobType
JOB_TYPE_CLEANUP_INDEX_VERSION: JobType
JOB_TYPE_DELETE_DATASET: JobType
DOCUMENT_STATUS_UNSPECIFIED: DocumentStatus
DOCUMENT_STATUS_PENDING: DocumentStatus
DOCUMENT_STATUS_READY: DocumentStatus
DOCUMENT_STATUS_FAILED: DocumentStatus
DOCUMENT_STATUS_DELETED: DocumentStatus

class RequestContext(_message.Message):
    __slots__ = ("request_id", "idempotency_key")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    idempotency_key: str
    def __init__(self, request_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class BusinessError(_message.Message):
    __slots__ = ("code", "message", "retryable", "request_id")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    request_id: str
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: _Optional[bool] = ..., request_id: _Optional[str] = ...) -> None: ...

class RetrievalConfig(_message.Message):
    __slots__ = ("dense_top_k", "sparse_top_k", "rrf_k", "rerank_enabled", "rerank_top_n", "max_context_tokens")
    DENSE_TOP_K_FIELD_NUMBER: _ClassVar[int]
    SPARSE_TOP_K_FIELD_NUMBER: _ClassVar[int]
    RRF_K_FIELD_NUMBER: _ClassVar[int]
    RERANK_ENABLED_FIELD_NUMBER: _ClassVar[int]
    RERANK_TOP_N_FIELD_NUMBER: _ClassVar[int]
    MAX_CONTEXT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    dense_top_k: int
    sparse_top_k: int
    rrf_k: int
    rerank_enabled: bool
    rerank_top_n: int
    max_context_tokens: int
    def __init__(self, dense_top_k: _Optional[int] = ..., sparse_top_k: _Optional[int] = ..., rrf_k: _Optional[int] = ..., rerank_enabled: _Optional[bool] = ..., rerank_top_n: _Optional[int] = ..., max_context_tokens: _Optional[int] = ...) -> None: ...

class CreateDatasetRequest(_message.Message):
    __slots__ = ("context", "name", "embedding_model", "embedding_dimension", "retrieval_config")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_MODEL_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_DIMENSION_FIELD_NUMBER: _ClassVar[int]
    RETRIEVAL_CONFIG_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    name: str
    embedding_model: str
    embedding_dimension: int
    retrieval_config: RetrievalConfig
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., name: _Optional[str] = ..., embedding_model: _Optional[str] = ..., embedding_dimension: _Optional[int] = ..., retrieval_config: _Optional[_Union[RetrievalConfig, _Mapping]] = ...) -> None: ...

class CreateDatasetResult(_message.Message):
    __slots__ = ("dataset_id", "name", "embedding_model", "embedding_dimension")
    DATASET_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_MODEL_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_DIMENSION_FIELD_NUMBER: _ClassVar[int]
    dataset_id: str
    name: str
    embedding_model: str
    embedding_dimension: int
    def __init__(self, dataset_id: _Optional[str] = ..., name: _Optional[str] = ..., embedding_model: _Optional[str] = ..., embedding_dimension: _Optional[int] = ...) -> None: ...

class CreateDatasetResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: CreateDatasetResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[CreateDatasetResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class DeleteDatasetRequest(_message.Message):
    __slots__ = ("context", "dataset_id")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    DATASET_ID_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    dataset_id: str
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., dataset_id: _Optional[str] = ...) -> None: ...

class DeleteDatasetResult(_message.Message):
    __slots__ = ("dataset_id", "job_id")
    DATASET_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    dataset_id: str
    job_id: str
    def __init__(self, dataset_id: _Optional[str] = ..., job_id: _Optional[str] = ...) -> None: ...

class DeleteDatasetResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: DeleteDatasetResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[DeleteDatasetResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class UploadHeader(_message.Message):
    __slots__ = ("context", "dataset_id", "source_name", "expected_sha256", "target_document_id")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    DATASET_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_SHA256_FIELD_NUMBER: _ClassVar[int]
    TARGET_DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    dataset_id: str
    source_name: str
    expected_sha256: str
    target_document_id: str
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., dataset_id: _Optional[str] = ..., source_name: _Optional[str] = ..., expected_sha256: _Optional[str] = ..., target_document_id: _Optional[str] = ...) -> None: ...

class UploadDocumentRequest(_message.Message):
    __slots__ = ("header", "data")
    HEADER_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    header: UploadHeader
    data: bytes
    def __init__(self, header: _Optional[_Union[UploadHeader, _Mapping]] = ..., data: _Optional[bytes] = ...) -> None: ...

class SubmitDocumentResult(_message.Message):
    __slots__ = ("document_id", "job_id", "reused")
    DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    REUSED_FIELD_NUMBER: _ClassVar[int]
    document_id: str
    job_id: str
    reused: bool
    def __init__(self, document_id: _Optional[str] = ..., job_id: _Optional[str] = ..., reused: _Optional[bool] = ...) -> None: ...

class SubmitDocumentResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: SubmitDocumentResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[SubmitDocumentResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class GetJobRequest(_message.Message):
    __slots__ = ("request_id", "job_id")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    job_id: str
    def __init__(self, request_id: _Optional[str] = ..., job_id: _Optional[str] = ...) -> None: ...

class JobFailure(_message.Message):
    __slots__ = ("code", "message", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...

class JobResult(_message.Message):
    __slots__ = ("job_id", "document_id", "type", "status", "progress", "failure", "retryable", "retry_count", "cancel_requested", "task_status", "dataset_id")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    RETRY_COUNT_FIELD_NUMBER: _ClassVar[int]
    CANCEL_REQUESTED_FIELD_NUMBER: _ClassVar[int]
    TASK_STATUS_FIELD_NUMBER: _ClassVar[int]
    DATASET_ID_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    document_id: str
    type: JobType
    status: JobStatus
    progress: float
    failure: JobFailure
    retryable: bool
    retry_count: int
    cancel_requested: bool
    task_status: TaskStatus
    dataset_id: str
    def __init__(self, job_id: _Optional[str] = ..., document_id: _Optional[str] = ..., type: _Optional[_Union[JobType, str]] = ..., status: _Optional[_Union[JobStatus, str]] = ..., progress: _Optional[float] = ..., failure: _Optional[_Union[JobFailure, _Mapping]] = ..., retryable: _Optional[bool] = ..., retry_count: _Optional[int] = ..., cancel_requested: _Optional[bool] = ..., task_status: _Optional[_Union[TaskStatus, str]] = ..., dataset_id: _Optional[str] = ...) -> None: ...

class GetJobResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: JobResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[JobResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class RetryJobRequest(_message.Message):
    __slots__ = ("context", "job_id")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    job_id: str
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., job_id: _Optional[str] = ...) -> None: ...

class RetryJobResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: JobResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[JobResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class CancelJobRequest(_message.Message):
    __slots__ = ("context", "job_id")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    job_id: str
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., job_id: _Optional[str] = ...) -> None: ...

class CancelJobResult(_message.Message):
    __slots__ = ("job_id", "job_status", "task_status", "cancel_requested")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_STATUS_FIELD_NUMBER: _ClassVar[int]
    TASK_STATUS_FIELD_NUMBER: _ClassVar[int]
    CANCEL_REQUESTED_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    job_status: JobStatus
    task_status: TaskStatus
    cancel_requested: bool
    def __init__(self, job_id: _Optional[str] = ..., job_status: _Optional[_Union[JobStatus, str]] = ..., task_status: _Optional[_Union[TaskStatus, str]] = ..., cancel_requested: _Optional[bool] = ...) -> None: ...

class CancelJobResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: CancelJobResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[CancelJobResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class MetadataFilter(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class RetrieveRequest(_message.Message):
    __slots__ = ("request_id", "dataset_id", "query", "filters", "top_k", "enable_rerank", "max_context_tokens")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    DATASET_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    TOP_K_FIELD_NUMBER: _ClassVar[int]
    ENABLE_RERANK_FIELD_NUMBER: _ClassVar[int]
    MAX_CONTEXT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    dataset_id: str
    query: str
    filters: _containers.RepeatedCompositeFieldContainer[MetadataFilter]
    top_k: int
    enable_rerank: bool
    max_context_tokens: int
    def __init__(self, request_id: _Optional[str] = ..., dataset_id: _Optional[str] = ..., query: _Optional[str] = ..., filters: _Optional[_Iterable[_Union[MetadataFilter, _Mapping]]] = ..., top_k: _Optional[int] = ..., enable_rerank: _Optional[bool] = ..., max_context_tokens: _Optional[int] = ...) -> None: ...

class Locator(_message.Message):
    __slots__ = ("page_number", "start_line", "end_line", "symbol", "language", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    PAGE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    START_LINE_FIELD_NUMBER: _ClassVar[int]
    END_LINE_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    page_number: int
    start_line: int
    end_line: int
    symbol: str
    language: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, page_number: _Optional[int] = ..., start_line: _Optional[int] = ..., end_line: _Optional[int] = ..., symbol: _Optional[str] = ..., language: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ScoreBreakdown(_message.Message):
    __slots__ = ("dense_score", "sparse_score", "fusion_score", "rerank_score")
    DENSE_SCORE_FIELD_NUMBER: _ClassVar[int]
    SPARSE_SCORE_FIELD_NUMBER: _ClassVar[int]
    FUSION_SCORE_FIELD_NUMBER: _ClassVar[int]
    RERANK_SCORE_FIELD_NUMBER: _ClassVar[int]
    dense_score: float
    sparse_score: float
    fusion_score: float
    rerank_score: float
    def __init__(self, dense_score: _Optional[float] = ..., sparse_score: _Optional[float] = ..., fusion_score: _Optional[float] = ..., rerank_score: _Optional[float] = ...) -> None: ...

class Evidence(_message.Message):
    __slots__ = ("chunk_id", "document_id", "content_with_weight", "source_name", "locator", "metadata", "scores", "index_version")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_WITH_WEIGHT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_NAME_FIELD_NUMBER: _ClassVar[int]
    LOCATOR_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    SCORES_FIELD_NUMBER: _ClassVar[int]
    INDEX_VERSION_FIELD_NUMBER: _ClassVar[int]
    chunk_id: str
    document_id: str
    content_with_weight: str
    source_name: str
    locator: Locator
    metadata: _containers.ScalarMap[str, str]
    scores: ScoreBreakdown
    index_version: int
    def __init__(self, chunk_id: _Optional[str] = ..., document_id: _Optional[str] = ..., content_with_weight: _Optional[str] = ..., source_name: _Optional[str] = ..., locator: _Optional[_Union[Locator, _Mapping]] = ..., metadata: _Optional[_Mapping[str, str]] = ..., scores: _Optional[_Union[ScoreBreakdown, _Mapping]] = ..., index_version: _Optional[int] = ...) -> None: ...

class RetrieveResult(_message.Message):
    __slots__ = ("evidence", "estimated_tokens", "omitted_chunk_ids")
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OMITTED_CHUNK_IDS_FIELD_NUMBER: _ClassVar[int]
    evidence: _containers.RepeatedCompositeFieldContainer[Evidence]
    estimated_tokens: int
    omitted_chunk_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, evidence: _Optional[_Iterable[_Union[Evidence, _Mapping]]] = ..., estimated_tokens: _Optional[int] = ..., omitted_chunk_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class RetrieveResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: RetrieveResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[RetrieveResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...

class DeleteDocumentRequest(_message.Message):
    __slots__ = ("context", "document_id")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    context: RequestContext
    document_id: str
    def __init__(self, context: _Optional[_Union[RequestContext, _Mapping]] = ..., document_id: _Optional[str] = ...) -> None: ...

class DeleteDocumentResult(_message.Message):
    __slots__ = ("document_id", "job_id", "document_status")
    DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    DOCUMENT_STATUS_FIELD_NUMBER: _ClassVar[int]
    document_id: str
    job_id: str
    document_status: DocumentStatus
    def __init__(self, document_id: _Optional[str] = ..., job_id: _Optional[str] = ..., document_status: _Optional[_Union[DocumentStatus, str]] = ...) -> None: ...

class DeleteDocumentResponse(_message.Message):
    __slots__ = ("result", "error")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    result: DeleteDocumentResult
    error: BusinessError
    def __init__(self, result: _Optional[_Union[DeleteDocumentResult, _Mapping]] = ..., error: _Optional[_Union[BusinessError, _Mapping]] = ...) -> None: ...
