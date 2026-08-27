"""gRPC 服务进程入口；不在此处装配基础设施依赖。"""

from rag_mvp.rpc.server import main as run_server


# 控制台入口：解析运行环境后启动对应进程。
def main() -> None:
    """Start only the private gRPC server process."""

    run_server()


if __name__ == "__main__":
    main()
