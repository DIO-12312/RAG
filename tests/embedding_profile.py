"""Build encrypted RPC profiles for explicit real-model test suites only."""

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypted_test_profile(model: str, dimension: int) -> str:
    key_path = os.getenv("RAG_MODEL_ENCRYPTION_KEY_FILE", "")
    if not key_path:
        return ""  # Standalone legacy Python test deployment.
    key = base64.b64decode(Path(key_path).read_text().strip(), validate=True)
    config = {
        "modelName": model,
        "embeddingDimension": dimension,
        "baseUrl": os.environ["EMBEDDING_MODEL_URL"],
        "apiKey": os.environ["EMBEDDING_MODEL_API_KEY"],
        "timeoutSeconds": 60,
    }
    nonce = os.urandom(12)
    return base64.b64encode(
        nonce + AESGCM(key).encrypt(nonce, json.dumps(config).encode(), b"rag/embedding-profile/v1")
    ).decode()
