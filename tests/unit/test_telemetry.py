from __future__ import annotations

import grpc
import pytest
from opentelemetry.instrumentation.grpc import aio_server_interceptor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.testing import capture_logs

from rag_mvp import telemetry
from rag_mvp.observability import emit_event


@pytest.mark.asyncio
async def test_grpc_server_inherits_traceparent_and_logs_safe_span_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        telemetry.trace, "get_tracer", lambda *_args, **_kwargs: provider.get_tracer("test")
    )

    async def ping(_request: bytes, _context: grpc.aio.ServicerContext[bytes, bytes]) -> bytes:
        with telemetry.span(
            "rag.retrieval.dense", stage="dense", prompt="secret", **{"task.id": "task-1"}
        ):
            emit_event("trace_test", stage="dense")
        return b"ok"

    server = grpc.aio.server(interceptors=[aio_server_interceptor()])
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "test.Trace",
                {"Ping": grpc.unary_unary_rpc_method_handler(ping)},
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    trace_id = "0123456789abcdef0123456789abcdef"
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            with capture_logs() as logs:
                reply = await channel.unary_unary("/test.Trace/Ping")(
                    b"request", metadata=(("traceparent", f"00-{trace_id}-0123456789abcdef-01"),)
                )
        assert reply == b"ok"
    finally:
        await server.stop(0)
        provider.shutdown()

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    server_span = next(span for span in spans if span.name != "rag.retrieval.dense")
    child = next(span for span in spans if span.name == "rag.retrieval.dense")
    assert f"{server_span.context.trace_id:032x}" == trace_id
    assert child.context.trace_id == server_span.context.trace_id
    assert child.parent.span_id == server_span.context.span_id
    assert child.attributes["task.id"] == "task-1"
    assert "prompt" not in child.attributes
    assert logs[0]["trace_id"] == trace_id
    assert len(logs[0]["span_id"]) == 16


@pytest.mark.asyncio
async def test_metrics_have_only_fixed_stage_and_outcome_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(
        telemetry.metrics, "get_meter", lambda *_args, **_kwargs: provider.get_meter("test")
    )
    telemetry._instruments.cache_clear()
    try:
        telemetry.record_task("complete", "succeeded")
        telemetry.record_task("document-123", "succeeded")
        telemetry.record_outbox("retry")
        async with telemetry.stage("dense", kind="retrieval"):
            pass
        data = reader.get_metrics_data()
        observed = {
            metric.name: metric
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert {
            "rag_ingestion_tasks_total",
            "rag_retrieval_duration_seconds",
            "rag_outbox_publish_total",
        } <= observed.keys()
        points = observed["rag_ingestion_tasks_total"].data.data_points
        assert len(points) == 1
        assert dict(points[0].attributes) == {"stage": "complete", "outcome": "succeeded"}
    finally:
        telemetry._instruments.cache_clear()
        provider.shutdown()


@pytest.mark.asyncio
async def test_metric_exporter_failure_does_not_interrupt_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_instruments() -> None:
        raise OSError("collector unavailable")

    monkeypatch.setattr(telemetry, "_instruments", unavailable_instruments)
    completed = False
    async with telemetry.stage("dense", kind="retrieval"):
        telemetry.record_task("complete", "succeeded")
        telemetry.record_outbox("succeeded")
        completed = True
    assert completed
