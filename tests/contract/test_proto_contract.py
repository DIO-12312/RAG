from __future__ import annotations

# 校验版本化 protobuf 消息和服务方法不发生破坏性漂移。
from google.protobuf.descriptor import Descriptor, FieldDescriptor

from rag_mvp.rpc.generated import rag_service_pb2


def _message(name: str) -> Descriptor:
    """构造本测试所需的输入、替身或运行环境。"""
    return rag_service_pb2.DESCRIPTOR.message_types_by_name[name]


def test_rag_service_defines_the_complete_rpc_surface() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    service = rag_service_pb2.DESCRIPTOR.services_by_name["RagService"]
    methods = {method.name: method for method in service.methods}

    assert set(methods) == {
        "CreateDataset",
        "DeleteDataset",
        "SubmitDocument",
        "GetJob",
        "RetryJob",
        "CancelJob",
        "Retrieve",
        "DeleteDocument",
    }
    assert methods["SubmitDocument"].client_streaming is True
    assert methods["SubmitDocument"].server_streaming is False
    assert all(
        not method.client_streaming for name, method in methods.items() if name != "SubmitDocument"
    )
    assert all(not method.server_streaming for method in methods.values())


def test_every_response_has_result_and_business_error_outcome() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    response_names = (
        "CreateDatasetResponse",
        "DeleteDatasetResponse",
        "SubmitDocumentResponse",
        "GetJobResponse",
        "RetryJobResponse",
        "CancelJobResponse",
        "RetrieveResponse",
        "DeleteDocumentResponse",
    )

    for response_name in response_names:
        response = _message(response_name)
        assert set(response.oneofs_by_name) == {"outcome"}
        outcome = response.oneofs_by_name["outcome"]
        assert [field.name for field in outcome.fields] == ["result", "error"]
        assert outcome.fields[1].message_type.full_name == "rag.v1.BusinessError"


def test_upload_request_is_a_header_or_data_frame() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    request = _message("UploadDocumentRequest")

    assert set(request.oneofs_by_name) == {"payload"}
    payload = request.oneofs_by_name["payload"]
    assert [field.name for field in payload.fields] == ["header", "data"]
    assert payload.fields[0].message_type.full_name == "rag.v1.UploadHeader"
    assert payload.fields[1].type == FieldDescriptor.TYPE_BYTES


def test_idempotency_context_is_only_used_by_commands() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    command_requests = (
        "CreateDatasetRequest",
        "DeleteDatasetRequest",
        "UploadHeader",
        "RetryJobRequest",
        "CancelJobRequest",
        "DeleteDocumentRequest",
    )

    for request_name in command_requests:
        context = _message(request_name).fields_by_name["context"]
        assert context.message_type.full_name == "rag.v1.RequestContext"

    assert set(_message("GetJobRequest").fields_by_name) == {"request_id", "job_id"}
    retrieve_fields = _message("RetrieveRequest").fields_by_name
    assert "request_id" in retrieve_fields
    assert "context" not in retrieve_fields
    assert "idempotency_key" not in retrieve_fields


def test_delete_dataset_contract_keeps_job_history_scoped_to_dataset() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    request = _message("DeleteDatasetRequest")
    result = _message("DeleteDatasetResult")
    job = _message("JobResult")

    assert [(field.name, field.number) for field in request.fields] == [
        ("context", 1),
        ("dataset_id", 2),
    ]
    assert [(field.name, field.number) for field in result.fields] == [
        ("dataset_id", 1),
        ("job_id", 2),
    ]
    assert job.fields_by_name["dataset_id"].number == 11
    assert rag_service_pb2.JobType.Value("JOB_TYPE_DELETE_DATASET") == 4


def test_evidence_contains_provenance_and_stage_scores_but_no_answer() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    evidence = _message("Evidence")

    assert {
        "chunk_id",
        "document_id",
        "content_with_weight",
        "source_name",
        "locator",
        "metadata",
        "scores",
        "index_version",
    } <= set(evidence.fields_by_name)
    assert {"answer", "citation", "prompt"}.isdisjoint(evidence.fields_by_name)

    scores = _message("ScoreBreakdown")
    assert set(scores.fields_by_name) == {
        "dense_score",
        "sparse_score",
        "fusion_score",
        "rerank_score",
    }


def test_business_error_has_stable_machine_readable_fields() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    error = _message("BusinessError")

    assert list(error.fields_by_name) == ["code", "message", "retryable", "request_id"]
