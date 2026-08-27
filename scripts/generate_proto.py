"""Generate the Python gRPC contract from the repository's sole proto source."""

# 从仓库唯一的 `.proto` 来源生成并覆写已提交的 Python gRPC 产物。

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROTO_FILE = REPOSITORY_ROOT / "proto" / "rag" / "v1" / "rag_service.proto"
GENERATED_DIR = REPOSITORY_ROOT / "src" / "rag_mvp" / "rpc" / "generated"
GENERATED_FILES = (
    "__init__.py",
    "rag_service_pb2.py",
    "rag_service_pb2.pyi",
    "rag_service_pb2_grpc.py",
)


def generate(output_dir: Path) -> None:
    """Generate all checked-in Python protobuf artifacts into *output_dir*."""

    # 执行 protoc，并将结果写入调用方指定的生成目录。

    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_FILE.parent}",
        f"--python_out={output_dir}",
        f"--pyi_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        str(PROTO_FILE),
    ]
    # check=True 让生成失败直接终止，避免后续覆盖为部分或过期的契约文件。
    subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)

    package_marker = '"""Generated protobuf package; do not edit generated modules."""\n'
    (output_dir / "__init__.py").write_text(package_marker, encoding="utf-8", newline="\n")

    grpc_module = output_dir / "rag_service_pb2_grpc.py"
    # 生成包标记和 gRPC 文件后，再修补 protoc 不感知仓库包布局的问题。
    grpc_source = grpc_module.read_text(encoding="utf-8")
    # protoc 默认产生顶层导入；仓库中的生成物位于包内，必须改成相对导入。
    grpc_source = grpc_source.replace(
        "import rag_service_pb2 as rag__service__pb2",
        "from . import rag_service_pb2 as rag__service__pb2",
    )
    grpc_module.write_text(grpc_source, encoding="utf-8", newline="\n")


def main() -> None:
    """Regenerate checked-in artifacts."""

    # 固定输出目录，重建仓库中受版本控制的契约产物。

    generate(GENERATED_DIR)


if __name__ == "__main__":
    main()
