"""Secret-safe health checks for the Compose topology and gRPC Server.

通过 Docker Compose 的状态摘要和一个无副作用的 gRPC 探针确认本地拓扑可用，不读取服务环境变量。
"""

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
    """定义健康检查 CLI；--grpc-only 供外部已启动服务的轻量探测使用。"""

    parser = argparse.ArgumentParser(description="check RAG MVP Docker services")
    parser.add_argument("--grpc-only", metavar="HOST:PORT")
    parser.add_argument("--grpc-address", default="127.0.0.1:50051")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    return parser


def parse_compose_processes(output: str) -> dict[str, dict[str, Any]]:
    """Parse Compose's NDJSON output without rendering service environments.

    只处理 `docker compose ps` 的结构化摘要，避免把可能含密钥的环境变量写入日志。
    """

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
    """确认长期服务均 healthy，且一次性迁移容器已成功退出。"""

    # 任一长驻服务未启动或未通过 Docker 健康检查时，都不能继续调用 gRPC。
    for service in RUNNING_SERVICES:
        record = records.get(service)
        if record is None or record.get("State") != "running" or record.get("Health") != "healthy":
            return False
    # 数据库迁移是一次性容器：成功退出才代表服务依赖的 schema 已就绪。
    migration = records.get("rag-migrate")
    return bool(
        migration is not None
        and migration.get("State") == "exited"
        and migration.get("ExitCode") == 0
    )


async def check_grpc(address: str, deadline_seconds: float) -> bool:
    """调用预期返回 JOB_NOT_FOUND 的只读 RPC，验证 gRPC 服务实际可处理请求。"""

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
        # 任意传输、超时或服务端 RPC 错误均视为探针失败，不泄漏服务端错误详情。
        return False
    return bool(
        response.WhichOneof("outcome") == "error" and response.error.code == "JOB_NOT_FOUND"
    )


def _compose_process_output() -> subprocess.CompletedProcess[str]:
    """执行不输出环境变量的 Compose 状态查询，并返回其原始结果。"""

    # `ps --format json` 只读取容器状态，避免 `inspect` 等命令暴露服务密钥。
    return subprocess.run(
        ["docker", "compose", "ps", "--all", "--format", "json"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


async def _run(arguments: argparse.Namespace) -> int:
    """在超时前轮询 Compose 状态，并在服务就绪后执行 gRPC 探针。"""

    if arguments.grpc_only:
        return int(not await check_grpc(arguments.grpc_only, arguments.timeout))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(arguments.wait_seconds, 0.0)
    failure = "docker compose health check failed"
    while True:
        # Docker CLI 是同步进程调用，转入线程以免阻塞 gRPC 健康检查的事件循环。
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
                    # 仅在 Compose 拓扑和 RPC 探针均成功后，才对调用方报告就绪。
                    print("docker compose and gRPC services are healthy")
                    return 0
        if loop.time() >= deadline:
            # 统一从最近失败阶段返回简短错误，避免循环无限等待。
            print(failure)
            return 1
        await asyncio.sleep(max(arguments.poll_interval, 0.05))


def main(argv: Sequence[str] | None = None) -> None:
    """解析 CLI 参数并在单一 asyncio 事件循环中运行健康检查。"""

    raise SystemExit(asyncio.run(_run(_parser().parse_args(argv))))


if __name__ == "__main__":
    main()
