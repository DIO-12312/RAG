package telemetry

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"google.golang.org/grpc/metadata"
)

func TestRunAndGRPCPropagationKeepOneTraceAndSafeAttributes(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	old := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	defer func() { otel.SetTracerProvider(old); _ = provider.Shutdown(context.Background()) }()

	ctx, root, started := StartRun(context.Background(), "run-123")
	ctx, grpcSpan, grpcStarted := StartGRPC(ctx, "Retrieve")
	propagated := inject(ctx)
	md, ok := metadata.FromOutgoingContext(propagated)
	if !ok || len(md.Get("traceparent")) != 1 {
		t.Fatalf("missing traceparent: %v", md)
	}
	EndGRPC(ctx, grpcSpan, grpcStarted, "Retrieve", nil)
	EndRun(ctx, root, started, "completed", nil)
	spans := recorder.Ended()
	if len(spans) != 2 || spans[0].SpanContext().TraceID() != spans[1].SpanContext().TraceID() || spans[0].Parent().SpanID() != spans[1].SpanContext().SpanID() {
		t.Fatalf("bad parent chain: %v", spans)
	}
	for _, span := range spans {
		for _, attr := range span.Attributes() {
			if strings.Contains(attr.Value.Emit(), "private question") {
				t.Fatalf("sensitive value: %v", attr)
			}
		}
	}
}

func TestRunCancellationAndUnknownLabelsRemainSafe(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	old := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	defer func() { otel.SetTracerProvider(old); _ = provider.Shutdown(context.Background()) }()
	ctx, span, started := StartRun(context.Background(), "run-123")
	EndRun(ctx, span, started.Add(-time.Millisecond), "private question", errors.New("private question"))
	got := recorder.Ended()
	if len(got) != 1 {
		t.Fatal(got)
	}
	for _, attr := range got[0].Attributes() {
		if strings.Contains(attr.Value.Emit(), "private question") {
			t.Fatalf("sensitive attribute: %v", attr)
		}
	}
}
