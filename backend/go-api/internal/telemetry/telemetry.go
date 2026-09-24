package telemetry

import (
	"context"
	"errors"
	"os"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

const scope = "rag-go-api"

// Init installs bounded OTLP exporters only when the private Collector is configured.
// A Collector outage must never prevent the product API from starting.
func Init(ctx context.Context) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.TraceContext{})
	endpoint := strings.TrimRight(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"), "/")
	if endpoint == "" {
		return func(context.Context) error { return nil }, nil
	}
	service := os.Getenv("OTEL_SERVICE_NAME")
	if service == "" {
		service = "rag-go-api"
	}
	res, err := resource.New(ctx, resource.WithAttributes(attribute.String("service.name", service)))
	if err != nil {
		return nil, err
	}
	traces, err := otlptracehttp.New(ctx,
		otlptracehttp.WithEndpointURL(endpoint+"/v1/traces"),
		otlptracehttp.WithTimeout(2*time.Second),
	)
	if err != nil {
		return nil, err
	}
	metrics, err := otlpmetrichttp.New(ctx,
		otlpmetrichttp.WithEndpointURL(endpoint+"/v1/metrics"),
		otlpmetrichttp.WithTimeout(2*time.Second),
	)
	if err != nil {
		_ = traces.Shutdown(ctx)
		return nil, err
	}
	traceProvider := sdktrace.NewTracerProvider(
		sdktrace.WithResource(res),
		sdktrace.WithBatcher(traces,
			sdktrace.WithBatchTimeout(5*time.Second),
			sdktrace.WithMaxQueueSize(512),
		),
	)
	metricProvider := sdkmetric.NewMeterProvider(
		sdkmetric.WithResource(res),
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metrics, sdkmetric.WithInterval(10*time.Second))),
	)
	otel.SetTracerProvider(traceProvider)
	otel.SetMeterProvider(metricProvider)
	return func(ctx context.Context) error {
		return errors.Join(metricProvider.Shutdown(ctx), traceProvider.Shutdown(ctx))
	}, nil
}

func IDs(ctx context.Context) (string, string) {
	span := trace.SpanFromContext(ctx).SpanContext()
	if !span.IsValid() {
		return "", ""
	}
	return span.TraceID().String(), span.SpanID().String()
}

func StartRun(ctx context.Context, runID string) (context.Context, trace.Span, time.Time) {
	ctx, span := otel.Tracer(scope).Start(ctx, "rag.chat", trace.WithAttributes(attribute.String("run.id", runID)))
	return ctx, span, time.Now()
}

func EndRun(ctx context.Context, span trace.Span, started time.Time, stopReason string, runErr error) {
	defer span.End()
	outcome := "succeeded"
	if runErr != nil {
		outcome = "failed"
		if errors.Is(runErr, context.Canceled) || errors.Is(runErr, context.DeadlineExceeded) {
			outcome = "cancelled"
		}
		span.SetStatus(codes.Error, "run_failed")
	}
	stopReason = safeStopReason(stopReason)
	span.SetAttributes(attribute.String("outcome", outcome), attribute.String("stop.reason", stopReason))
	recordCounter(ctx, "rag_chat_runs_total", 1,
		attribute.String("outcome", outcome), attribute.String("stop_reason", stopReason))
	recordDuration(ctx, "rag_chat_duration_seconds", time.Since(started), nil)
}

func StartPhase(ctx context.Context, phase string) (context.Context, trace.Span) {
	phase = safePhase(phase)
	ctx, span := otel.Tracer(scope).Start(ctx, "rag.agent."+phase,
		trace.WithAttributes(attribute.String("stage", phase)))
	return ctx, span
}

func EndPhase(ctx context.Context, span trace.Span, phase string, modelCalls int, phaseErr error) {
	defer span.End()
	if phaseErr != nil {
		span.SetStatus(codes.Error, "phase_failed")
	}
	if modelCalls > 0 {
		recordCounter(ctx, "rag_agent_model_calls_total", int64(modelCalls),
			attribute.String("phase", safePhase(phase)))
	}
}

func StartGRPC(ctx context.Context, method string) (context.Context, trace.Span, time.Time) {
	method = safeMethod(method)
	ctx, span := otel.Tracer(scope).Start(ctx, "rag.grpc."+method,
		trace.WithAttributes(attribute.String("rpc.method", method)))
	return ctx, span, time.Now()
}

func EndGRPC(ctx context.Context, span trace.Span, started time.Time, method string, rpcErr error) {
	defer span.End()
	outcome := "succeeded"
	if rpcErr != nil {
		outcome = "failed"
		if errors.Is(rpcErr, context.Canceled) || errors.Is(rpcErr, context.DeadlineExceeded) {
			outcome = "cancelled"
		}
		span.SetStatus(codes.Error, "grpc_failed")
	}
	recordDuration(ctx, "rag_grpc_client_duration_seconds", time.Since(started), []attribute.KeyValue{
		attribute.String("method", safeMethod(method)), attribute.String("outcome", outcome),
	})
}

func recordCounter(ctx context.Context, name string, value int64, labels ...attribute.KeyValue) {
	defer func() { _ = recover() }()
	counter, err := otel.Meter(scope).Int64Counter(name)
	if err == nil {
		counter.Add(ctx, value, metric.WithAttributes(labels...))
	}
}

func recordDuration(ctx context.Context, name string, value time.Duration, labels []attribute.KeyValue) {
	defer func() { _ = recover() }()
	histogram, err := otel.Meter(scope).Float64Histogram(name)
	if err == nil {
		histogram.Record(ctx, value.Seconds(), metric.WithAttributes(labels...))
	}
}

func safePhase(phase string) string {
	switch phase {
	case "route", "model", "tool", "assess", "rewrite", "finalize", "complete":
		return phase
	default:
		return "unknown"
	}
}

func safeStopReason(reason string) string {
	switch reason {
	case "completed", "direct_reply", "clarification", "evidence_sufficient", "evidence_insufficient", "budget_exceeded", "cancelled", "provider_error", "invalid_tool_call":
		return reason
	default:
		return "unknown"
	}
}

func safeMethod(full string) string {
	switch full {
	case "CreateDataset", "BindEmbeddingProfile", "DeleteDataset", "ReindexDocument", "SubmitDocument", "GetJob", "Retrieve", "RetryJob", "CancelJob", "DeleteDocument", "GetSourceTopic":
		return full
	default:
		return "other"
	}
}
