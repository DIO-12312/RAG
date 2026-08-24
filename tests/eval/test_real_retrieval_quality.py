"""Real-model and real-Elasticsearch retrieval quality gate through gRPC."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from rag_mvp.domain.ids import chunk_id
from rag_mvp.retrieval.evaluation import EvaluationCase, evaluate_rankings
from rag_mvp.rpc.generated import rag_service_pb2
from tests.e2e.conftest import (
    EmbeddingRuntime,
    create_dataset,
    retrieve,
    unique_id,
    wait_for_job,
)


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    source_name: str
    content: str


@dataclass(frozen=True, slots=True)
class QualityQuestion:
    query: str
    document_index: int
    source_line: int


CORPUS = (
    CorpusDocument(
        "orion-vault.txt",
        "Orion Vault operations record.\n"
        "The vault validates archives with the cobalt-17 checksum.\n"
        "Its recovery terminal is beside the northern pier.\n"
        "Operators wait fourteen minutes before restoring an archive.",
    ),
    CorpusDocument(
        "lumen-orchard.txt",
        "Lumen Orchard cultivation record.\n"
        "The germination batch uses the jade seed marker.\n"
        "A complete irrigation cycle lasts twenty-eight hours.\n"
        "Harvest manifests are deposited at the river gate.",
    ),
    CorpusDocument(
        "helix-foundry.txt",
        "Helix Foundry maintenance record.\n"
        "The calibration spindle runs at forty-one revolutions per minute.\n"
        "Technicians secure the chamber with a titanium latch.\n"
        "Finished assemblies leave through bay seven.",
    ),
    CorpusDocument(
        "sable-observatory.txt",
        "Sable Observatory observation record.\n"
        "The primary spectral filter is centered at 620 nanometers.\n"
        "The nightly alignment begins exactly at midnight.\n"
        "The backup telescope is housed under the glass dome.",
    ),
    CorpusDocument(
        "nimbus-archive.txt",
        "Nimbus Archive custody record.\n"
        "Restricted catalogs are protected by the amber cipher.\n"
        "The master catalog is stored on shelf twelve.\n"
        "Every preservation set contains three copies.",
    ),
    CorpusDocument(
        "ember-canal.txt",
        "Ember Canal pressure record.\n"
        "The east lock operates at nine bar.\n"
        "Routine inspections happen every Tuesday.\n"
        "Emergency flow is isolated with the bronze valve.",
    ),
    CorpusDocument(
        "polar-relay.txt",
        "Polar Relay transmission record.\n"
        "The acknowledgement timeout is eighty-eight seconds.\n"
        "The relay station stands on Delta Ridge.\n"
        "Its emergency transmitter uses the silver antenna.",
    ),
    CorpusDocument(
        "cedar-clinic.txt",
        "Cedar Clinic storage record.\n"
        "The specimen cabinet is maintained at sixteen degrees Celsius.\n"
        "Each transport case carries five vials.\n"
        "Reserve samples remain in the west refrigerator.",
    ),
    CorpusDocument(
        "mosaic-engine.txt",
        "Mosaic Engine scheduling record.\n"
        "The rendering pool is configured with twenty-three threads.\n"
        "Priority jobs enter through the violet queue.\n"
        "A planned restart may begin only after noon.",
    ),
    CorpusDocument(
        "atlas-garden.txt",
        "Atlas Garden irrigation record.\n"
        "Each greenhouse receives forty-six liters per cycle.\n"
        "The nocturnal watering window opens at moonrise.\n"
        "Emergency water is drawn from the east cistern.",
    ),
)

QUESTIONS = (
    QualityQuestion("Which checksum validates Orion Vault archives?", 0, 2),
    QualityQuestion("Where is the Orion Vault recovery terminal?", 0, 3),
    QualityQuestion("How long do Orion operators wait before archive restoration?", 0, 4),
    QualityQuestion("What marker identifies the Lumen Orchard germination batch?", 1, 2),
    QualityQuestion("How long is a complete Lumen Orchard irrigation cycle?", 1, 3),
    QualityQuestion("Where are Lumen Orchard harvest manifests deposited?", 1, 4),
    QualityQuestion("What speed is used by the Helix Foundry calibration spindle?", 2, 2),
    QualityQuestion("What secures the Helix Foundry chamber?", 2, 3),
    QualityQuestion("Which bay handles completed Helix Foundry assemblies?", 2, 4),
    QualityQuestion("What wavelength is the Sable Observatory spectral filter?", 3, 2),
    QualityQuestion("When does Sable Observatory nightly alignment begin?", 3, 3),
    QualityQuestion("Where is the Sable backup telescope housed?", 3, 4),
    QualityQuestion("Which cipher protects restricted Nimbus Archive catalogs?", 4, 2),
    QualityQuestion("On which shelf is the Nimbus master catalog stored?", 4, 3),
    QualityQuestion("How many copies are in a Nimbus preservation set?", 4, 4),
    QualityQuestion("What pressure does the Ember Canal east lock use?", 5, 2),
    QualityQuestion("On which day are Ember Canal inspections performed?", 5, 3),
    QualityQuestion("Which valve isolates Ember Canal emergency flow?", 5, 4),
    QualityQuestion("What is the Polar Relay acknowledgement timeout?", 6, 2),
    QualityQuestion("Where does the Polar Relay station stand?", 6, 3),
    QualityQuestion("Which antenna is used by the Polar emergency transmitter?", 6, 4),
    QualityQuestion("At what temperature is the Cedar Clinic specimen cabinet kept?", 7, 2),
    QualityQuestion("How many vials are in a Cedar Clinic transport case?", 7, 3),
    QualityQuestion("Where does Cedar Clinic keep reserve samples?", 7, 4),
    QualityQuestion("How many threads are configured for the Mosaic rendering pool?", 8, 2),
    QualityQuestion("Which queue receives Mosaic Engine priority jobs?", 8, 3),
    QualityQuestion("When may a Mosaic Engine planned restart begin?", 8, 4),
    QualityQuestion("How much water does each Atlas Garden greenhouse receive?", 9, 2),
    QualityQuestion("When does the Atlas Garden nocturnal watering window open?", 9, 3),
    QualityQuestion("Which cistern supplies Atlas Garden emergency water?", 9, 4),
)


def _result(response: object) -> object:
    outcome = response.WhichOneof("outcome")  # type: ignore[attr-defined]
    if outcome == "result":
        return response.result  # type: ignore[attr-defined,no-any-return]
    error = response.error  # type: ignore[attr-defined]
    raise AssertionError(f"gRPC business error {error.code}: {error.message}")


async def _submit_text(
    stub: object,
    dataset_id: str,
    document: CorpusDocument,
) -> tuple[str, str]:
    async def frames() -> AsyncIterator[rag_service_pb2.UploadDocumentRequest]:
        yield rag_service_pb2.UploadDocumentRequest(
            header=rag_service_pb2.UploadHeader(
                context=rag_service_pb2.RequestContext(
                    request_id=unique_id("eval-submit-request"),
                    idempotency_key=unique_id("eval-submit-key"),
                ),
                dataset_id=dataset_id,
                source_name=document.source_name,
            )
        )
        yield rag_service_pb2.UploadDocumentRequest(data=document.content.encode("utf-8"))

    response = await stub.SubmitDocument(frames(), timeout=60)  # type: ignore[attr-defined]
    result = _result(response)
    return str(result.document_id), str(result.job_id)  # type: ignore[attr-defined]


@pytest.mark.eval
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_thirty_question_quality_baseline(
    rag_stub: object,
    embedding_runtime: EmbeddingRuntime,
) -> None:
    assert len(QUESTIONS) == 30
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "real-quality-30")

    submitted = [await _submit_text(rag_stub, dataset_id, document) for document in CORPUS]
    for _, job_id in submitted:
        await wait_for_job(rag_stub, job_id)

    expected_chunks = tuple(
        chunk_id(document.content, document_id)
        for document, (document_id, _) in zip(CORPUS, submitted, strict=True)
    )
    cases: list[EvaluationCase] = []
    diagnostics: list[str] = []
    for question in QUESTIONS:
        result = await retrieve(rag_stub, dataset_id, question.query)
        target_chunk = expected_chunks[question.document_index]
        retrieved_ids = tuple(item.chunk_id for item in result.evidence)
        target = next((item for item in result.evidence if item.chunk_id == target_chunk), None)
        document = CORPUS[question.document_index]
        locator_matches = bool(
            target is not None
            and target.source_name == document.source_name
            and target.locator.start_line <= question.source_line <= target.locator.end_line
        )
        cases.append(
            EvaluationCase(
                relevant_chunk_ids=(target_chunk,),
                retrieved_chunk_ids=retrieved_ids,
                locator_matches=locator_matches,
            )
        )
        rank = next(
            (
                position
                for position, value in enumerate(retrieved_ids, start=1)
                if value == target_chunk
            ),
            None,
        )
        if rank != 1 or not locator_matches:
            stage_scores = [
                (
                    item.source_name,
                    item.scores.dense_score,
                    item.scores.sparse_score,
                    item.scores.fusion_score,
                )
                for item in result.evidence
            ]
            diagnostics.append(
                f"query={question.query!r} target={document.source_name} "
                f"rank={rank} locator={locator_matches} scores={stage_scores!r}"
            )

    metrics = evaluate_rankings(tuple(cases), k=6)

    assert metrics.recall_at_k >= 0.85, diagnostics
    assert metrics.mrr_at_k >= 0.70, diagnostics
    assert metrics.locator_accuracy == 1.0, diagnostics
