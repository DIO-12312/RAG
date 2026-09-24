"""Bounded, content-free OpenTelemetry instrumentation for RAG process roles."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from functools import lru_cache
from time import perf_counter
from urllib.parse import urlparse

from opentelemetry import metrics, propagate, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_ALLOWED_ATTRIBUTES = frozenset(
    {
        "task.id",
        "job.id",
        "run.id",
        "stage",
        "outcome",
        "error.code",
        "item.count",
        "delivery.count",
    }
)
_STAGES = frozenset(
    {
        "object_read",
        "parse",
        "chunk",
        "embedding",
        "index",
        "complete",
        "dense",
        "sparse",
        "visibility",
        "rerank",
        "evidence",
        "finalize",
        "publish",
    }
)
_OUTCOMES = frozenset({"succeeded", "failed", "cancelled", "retry", "skipped"})
_initialized = False
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def _endpoint() -> str | None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    parsed = urlparse(endpoint)
    if not endpoint or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return endpoint


def configure(role: str) -> None:
    """Create SDK exporters once per process; absence of OTLP keeps tests offline."""

    global _initialized, _tracer_provider, _meter_provider
    if _initialized:
        return
    _initialized = True
    endpoint = _endpoint()
    if endpoint is None:
        return
    service_name = os.getenv("OTEL_SERVICE_NAME", f"rag-python-{role}")
    resource = Resource.create({SERVICE_NAME: service_name})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=2),
            max_queue_size=512,
            max_export_batch_size=128,
            schedule_delay_millis=5000,
        )
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=2),
                export_interval_millis=10000,
                export_timeout_millis=3000,
            )
        ],
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    propagate.set_global_textmap(TraceContextTextMapPropagator())
    _tracer_provider, _meter_provider = tracer_provider, meter_provider


async def shutdown() -> None:
    """Bound shutdown so a missing collector cannot hold an application process."""

    for provider in (_meter_provider, _tracer_provider):
        if provider is not None:
            with suppress(Exception):
                await asyncio.wait_for(asyncio.to_thread(provider.shutdown), timeout=3)


def _safe_attributes(attributes: dict[str, object]) -> dict[str, str | int | float]:
    return {
        key: value
        for key, value in attributes.items()
        if key in _ALLOWED_ATTRIBUTES and isinstance(value, str | int | float)
    }


@contextmanager
def span(name: str, **attributes: object) -> Iterator[None]:
    """Add only allowlisted attributes; never serialize prompts or source text."""

    tracer = trace.get_tracer("rag_mvp")
    with tracer.start_as_current_span(name, attributes=_safe_attributes(attributes)) as current:
        try:
            yield
        except Exception as error:
            current.set_status(Status(StatusCode.ERROR, type(error).__name__))
            raise


@lru_cache(maxsize=1)
def _instruments() -> tuple[object, object, object, object]:
    meter = metrics.get_meter("rag_mvp")
    return (
        meter.create_counter("rag_ingestion_tasks_total"),
        meter.create_histogram("rag_ingestion_stage_duration_seconds", unit="s"),
        meter.create_histogram("rag_retrieval_duration_seconds", unit="s"),
        meter.create_counter("rag_outbox_publish_total"),
    )


def record_task(stage: str, outcome: str) -> None:
    if stage not in _STAGES or outcome not in _OUTCOMES:
        return
    with suppress(Exception):
        counter = _instruments()[0]
        counter.add(1, {"stage": stage, "outcome": outcome})  # type: ignore[attr-defined]


def record_outbox(outcome: str) -> None:
    if outcome not in _OUTCOMES:
        return
    with suppress(Exception):
        counter = _instruments()[3]
        counter.add(1, {"outcome": outcome})  # type: ignore[attr-defined]


@asynccontextmanager
async def stage(name: str, *, kind: str = "ingestion") -> AsyncIterator[None]:
    """Measure a fixed stage name around the real async operation."""

    started = perf_counter()
    with span(f"rag.{kind}.{name}", stage=name):
        try:
            yield
        finally:
            if name in _STAGES:
                with suppress(Exception):
                    instrument = _instruments()[1 if kind == "ingestion" else 2]
                    instrument.record(perf_counter() - started, {"stage": name})  # type: ignore[attr-defined]


def current_ids() -> tuple[str | None, str | None]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"
