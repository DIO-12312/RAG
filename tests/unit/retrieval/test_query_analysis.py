from __future__ import annotations

import pytest

from rag_mvp.retrieval.query_analysis import (
    QueryEntityKind,
    QueryIntent,
    analyze_query,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        ("如何安装 ZRDDS，需要哪些依赖", QueryIntent.INSTALLATION),
        ("ZRDDS 配置文件和环境变量在哪里设置", QueryIntent.CONFIGURATION),
        ("DDS_DataReader_take 的参数和返回值是什么", QueryIntent.API_USAGE),
        ("如何设置 DataWriter 的 Reliability QoS", QueryIntent.QOS),
        ("DDS_RETCODE_TIMEOUT 错误怎么排查", QueryIntent.TROUBLESHOOTING),
        ("DataReader 延迟高吞吐低怎么调优", QueryIntent.PERFORMANCE_TUNING),
    ),
)
def test_query_intent_classification(query: str, expected: QueryIntent) -> None:
    assert analyze_query(query).intent is expected


def test_query_normalization_expands_abbreviations_terms_and_natural_language_apis() -> None:
    result = analyze_query("DP 如何创建 DW，DR 怎么读取数据？")

    assert "DomainParticipant" in result.normalized_query
    assert "DDS_DataWriter" in result.normalized_query
    assert "DDS_DataReader" in result.normalized_query
    assert "DDS_Publisher_create_datawriter" in result.normalized_query
    assert "DDS_DataReader_read" in result.normalized_query
    assert {entity.kind for entity in result.entities} >= {
        QueryEntityKind.ABBREVIATION,
        QueryEntityKind.API,
    }


def test_query_analysis_extracts_api_struct_enum_and_error_code() -> None:
    query = (
        "DDS_DomainParticipantFactory_create_participant 使用 DDS_DataWriterQos 时，"
        "DDS_RELIABILITY_QOS_POLICY_ID 或 DDS_RETCODE_TIMEOUT 分别表示什么？"
    )

    entities = analyze_query(query).entities
    by_value = {entity.canonical: entity.kind for entity in entities}

    assert by_value["DDS_DomainParticipantFactory_create_participant"] is QueryEntityKind.API
    assert by_value["DDS_DataWriterQos"] is QueryEntityKind.STRUCT
    assert by_value["DDS_RELIABILITY_QOS_POLICY_ID"] is QueryEntityKind.ENUM
    assert by_value["DDS_RETCODE_TIMEOUT"] is QueryEntityKind.ERROR_CODE


def test_vague_query_produces_two_or_three_distinct_retrieval_subqueries() -> None:
    result = analyze_query("DW怎么用")

    assert result.ambiguous is True
    assert 2 <= len(result.subqueries) <= 3
    assert len(set(result.subqueries)) == len(result.subqueries)
    assert any("DDS_DataWriter" in subquery for subquery in result.subqueries)
    assert any("DDS_Publisher_create_datawriter" in subquery for subquery in result.subqueries)


def test_explicit_api_query_keeps_one_normalized_subquery() -> None:
    result = analyze_query("DDS_DataReader_take 的参数是什么")

    assert result.ambiguous is False
    assert result.subqueries == (result.normalized_query,)


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError, match="query"):
        analyze_query("  ")
