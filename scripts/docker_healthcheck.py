"""Secret-safe health checks for the Compose topology and gRPC Server."""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from collections.abc import Sequence
from typing import Any

import grpc

from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc

RUNNING_SERVICES = {
    "mysql",
    "elasticsearch",
    "nats",
    "rag-server",
    "rag-worker",
    "rag-outbox",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="check RAG MVP Docker services")
    parser.add_argument("--grpc-only", metavar="HOST:PORT")
    parser.add_argument("--grpc-address", default="127.0.0.1:50051")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    return parser


def parse_compose_processes(output: str) -> dict[str, dict[str, Any]]:
    """Parse Compose's NDJSON output without rendering service environments."""

    records: dict[str, dict[str, Any]] = {}
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        service = record.get("Service")
        if not isinstance(service, str) or not service:
            raise ValueError("compose process record does not contain a service name")
        records[service] = record
    return records


def compose_is_healthy(records: dict[str, dict[str, Any]]) -> bool:
    for service in RUNNING_SERVICES:
        record = records.get(service)
        if record is None or record.get("State") != "running" or record.get("Health") != "healthy":
            return False
    migration = records.get("rag-migrate")
    return bool(
        migration is not None
        and migration.get("State") == "exited"
        and migration.get("ExitCode") == 0
    )


async def check_grpc(address: str, deadline_seconds: float) -> bool:
    try:
        async with grpc.aio.insecure_channel(address) as channel:
            stub = rag_service_pb2_grpc.RagServiceStub(channel)  # type: ignore[no-untyped-call]
            response = await stub.GetJob(
                rag_service_pb2.GetJobRequest(
                    request_id="docker-healthcheck",
                    job_id="docker-healthcheck-missing-job",
                ),
                timeout=deadline_seconds,
            )
    except grpc.RpcError:
        return False
    return bool(
        response.WhichOneof("outcome") == "error" and response.error.code == "JOB_NOT_FOUND"
    )


def _compose_process_output() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "ps", "--all", "--format", "json"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


async def _run(arguments: argparse.Namespace) -> int:
    if arguments.grpc_only:
        return int(not await check_grpc(arguments.grpc_only, arguments.timeout))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(arguments.wait_seconds, 0.0)
    failure = "docker compose health check failed"
    while True:
        completed = await asyncio.to_thread(_compose_process_output)
        if completed.returncode == 0:
            try:
                records = parse_compose_processes(completed.stdout)
            except (json.JSONDecodeError, ValueError):
                failure = "docker compose health check failed"
            else:
                if not compose_is_healthy(records):
                    failure = "docker compose services are not healthy"
                elif not await check_grpc(arguments.grpc_address, arguments.timeout):
                    failure = "gRPC health check failed"
                else:
                    print("docker compose and gRPC services are healthy")
                    return 0
        if loop.time() >= deadline:
            print(failure)
            return 1
        await asyncio.sleep(max(arguments.poll_interval, 0.05))


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_run(_parser().parse_args(argv))))


if __name__ == "__main__":
    main()
