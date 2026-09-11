"""使用本机 CHM 手册验证真实摄取、Topic 边界、来源定位和检索质量。"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from rag_mvp.rpc.generated import rag_service_pb2
from tests.e2e.conftest import (
    EmbeddingRuntime,
    create_dataset,
    retrieve,
    submit_document,
    wait_for_job,
)

CHM_PATH_ENV = "RAG_E2E_CHM_PATH"
CHM_INGESTION_TIMEOUT_ENV = "RAG_E2E_CHM_INGESTION_TIMEOUT_SECONDS"
CHM_REUSE_DATASET_ID_ENV = "RAG_E2E_CHM_REUSE_DATASET_ID"
CHM_REUSE_DOCUMENT_ID_ENV = "RAG_E2E_CHM_REUSE_DOCUMENT_ID"
CHM_REPORT_PATH_ENV = "RAG_E2E_CHM_BASELINE_REPORT_PATH"
CHI_PATH_ENV = "RAG_E2E_CHI_PATH"
CHI_REPORT_PATH_ENV = "RAG_E2E_CHI_REPORT_PATH"
DEFAULT_CHM_INGESTION_TIMEOUT_SECONDS = 1200
DEFAULT_CHM = Path(__file__).resolve().parents[1] / "chm_test" / "ZRDDS_C_UserManual.chm"
DEFAULT_CHM_REPORT = Path("/app/tests/eval/log/chm_baseline_retrieval_report.json")
DEFAULT_CHI = Path(__file__).resolve().parents[1] / "chm_test" / "ZRDDS_C_UserManual.chi"
DEFAULT_CHI_REPORT = Path("/app/tests/eval/log/chm_chi_extended_retrieval_report.json")


@dataclass(frozen=True, slots=True)
class QueryCase:
    query: str
    topic_path: str
    required_phrase: str
    alternate_topic_paths: tuple[str, ...] = ()

    @property
    def relevant_topic_paths(self) -> tuple[str, ...]:
        """返回所有可独立正确回答该问题的 CHM Topic。"""
        return (self.topic_path, *self.alternate_topic_paths)


@dataclass(frozen=True, slots=True)
class ChiQueryCase:
    query: str
    topic_path: str


QUERY_CASES = (
    QueryCase(
        query="域参与者工厂如何创建域参与者？",
        topic_path="group___c_domain.html",
        required_phrase="DDS_DomainParticipantFactory_create_participant",
        alternate_topic_paths=(
            "index.html",
            "struct_d_d_s___domain_participant_factory.html",
        ),
    ),
    QueryCase(
        query="如何使用条件等待模型等待 DDS 条件？",
        topic_path="_wait-_condition_8c-example.html",
        required_phrase="DDS_WaitSet_wait",
        alternate_topic_paths=(
            "index.html",
            "struct_d_d_s___wait_set.html",
        ),
    ),
    QueryCase(
        query="如何配置 DeadlineQos 数据发布端？",
        topic_path="_dead_line_qos_2main_pub_8c-example.html",
        required_phrase="DeadlineQos",
    ),
    QueryCase(
        query="如何使用 BatchQoS 进行批量发送？",
        topic_path="_batch_qos_2main_pub_8c-example.html",
        required_phrase="BatchQoS",
    ),
    QueryCase(
        query="如何使用基于内容过滤功能的订阅端？",
        topic_path="_data_receive_by_filter_2main_sub_8c-example.html",
        required_phrase="基于内容过滤",
        alternate_topic_paths=(
            "struct_d_d_s___content_filtered_topic.html",
            "index.html",
        ),
    ),
    QueryCase(
        query="DDS_RETCODE_TIMEOUT 的枚举值是多少？",
        topic_path="_return_code__t_8h_source.html",
        required_phrase="DDS_RETCODE_TIMEOUT",
        alternate_topic_paths=("group___core_base_struct.html",),
    ),
)

CHI_QUERY_CASES = (
    ChiQueryCase(
        "DDS_DomainParticipantFactory_create_participant",
        "group___c_domain.html",
    ),
    ChiQueryCase(
        "DDS_DomainParticipantFactory_create_participant_with_qos_profile",
        "group___c_domain.html",
    ),
    ChiQueryCase("DDS_DomainParticipant_create_topic", "group___c_domain.html"),
    ChiQueryCase("DDS_Publisher_create_datawriter", "group___c_publication.html"),
    ChiQueryCase("FooDataWriter_write", "group___c_publication.html"),
    ChiQueryCase("DDS_Subscriber_create_datareader", "group___c_subscription.html"),
    ChiQueryCase("FooDataReader_take_next_sample", "group___c_subscription.html"),
    ChiQueryCase("DDS_WaitSet_wait", "group___c_infrastruct.html"),
    ChiQueryCase("DDS_RETCODE_TIMEOUT", "group___core_base_struct.html"),
    ChiQueryCase("DDS_RETCODE_BAD_PARAMETER", "group___core_base_struct.html"),
    ChiQueryCase("DDS_RELIABLE_RELIABILITY_QOS", "group___core_qos_struct.html"),
    ChiQueryCase("DDS_BEST_EFFORT_RELIABILITY_QOS", "group___core_qos_struct.html"),
    ChiQueryCase("DDS_DomainParticipant_assert_liveliness", "group___c_domain.html"),
)


@pytest.fixture
def local_chm() -> Path:
    """读取未纳入 Git 的本地 CHM 测试手册。"""
    configured = os.getenv(CHM_PATH_ENV, "").strip()
    source = Path(configured).expanduser() if configured else DEFAULT_CHM
    if not source.is_file():
        pytest.skip(
            f"CHM unavailable: place it at {DEFAULT_CHM} "
            f"or set {CHM_PATH_ENV} to a container-visible path"
        )
    return source


def document_evidence(result: Any, document_id: str) -> list[Any]:
    return [item for item in result.evidence if item.document_id == document_id]


def assert_provenance(evidence: Any) -> None:
    assert evidence.source_name == "ZRDDS_C_UserManual.chm"
    assert not evidence.source_name.lower().endswith(".chi")
    assert evidence.chunk_id
    assert evidence.index_version == 1

    assert evidence.metadata["source_type"] == "chm"
    assert evidence.metadata["logical_document_type"] == "chm_topic"
    assert evidence.metadata["topic_path"]
    assert evidence.metadata["topic_title"]
    assert evidence.metadata["topic_order"].isdigit()
    assert evidence.metadata["heading_path"]

    assert evidence.locator.language == "html"
    assert evidence.locator.start_line >= 1
    assert evidence.locator.end_line >= evidence.locator.start_line
    assert evidence.locator.metadata["topic_path"]
    assert evidence.locator.metadata["topic_title"]
    assert evidence.locator.metadata["heading_path"]

    if evidence.metadata.get("retrieval_role") in {
        "topic_neighbor",
        "chi_topic_reference",
    }:
        assert evidence.metadata["anchor_chunk_id"]
        if evidence.metadata["retrieval_role"] == "topic_neighbor":
            assert evidence.metadata["neighbor_distance"] in {"-1", "1"}
        else:
            assert evidence.metadata["chi_topic_url"]
        assert not evidence.scores.HasField("fusion_score")
    else:
        assert evidence.scores.HasField("fusion_score")
        assert math.isfinite(evidence.scores.fusion_score)
        assert evidence.scores.fusion_score > 0
        assert evidence.scores.HasField("dense_score") or evidence.scores.HasField("sparse_score")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_chm_ingestion_topic_boundaries_and_retrieval_quality(
    local_chm: Path,
    rag_stub: object,
    embedding_runtime: EmbeddingRuntime,
) -> None:
    dataset_id: str | None = None
    document_id: str | None = None

    try:
        reused_dataset_id = os.getenv(CHM_REUSE_DATASET_ID_ENV, "").strip()
        reused_document_id = os.getenv(CHM_REUSE_DOCUMENT_ID_ENV, "").strip()
        assert bool(reused_dataset_id) == bool(reused_document_id), (
            f"{CHM_REUSE_DATASET_ID_ENV} and {CHM_REUSE_DOCUMENT_ID_ENV} "
            "must be configured together"
        )

        if reused_dataset_id:
            dataset_id = reused_dataset_id
            document_id = reused_document_id
        else:
            dataset_id = await create_dataset(
                rag_stub,
                embedding_runtime,
                "local-zrdds-chm",
            )

            document_id, job_id = await submit_document(
                rag_stub,
                dataset_id,
                local_chm,
            )

            job = await wait_for_job(
                rag_stub,
                job_id,
                deadline_seconds=int(
                    os.getenv(
                        CHM_INGESTION_TIMEOUT_ENV,
                        str(DEFAULT_CHM_INGESTION_TIMEOUT_SECONDS),
                    )
                ),
            )

            assert job.document_id == document_id
            assert job.status == rag_service_pb2.JOB_STATUS_SUCCEEDED
            assert job.task_status == rag_service_pb2.TASK_STATUS_SUCCEEDED
            assert job.progress == pytest.approx(1.0)

            # 同一内容使用新的幂等键再次提交，应复用 canonical 文档和 Job。
            canonical_document_id, canonical_job_id = await submit_document(
                rag_stub,
                dataset_id,
                local_chm,
            )
            assert canonical_document_id == document_id
            assert canonical_job_id == job_id

        reciprocal_ranks: list[float] = []
        phrase_hits = 0
        case_reports: list[dict[str, Any]] = []

        for case in QUERY_CASES:
            result = await retrieve(
                rag_stub,
                dataset_id,
                case.query,
            )
            evidence = document_evidence(result, document_id)
            anchors = [
                item for item in evidence if item.metadata.get("retrieval_role") != "topic_neighbor"
            ]

            assert evidence, f"no evidence returned for query: {case.query}"
            assert len(anchors) <= 6

            for item in evidence:
                assert_provenance(item)

            matching_ranks = [
                rank
                for rank, item in enumerate(anchors, start=1)
                if item.metadata["topic_path"] in case.relevant_topic_paths
            ]

            assert matching_ranks, (
                f"target Topic {case.topic_path!r} was not found "
                f"within top 6 for query {case.query!r}; returned paths: "
                f"{[item.metadata['topic_path'] for item in anchors]}"
            )

            rank = matching_ranks[0]
            reciprocal_ranks.append(1.0 / rank)

            target_content = "\n".join(
                item.content_with_weight
                for item in evidence
                if item.metadata["topic_path"] in case.relevant_topic_paths
            )
            phrase_hit = case.required_phrase in target_content
            if phrase_hit:
                phrase_hits += 1
            case_reports.append(
                {
                    "query": case.query,
                    "expected_topic_path": case.topic_path,
                    "relevant_topic_paths": case.relevant_topic_paths,
                    "required_phrase": case.required_phrase,
                    "target_rank": rank,
                    "phrase_hit": phrase_hit,
                    "evidence": [
                        {
                            "rank": evidence_rank,
                            "chunk_id": item.chunk_id,
                            "topic_path": item.metadata["topic_path"],
                            "topic_title": item.metadata["topic_title"],
                            "heading_path": item.metadata["heading_path"],
                            "start_line": item.locator.start_line,
                            "end_line": item.locator.end_line,
                            "content_preview": item.content_with_weight[:500],
                        }
                        for evidence_rank, item in enumerate(evidence, start=1)
                    ],
                }
            )

        recall_at_6 = len(reciprocal_ranks) / len(QUERY_CASES)
        mrr_at_6 = sum(reciprocal_ranks) / len(QUERY_CASES)
        phrase_hit_rate = phrase_hits / len(QUERY_CASES)
        report_path = Path(os.getenv(CHM_REPORT_PATH_ENV, str(DEFAULT_CHM_REPORT)))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "dataset_id": dataset_id,
                    "document_id": document_id,
                    "metrics": {
                        "recall_at_6": recall_at_6,
                        "mrr_at_6": mrr_at_6,
                        "phrase_hit_rate": phrase_hit_rate,
                    },
                    "cases": case_reports,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            "\nCHM retrieval metrics:"
            f"\n  Recall@6={recall_at_6:.4f}"
            f"\n  MRR@6={mrr_at_6:.4f}"
            f"\n  phrase_hit_rate={phrase_hit_rate:.4f}"
            f"\n  report={report_path}"
        )

        assert recall_at_6 == pytest.approx(1.0)
        assert mrr_at_6 >= 0.65

        # Topic 是硬边界：两个示例页的独有内容不能混入彼此的 chunk。
        wait_result = await retrieve(
            rag_stub,
            dataset_id,
            "DDS WaitSet 条件等待模型",
        )
        for item in document_evidence(wait_result, document_id):
            if item.metadata["topic_path"] == "_wait-_condition_8c-example.html":
                assert "使用BatchQoS进行批量发送" not in item.content_with_weight

        batch_result = await retrieve(
            rag_stub,
            dataset_id,
            "BatchQoS 批量发送端",
        )
        for item in document_evidence(batch_result, document_id):
            if item.metadata["topic_path"] == "_batch_qos_2main_pub_8c-example.html":
                assert "条件-等待模型" not in item.content_with_weight

    finally:
        if dataset_id is not None:
            print(
                "\nCHM knowledge base retained for manual cleanup:"
                f"\n  dataset_id={dataset_id}"
                f"\n  document_id={document_id or '<not-created>'}"
            )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_chi_sidecar_participates_in_same_dataset_retrieval(
    rag_stub: object,
) -> None:
    """用真实 CHI 的 13 个接口/枚举查询验证 Topic 映射和 CHM 正文桥接。"""
    dataset_id = os.getenv(CHM_REUSE_DATASET_ID_ENV, "").strip()
    if not dataset_id:
        pytest.skip(f"set {CHM_REUSE_DATASET_ID_ENV} to an existing CHM Dataset")
    chi_path = Path(os.getenv(CHI_PATH_ENV, str(DEFAULT_CHI))).expanduser()
    if not chi_path.is_file():
        pytest.skip(f"CHI unavailable: place it at {DEFAULT_CHI} or set {CHI_PATH_ENV}")

    chi_document_id, job_id = await submit_document(rag_stub, dataset_id, chi_path)
    await wait_for_job(rag_stub, job_id, deadline_seconds=1200)

    reciprocal_ranks: list[float] = []
    phrase_hits = 0
    bridge_hits = 0
    provenance_hits = 0
    case_reports: list[dict[str, Any]] = []
    for case in CHI_QUERY_CASES:
        result = await retrieve(rag_stub, dataset_id, case.query)
        direct = [
            item
            for item in result.evidence
            if item.metadata.get("source_type") == "chi"
            and item.metadata.get("retrieval_role", "anchor") == "anchor"
        ]
        matching_ranks = [
            rank
            for rank, item in enumerate(direct, start=1)
            if item.metadata.get("topic_path") == case.topic_path
            and case.query in item.content_with_weight
        ]
        rank = matching_ranks[0] if matching_ranks else None
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        phrase_hit = any(case.query in item.content_with_weight for item in direct)
        bridge_hit = any(
            (
                item.metadata.get("retrieval_role") == "chi_topic_reference"
                or item.metadata.get("chi_reference_anchor_chunk_id")
            )
            and item.metadata.get("topic_path") == case.topic_path
            and item.source_name == "ZRDDS_C_UserManual.chm"
            for item in result.evidence
        )
        matching = [
            item
            for item in direct
            if item.metadata.get("topic_path") == case.topic_path
            and case.query in item.content_with_weight
        ]
        provenance_complete = bool(matching) and all(
            item.document_id == chi_document_id
            and item.source_name == "ZRDDS_C_UserManual.chi"
            and item.metadata.get("logical_document_type") == "chm_index"
            and item.metadata.get("chi_stream") == "$WWKeywordLinks/BTree"
            and item.metadata.get("associated_chm_source_name") == "ZRDDS_C_UserManual.chm"
            and item.metadata.get("chi_topic_index", "").isdigit()
            and item.metadata.get("topic_title")
            and item.metadata.get("topic_url")
            and item.locator.symbol
            and item.locator.language == "chi"
            for item in matching
        )
        phrase_hits += int(phrase_hit)
        bridge_hits += int(bridge_hit)
        provenance_hits += int(provenance_complete)
        case_reports.append(
            {
                "query": case.query,
                "expected_topic_path": case.topic_path,
                "target_rank": rank,
                "phrase_hit": phrase_hit,
                "chm_bridge_hit": bridge_hit,
                "provenance_complete": provenance_complete,
                "evidence": [
                    {
                        "rank": evidence_rank,
                        "source_name": item.source_name,
                        "source_type": item.metadata.get("source_type"),
                        "retrieval_role": item.metadata.get("retrieval_role", "anchor"),
                        "chunk_id": item.chunk_id,
                        "topic_path": item.metadata.get("topic_path"),
                        "topic_title": item.metadata.get("topic_title"),
                        "topic_url": item.metadata.get("topic_url"),
                        "anchor": item.metadata.get("anchor"),
                        "symbol": item.locator.symbol,
                        "content_preview": item.content_with_weight[:500],
                    }
                    for evidence_rank, item in enumerate(result.evidence, start=1)
                ],
            }
        )

    query_count = len(CHI_QUERY_CASES)
    recall_at_6 = sum(rank > 0 for rank in reciprocal_ranks) / query_count
    mrr_at_6 = sum(reciprocal_ranks) / query_count
    phrase_hit_rate = phrase_hits / query_count
    chm_bridge_hit_rate = bridge_hits / query_count
    provenance_complete_rate = provenance_hits / query_count
    report_path = Path(os.getenv(CHI_REPORT_PATH_ENV, str(DEFAULT_CHI_REPORT)))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "dataset_id": dataset_id,
                "chi_document_id": chi_document_id,
                "query_count": query_count,
                "metrics": {
                    "recall_at_6": recall_at_6,
                    "mrr_at_6": mrr_at_6,
                    "phrase_hit_rate": phrase_hit_rate,
                    "chm_bridge_hit_rate": chm_bridge_hit_rate,
                    "provenance_complete_rate": provenance_complete_rate,
                },
                "cases": case_reports,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        "\nExtended CHI retrieval metrics:"
        f"\n  queries={query_count}"
        f"\n  Recall@6={recall_at_6:.4f}"
        f"\n  MRR@6={mrr_at_6:.4f}"
        f"\n  phrase_hit_rate={phrase_hit_rate:.4f}"
        f"\n  chm_bridge_hit_rate={chm_bridge_hit_rate:.4f}"
        f"\n  provenance_complete_rate={provenance_complete_rate:.4f}"
        f"\n  report={report_path}"
    )

    assert recall_at_6 == pytest.approx(1.0)
    assert mrr_at_6 >= 0.65
    assert phrase_hit_rate == pytest.approx(1.0)
    assert chm_bridge_hit_rate == pytest.approx(1.0)
    assert provenance_complete_rate == pytest.approx(1.0)
