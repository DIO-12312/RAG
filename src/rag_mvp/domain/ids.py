"""Stable identifiers and content digest rules."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Mapping

import xxhash


def new_id() -> str:
    """Generate an RFC 9562 UUIDv7-compatible identifier on Python 3.12+."""

    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return str(uuid.UUID(int=value))


def canonical_json(value: Mapping[str, object]) -> str:
    """Serialize configuration deterministically for digesting."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def config_digest(config: Mapping[str, object]) -> str:
    return _sha256(canonical_json(config).encode("utf-8"))


def file_sha256(content: bytes) -> str:
    return _sha256(content)


def content_sha256(content_with_weight: str) -> str:
    return _sha256(content_with_weight.encode("utf-8", "surrogatepass"))


def chunk_id(content_with_weight: str, document_id: str) -> str:
    """Apply RAGFlow's xxHash64(content_with_weight + document_id) rule."""

    value = (content_with_weight + document_id).encode("utf-8", "surrogatepass")
    return xxhash.xxh64(value).hexdigest()


def es_record_id(document_id: str, index_version: int, logical_chunk_id: str) -> str:
    if index_version < 1:
        raise ValueError("index_version must be at least 1")
    return f"{document_id}:{index_version}:{logical_chunk_id}"
