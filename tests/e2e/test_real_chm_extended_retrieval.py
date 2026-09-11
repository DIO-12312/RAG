"""复用本地 CHM 知识库执行扩展检索质量评测并输出诊断报告。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.conftest import retrieve

DATASET_ID_ENV = "RAG_E2E_CHM_REUSE_DATASET_ID"
DOCUMENT_ID_ENV = "RAG_E2E_CHM_REUSE_DOCUMENT_ID"
REPORT_PATH_ENV = "RAG_E2E_CHM_REPORT_PATH"
DEFAULT_REPORT = Path("/app/tests/eval/log/chm_extended_retrieval_report.json")


@dataclass(frozen=True, slots=True)
class ExtendedQueryCase:
    category: str
    query: str
    topic_path: str
    required_phrase: str
    alternate_topic_paths: tuple[str, ...] = ()

    @property
    def relevant_topic_paths(self) -> tuple[str, ...]:
        return (self.topic_path, *self.alternate_topic_paths)


QUERY_CASES = (
    ExtendedQueryCase(
        "overview",
        "ZRDDS 的标准协议接口、扩展接口和简化接口分别如何使用？",
        "zrdds_interface.html",
        "标准协议接口",
    ),
    ExtendedQueryCase(
        "configuration",
        "如何使用 XML 文件配置实体 QoS 并管理 ZRDDS 内部 QoS 仓库？",
        "zrdds_qos_xml.html",
        "XML配置QoS",
    ),
    ExtendedQueryCase(
        "logging",
        "DDS 中间件日志的标准风格和极简风格如何配置？",
        "zrdds_log.html",
        "日志输出风格",
    ),
    ExtendedQueryCase(
        "faq",
        "DDS 使用 UDP 传输时最大传输数据长度是多少？",
        "faq.html",
        "UDP",
    ),
    ExtendedQueryCase(
        "transport",
        "ZRDDS TCP 多核传输的发送端应该怎样配置？",
        "md_resources_docs_online_c_required_tcp_concurrent.html",
        "TCP多核传输",
    ),
    ExtendedQueryCase(
        "transport",
        "ZRDDS 大包零拷贝功能的接收端如何配置？",
        "md_resources_docs_online_c_required_udp_large_package_zerocopy.html",
        "大包零拷贝",
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_ReliabilityQosPolicy 如何配置可靠传输？",
        "struct_d_d_s___reliability_qos_policy.html",
        "DDS_ReliabilityQosPolicy",
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_DurabilityQosPolicy 如何让晚加入的订阅者获得历史数据？",
        "struct_d_d_s___durability_qos_policy.html",
        "DDS_DurabilityQosPolicy",
        (
            "struct_d_d_s___data_writer_qos.html",
            "struct_d_d_s___durability_service_qos_policy.html",
            "group___core_qos_struct.html",
        ),
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_HistoryQosPolicy 如何控制保存的历史样本？",
        "struct_d_d_s___history_qos_policy.html",
        "DDS_HistoryQosPolicy",
        (
            "_history_qos_policy_8h_source.html",
            "_durability_service_qos_policy_8h_source.html",
            "group___core_qos_struct.html",
        ),
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_LivelinessQosPolicy 如何配置参与者活跃性检测？",
        "struct_d_d_s___liveliness_qos_policy.html",
        "DDS_LivelinessQosPolicy",
        (
            "_data_reader_qos_8h_source.html",
            "struct_d_d_s___liveliness_lost_status.html",
            "struct_d_d_s___data_reader_qos.html",
        ),
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_OwnershipQosPolicy 如何设置共享或独占所有权？",
        "struct_d_d_s___ownership_qos_policy.html",
        "DDS_OwnershipQosPolicy",
        (
            "_ownership_qos_policy_8h_source.html",
            "group___core_qos_struct.html",
            "struct_d_d_s___ownership_strength_qos_policy.html",
        ),
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_ResourceLimitsQosPolicy 如何限制样本和实例资源？",
        "struct_d_d_s___resource_limits_qos_policy.html",
        "DDS_ResourceLimitsQosPolicy",
    ),
    ExtendedQueryCase(
        "qos",
        "DDS_TransportPriorityQosPolicy 如何设置传输优先级？",
        "struct_d_d_s___transport_priority_qos_policy.html",
        "DDS_TransportPriorityQosPolicy",
    ),
    ExtendedQueryCase(
        "topic",
        "DDS_ContentFilteredTopic 结构体如何表示基于内容过滤的主题？",
        "struct_d_d_s___content_filtered_topic.html",
        "DDS_ContentFilteredTopic",
    ),
    ExtendedQueryCase(
        "condition",
        "DDS_WaitSet 结构体如何等待多个 DDS 条件？",
        "struct_d_d_s___wait_set.html",
        "DDS_WaitSet",
    ),
    ExtendedQueryCase(
        "condition",
        "DDS_StatusCondition 结构体用于监视哪些实体状态？",
        "struct_d_d_s___status_condition.html",
        "DDS_StatusCondition",
    ),
    ExtendedQueryCase(
        "listener",
        "DDS_DataWriterListener 结构体包含哪些发布端回调？",
        "struct_d_d_s___data_writer_listener.html",
        "DDS_DataWriterListener",
        (
            "_data_writer_listener_8h_source.html",
            "_publisher_listener_8h_source.html",
        ),
    ),
    ExtendedQueryCase(
        "listener",
        "DDS_DataReaderListener 结构体包含哪些订阅端回调？",
        "struct_d_d_s___data_reader_listener.html",
        "DDS_DataReaderListener",
        (
            "_data_reader_listener_8h_source.html",
            "_subscriber_listener_8h_source.html",
            "struct_d_d_s___subscriber_listener.html",
            "_get_plain_status_8c-example.html",
        ),
    ),
    ExtendedQueryCase(
        "domain",
        "DDS_DomainParticipantFactory 结构体负责什么？",
        "struct_d_d_s___domain_participant_factory.html",
        "DDS_DomainParticipantFactory",
    ),
    ExtendedQueryCase(
        "status",
        "DDS_LivelinessChangedStatus 结构体记录哪些活跃性变化？",
        "struct_d_d_s___liveliness_changed_status.html",
        "DDS_LivelinessChangedStatus",
    ),
)


def _score(item: Any, name: str) -> float | None:
    return float(getattr(item.scores, name)) if item.scores.HasField(name) else None


def _evidence_record(item: Any, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "chunk_id": item.chunk_id,
        "topic_path": item.metadata.get("topic_path", ""),
        "topic_title": item.metadata.get("topic_title", ""),
        "heading_path": item.metadata.get("heading_path", ""),
        "retrieval_role": item.metadata.get("retrieval_role", "anchor"),
        "anchor_chunk_id": item.metadata.get("anchor_chunk_id"),
        "neighbor_distance": item.metadata.get("neighbor_distance"),
        "start_line": item.locator.start_line,
        "end_line": item.locator.end_line,
        "scores": {
            "dense": _score(item, "dense_score"),
            "sparse": _score(item, "sparse_score"),
            "fusion": _score(item, "fusion_score"),
            "rerank": _score(item, "rerank_score"),
        },
        "content_preview": item.content_with_weight[:500],
    }


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_existing_chm_knowledge_base_extended_retrieval_quality(
    rag_stub: object,
) -> None:
    """用 20 个跨章节问题评估已有 CHM 知识库并落盘 Top-6 诊断报告。"""
    dataset_id = os.getenv(DATASET_ID_ENV, "").strip()
    document_id = os.getenv(DOCUMENT_ID_ENV, "").strip()
    if not dataset_id or not document_id:
        pytest.skip(f"set {DATASET_ID_ENV} and {DOCUMENT_ID_ENV} to reuse a CHM knowledge base")

    report_path = Path(os.getenv(REPORT_PATH_ENV, str(DEFAULT_REPORT)))
    reciprocal_ranks: list[float] = []
    phrase_hits = 0
    case_reports: list[dict[str, Any]] = []

    for case in QUERY_CASES:
        result = await retrieve(rag_stub, dataset_id, case.query)
        evidence = [item for item in result.evidence if item.document_id == document_id]
        anchors = [
            item for item in evidence if item.metadata.get("retrieval_role") != "topic_neighbor"
        ]
        records = [_evidence_record(item, rank) for rank, item in enumerate(evidence, start=1)]
        anchor_records = [
            _evidence_record(item, rank) for rank, item in enumerate(anchors, start=1)
        ]
        matching_ranks = [
            record["rank"]
            for record in anchor_records
            if record["topic_path"] in case.relevant_topic_paths
        ]
        rank = matching_ranks[0] if matching_ranks else None
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        target_text = "\n".join(
            item.content_with_weight
            for item in evidence
            if item.metadata.get("topic_path") in case.relevant_topic_paths
        )
        phrase_hit = case.required_phrase in target_text
        phrase_hits += int(phrase_hit)
        case_reports.append(
            {
                "category": case.category,
                "query": case.query,
                "expected_topic_path": case.topic_path,
                "relevant_topic_paths": case.relevant_topic_paths,
                "required_phrase": case.required_phrase,
                "target_rank": rank,
                "phrase_hit": phrase_hit,
                "evidence": records,
            }
        )

    recall_at_6 = sum(rank > 0 for rank in reciprocal_ranks) / len(QUERY_CASES)
    mrr_at_6 = sum(reciprocal_ranks) / len(QUERY_CASES)
    phrase_hit_rate = phrase_hits / len(QUERY_CASES)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset_id,
        "document_id": document_id,
        "query_count": len(QUERY_CASES),
        "metrics": {
            "recall_at_6": recall_at_6,
            "mrr_at_6": mrr_at_6,
            "phrase_hit_rate": phrase_hit_rate,
        },
        "cases": case_reports,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        "\nExtended CHM retrieval metrics:"
        f"\n  queries={len(QUERY_CASES)}"
        f"\n  Recall@6={recall_at_6:.4f}"
        f"\n  MRR@6={mrr_at_6:.4f}"
        f"\n  phrase_hit_rate={phrase_hit_rate:.4f}"
        f"\n  report={report_path}"
    )

    assert recall_at_6 == pytest.approx(1.0)
    assert mrr_at_6 >= 0.65
