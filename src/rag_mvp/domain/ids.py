"""稳定标识与内容摘要规则，包含与 RAGFlow 对齐的 xxHash64 Chunk ID。"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Mapping

import xxhash


# 生成按毫秒时间排序的 UUIDv7 兼容实体 ID。
def new_id() -> str:
    """Generate an RFC 9562 UUIDv7-compatible identifier on Python 3.12+."""

    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return str(uuid.UUID(int=value))


# 将配置序列化为键顺序固定、无多余空白的 JSON，供摘要计算使用。
def canonical_json(value: Mapping[str, object]) -> str:
    """Serialize configuration deterministically for digesting."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# 计算字节内容的 SHA-256 十六进制摘要。
def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# 计算摄取配置的稳定 SHA-256 摘要。
def config_digest(config: Mapping[str, object]) -> str:
    return _sha256(canonical_json(config).encode("utf-8"))


# 计算上传文件原始字节的 SHA-256 摘要。
def file_sha256(content: bytes) -> str:
    return _sha256(content)


# 计算带权重 Chunk 文本的 SHA-256 摘要，并保留代理码点。
def content_sha256(content_with_weight: str) -> str:
    return _sha256(content_with_weight.encode("utf-8", "surrogatepass"))


# 按 RAGFlow 规则从 Chunk 文本和文档 ID 生成逻辑 Chunk ID。
def chunk_id(content_with_weight: str, document_id: str) -> str:
    """Apply RAGFlow's xxHash64(content_with_weight + document_id) rule."""

    value = (content_with_weight + document_id).encode("utf-8", "surrogatepass")
    return xxhash.xxh64(value).hexdigest()


# 组合文档、索引版本和逻辑 Chunk ID，生成 Elasticsearch 物理记录 ID。
def es_record_id(document_id: str, index_version: int, logical_chunk_id: str) -> str:
    if index_version < 1:
        raise ValueError("index_version must be at least 1")
    return f"{document_id}:{index_version}:{logical_chunk_id}"
