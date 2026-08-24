from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Final

SOURCE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "rag_mvp"
REQUIRED_PACKAGES: Final = (
    "domain",
    "application",
    "ports",
    "adapters",
    "rpc",
    "ingestion",
    "retrieval",
    "outbox",
    "bootstrap",
    "dev",
)
FORBIDDEN_IMPORT_PREFIXES: Final = {
    "domain": (
        "grpc",
        "pydantic",
        "pydantic_settings",
        "rag_mvp.application",
        "rag_mvp.ports",
        "rag_mvp.adapters",
        "rag_mvp.rpc",
        "rag_mvp.ingestion",
        "rag_mvp.outbox",
        "rag_mvp.bootstrap",
    ),
    "application": (
        "grpc",
        "rag_mvp.adapters",
        "rag_mvp.rpc",
        "rag_mvp.bootstrap",
    ),
    "rpc": ("rag_mvp.adapters",),
    "dev": (
        "rag_mvp.application",
        "rag_mvp.adapters",
        "rag_mvp.bootstrap",
    ),
    "retrieval": (
        "grpc",
        "pydantic",
        "pydantic_settings",
        "rag_mvp.adapters",
        "rag_mvp.rpc",
        "rag_mvp.bootstrap",
    ),
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    return imports


def test_final_layer_packages_exist() -> None:
    missing = [name for name in REQUIRED_PACKAGES if not (SOURCE_ROOT / name).is_dir()]

    assert missing == []


def test_source_imports_respect_layer_boundaries() -> None:
    violations: list[str] = []
    for layer, forbidden_prefixes in FORBIDDEN_IMPORT_PREFIXES.items():
        for path in sorted((SOURCE_ROOT / layer).rglob("*.py")):
            for imported_module in _imports(path):
                if imported_module.startswith(forbidden_prefixes):
                    relative_path = path.relative_to(SOURCE_ROOT)
                    violations.append(f"{relative_path}: forbidden import {imported_module}")

    assert violations == []


def test_production_source_never_imports_test_fakes() -> None:
    violations = [
        f"{path.relative_to(SOURCE_ROOT)}: forbidden test import {imported_module}"
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for imported_module in _imports(path)
        if imported_module == "tests" or imported_module.startswith("tests.")
    ]

    assert violations == []


def test_all_declared_ports_are_protocols() -> None:
    expected = {
        "metadata": "MetadataRepository",
        "storage": "ObjectStorage",
        "message_queue": "TaskQueue",
        "search_engine": "SearchEngine",
        "model": "ModelGateway",
        "parser": "Parser",
        "chunker": "Chunker",
    }

    for module_name, class_name in expected.items():
        module = importlib.import_module(f"rag_mvp.ports.{module_name}")
        port = getattr(module, class_name)
        assert port.__dict__.get("_is_protocol") is True
