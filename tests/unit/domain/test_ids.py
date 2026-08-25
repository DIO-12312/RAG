from __future__ import annotations

from uuid import UUID

from rag_mvp.domain.ids import (
    canonical_json,
    chunk_id,
    config_digest,
    content_sha256,
    es_record_id,
    file_sha256,
    new_id,
)


def test_new_id_is_uuid7_compatible() -> None:
    identifier = UUID(new_id())

    assert identifier.version == 7
    assert identifier.variant == "specified in RFC 4122"


def test_canonical_json_and_digests_are_stable() -> None:
    first = {"b": "中", "a": 1}
    second = {"a": 1, "b": "中"}

    assert canonical_json(first) == '{"a":1,"b":"中"}'
    assert config_digest(first) == config_digest(second)
    assert (
        config_digest(first) == "2831299868169bc527f55f88ebbdcd8b785d78d9e7dc64e6887dfbd2825dd247"
    )
    assert file_sha256(b"raw") == "d7439bee24773bcbfa2d0a97947ee36227b10d1022b1a55847e928965bb6bfde"
    assert content_sha256("正文") == (
        "d661c3d96d53ebc0ca8a55aae24b5df4a4d1bf28d37337b982fe8ebf54846eeb"
    )


def test_chunk_id_matches_ragflow_xxhash64_rule() -> None:
    assert chunk_id(content_with_weight="hel", document_id="lo") == "26c7827d889f6da3"
    assert chunk_id(content_with_weight="hello", document_id="document-a") == chunk_id(
        content_with_weight="hello", document_id="document-a"
    )
    assert chunk_id(content_with_weight="hello!", document_id="document-a") != chunk_id(
        content_with_weight="hello", document_id="document-a"
    )


def test_physical_es_id_preserves_document_version_and_chunk() -> None:
    assert es_record_id("doc-1", 3, "chunk-1") == "doc-1:3:chunk-1"
