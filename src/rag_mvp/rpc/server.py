"""Async gRPC server lifecycle."""

from __future__ import annotations

import asyncio

import grpc
from grpc_reflection.v1alpha import reflection

from rag_mvp.bootstrap.container import (
    Container,
    build_container,
    install_shutdown_handlers,
)
from rag_mvp.config import Settings, load_settings
from rag_mvp.rpc.generated import rag_service_pb2, rag_service_pb2_grpc
from rag_mvp.rpc.rag_service import RagService


async def serve(
    settings: Settings,
    container: Container,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Start the private gRPC server and stop it gracefully."""

    del container
    server = grpc.aio.server()
    rag_service_pb2_grpc.add_RagServiceServicer_to_server(  # type: ignore[no-untyped-call]
        RagService(), server
    )

    if settings.grpc_reflection:
        service_name = rag_service_pb2.DESCRIPTOR.services_by_name["RagService"].full_name
        reflection.enable_server_reflection((service_name, reflection.SERVICE_NAME), server)

    bound_port = server.add_insecure_port(settings.grpc_address)
    if bound_port == 0:
        raise RuntimeError(f"failed to bind gRPC server to {settings.grpc_address}")

    await server.start()
    try:
        if stop_event is None:
            await server.wait_for_termination()
        else:
            await stop_event.wait()
    finally:
        await server.stop(settings.grpc_shutdown_timeout_seconds)


async def _run() -> None:
    settings = load_settings()
    container = build_container(settings)
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)
    try:
        await serve(settings, container, stop_event)
    finally:
        await container.close()


def main() -> None:
    """Run the gRPC server process."""

    asyncio.run(_run())


if __name__ == "__main__":
    main()
