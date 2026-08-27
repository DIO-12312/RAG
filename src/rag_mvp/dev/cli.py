"""本地诊断 gRPC 客户端：模拟真实调用方，不绕过 RPC 边界。"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import grpc
from google.protobuf.json_format import MessageToJson
from google.protobuf.message import Message

from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc

UPLOAD_FRAME_BYTES = 64 * 1024


# 内部辅助：完成 add_context_arguments 所需的局部转换或校验。
def _add_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--idempotency-key", required=True)


# 内部辅助：完成 metadata_filter 所需的局部转换或校验。
def _metadata_filter(value: str) -> tuple[str, str]:
    key, separator, item = value.partition("=")
    if not separator or not key.strip() or not item.strip():
        raise argparse.ArgumentTypeError("filter must use a non-empty key=value form")
    return key.strip(), item.strip()


# 内部辅助：完成 parser 所需的局部转换或校验。
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG MVP generated gRPC development client")
    parser.add_argument("--address", default="127.0.0.1:50051")
    parser.add_argument("--timeout", type=float, default=30.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_dataset = subparsers.add_parser("create-dataset")
    _add_context_arguments(create_dataset)
    create_dataset.add_argument("--name", required=True)
    create_dataset.add_argument("--embedding-model", required=True)
    create_dataset.add_argument("--embedding-dimension", type=int, required=True)
    create_dataset.add_argument("--dense-top-k", type=int, default=20)
    create_dataset.add_argument("--sparse-top-k", type=int, default=20)
    create_dataset.add_argument("--rrf-k", type=int, default=60)
    create_dataset.add_argument("--rerank-enabled", action="store_true")
    create_dataset.add_argument("--rerank-top-n", type=int, default=10)
    create_dataset.add_argument("--max-context-tokens", type=int, default=4000)

    submit = subparsers.add_parser("submit-document")
    _add_context_arguments(submit)
    submit.add_argument("--dataset-id", required=True)
    submit.add_argument("--file", type=Path, required=True)
    submit.add_argument("--source-name")
    submit.add_argument("--expected-sha256")
    submit.add_argument("--target-document-id")

    get_job = subparsers.add_parser("get-job")
    get_job.add_argument("--request-id", required=True)
    get_job.add_argument("--job-id", required=True)

    retry_job = subparsers.add_parser("retry-job")
    _add_context_arguments(retry_job)
    retry_job.add_argument("--job-id", required=True)

    cancel_job = subparsers.add_parser("cancel-job")
    _add_context_arguments(cancel_job)
    cancel_job.add_argument("--job-id", required=True)

    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("--request-id", required=True)
    retrieve.add_argument("--dataset-id", required=True)
    retrieve.add_argument("--query", required=True)
    retrieve.add_argument("--filter", action="append", default=[], type=_metadata_filter)
    retrieve.add_argument("--top-k", type=int, default=10)
    retrieve.add_argument("--enable-rerank", action="store_true")
    retrieve.add_argument("--max-context-tokens", type=int, default=4000)

    delete = subparsers.add_parser("delete-document")
    _add_context_arguments(delete)
    delete.add_argument("--document-id", required=True)

    delete_dataset = subparsers.add_parser("delete-dataset")
    _add_context_arguments(delete_dataset)
    delete_dataset.add_argument("--dataset-id", required=True)
    return parser


# 内部辅助：完成 context 所需的局部转换或校验。
def _context(arguments: argparse.Namespace) -> rag_service_pb2.RequestContext:
    return rag_service_pb2.RequestContext(
        request_id=arguments.request_id,
        idempotency_key=arguments.idempotency_key,
    )


# 内部辅助：完成 upload_requests 所需的局部转换或校验。
async def _upload_requests(
    arguments: argparse.Namespace,
) -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
    source = arguments.file
    header = rag_service_pb2.UploadHeader(
        context=_context(arguments),
        dataset_id=arguments.dataset_id,
        source_name=arguments.source_name or source.name,
    )
    if arguments.expected_sha256:
        header.expected_sha256 = arguments.expected_sha256
    if arguments.target_document_id:
        header.target_document_id = arguments.target_document_id
    yield rag_service_pb2.UploadDocumentRequest(header=header)

    with source.open("rb") as stream:
        while data := stream.read(UPLOAD_FRAME_BYTES):
            yield rag_service_pb2.UploadDocumentRequest(data=data)


# 内部辅助：完成 render 所需的局部转换或校验。
def _render(response: Message) -> int:
    print(MessageToJson(response, preserving_proto_field_name=True))
    return int(response.WhichOneof("outcome") == "error")


# 内部辅助：完成 run 所需的局部转换或校验。
async def _run(arguments: argparse.Namespace) -> int:
    async with grpc.aio.insecure_channel(arguments.address) as channel:
        stub = rag_service_pb2_grpc.RagServiceStub(channel)  # type: ignore[no-untyped-call]
        if arguments.command == "create-dataset":
            response = await stub.CreateDataset(
                rag_service_pb2.CreateDatasetRequest(
                    context=_context(arguments),
                    name=arguments.name,
                    embedding_model=arguments.embedding_model,
                    embedding_dimension=arguments.embedding_dimension,
                    retrieval_config=rag_service_pb2.RetrievalConfig(
                        dense_top_k=arguments.dense_top_k,
                        sparse_top_k=arguments.sparse_top_k,
                        rrf_k=arguments.rrf_k,
                        rerank_enabled=arguments.rerank_enabled,
                        rerank_top_n=arguments.rerank_top_n,
                        max_context_tokens=arguments.max_context_tokens,
                    ),
                ),
                timeout=arguments.timeout,
            )
        elif arguments.command == "submit-document":
            response = await stub.SubmitDocument(
                _upload_requests(arguments),
                timeout=arguments.timeout,
            )
        elif arguments.command == "get-job":
            response = await stub.GetJob(
                rag_service_pb2.GetJobRequest(
                    request_id=arguments.request_id,
                    job_id=arguments.job_id,
                ),
                timeout=arguments.timeout,
            )
        elif arguments.command == "retry-job":
            response = await stub.RetryJob(
                rag_service_pb2.RetryJobRequest(
                    context=_context(arguments),
                    job_id=arguments.job_id,
                ),
                timeout=arguments.timeout,
            )
        elif arguments.command == "cancel-job":
            response = await stub.CancelJob(
                rag_service_pb2.CancelJobRequest(
                    context=_context(arguments),
                    job_id=arguments.job_id,
                ),
                timeout=arguments.timeout,
            )
        elif arguments.command == "retrieve":
            response = await stub.Retrieve(
                rag_service_pb2.RetrieveRequest(
                    request_id=arguments.request_id,
                    dataset_id=arguments.dataset_id,
                    query=arguments.query,
                    filters=[
                        rag_service_pb2.MetadataFilter(key=key, value=value)
                        for key, value in arguments.filter
                    ],
                    top_k=arguments.top_k,
                    enable_rerank=arguments.enable_rerank,
                    max_context_tokens=arguments.max_context_tokens,
                ),
                timeout=arguments.timeout,
            )
        elif arguments.command == "delete-document":
            response = await stub.DeleteDocument(
                rag_service_pb2.DeleteDocumentRequest(
                    context=_context(arguments),
                    document_id=arguments.document_id,
                ),
                timeout=arguments.timeout,
            )
        elif arguments.command == "delete-dataset":
            response = await stub.DeleteDataset(
                rag_service_pb2.DeleteDatasetRequest(
                    context=_context(arguments),
                    dataset_id=arguments.dataset_id,
                ),
                timeout=arguments.timeout,
            )
        else:
            raise ValueError(f"unsupported command: {arguments.command}")
    return _render(response)


# 控制台入口：解析运行环境后启动对应进程。
def main(argv: Sequence[str] | None = None) -> None:
    """Run the generated gRPC development client."""

    arguments = _parser().parse_args(argv)
    raise SystemExit(asyncio.run(_run(arguments)))


if __name__ == "__main__":
    main()
