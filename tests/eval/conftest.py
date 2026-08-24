"""Reuse the real gRPC E2E fixtures for infrastructure-backed evaluation."""

from tests.e2e.conftest import embedding_runtime, rag_stub

__all__ = ("embedding_runtime", "rag_stub")
