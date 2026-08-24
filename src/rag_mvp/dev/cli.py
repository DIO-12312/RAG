"""Generated gRPC client for local diagnostics."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import grpc
from google.protobuf.json_format import MessageToJson

from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG MVP generated gRPC development client")
    parser.add_argument("--address", default="127.0.0.1:50051")
    parser.add_argument("--timeout", type=float, default=3.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_job = subparsers.add_parser("get-job", help="call RagService.GetJob")
    get_job.add_argument("--request-id", required=True)
    get_job.add_argument("--job-id", required=True)
    return parser


async def _get_job(arguments: argparse.Namespace) -> int:
    async with grpc.aio.insecure_channel(arguments.address) as channel:
        stub = rag_service_pb2_grpc.RagServiceStub(channel)  # type: ignore[no-untyped-call]
        response = await stub.GetJob(
            rag_service_pb2.GetJobRequest(
                request_id=arguments.request_id,
                job_id=arguments.job_id,
            ),
            timeout=arguments.timeout,
        )
    print(MessageToJson(response, preserving_proto_field_name=True))
    return 0


async def _run(arguments: argparse.Namespace) -> int:
    if arguments.command == "get-job":
        return await _get_job(arguments)
    raise ValueError(f"unsupported command: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the generated gRPC development client."""

    arguments = _parser().parse_args(argv)
    raise SystemExit(asyncio.run(_run(arguments)))


if __name__ == "__main__":
    main()
