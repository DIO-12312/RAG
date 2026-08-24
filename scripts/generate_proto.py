"""Generate the Python gRPC contract from the repository's sole proto source."""

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
    subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)

    package_marker = '"""Generated protobuf package; do not edit generated modules."""\n'
    (output_dir / "__init__.py").write_text(package_marker, encoding="utf-8", newline="\n")

    grpc_module = output_dir / "rag_service_pb2_grpc.py"
    grpc_source = grpc_module.read_text(encoding="utf-8")
    grpc_source = grpc_source.replace(
        "import rag_service_pb2 as rag__service__pb2",
        "from . import rag_service_pb2 as rag__service__pb2",
    )
    grpc_module.write_text(grpc_source, encoding="utf-8", newline="\n")


def main() -> None:
    """Regenerate checked-in artifacts."""

    generate(GENERATED_DIR)


if __name__ == "__main__":
    main()
