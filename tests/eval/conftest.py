"""复用真实 gRPC E2E fixture，为基础设施支撑的质量评测提供环境。"""

from tests.e2e.conftest import embedding_runtime, rag_stub

__all__ = ("embedding_runtime", "rag_stub")
