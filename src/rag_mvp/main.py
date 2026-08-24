"""Public gRPC server entry point."""

from rag_mvp.rpc.server import main as run_server


def main() -> None:
    """Start only the private gRPC server process."""

    run_server()


if __name__ == "__main__":
    main()
