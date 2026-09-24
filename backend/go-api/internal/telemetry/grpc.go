package telemetry

import (
	"context"
	"strings"
	"sync"

	"go.opentelemetry.io/otel"
	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
)

type metadataCarrier struct{ values metadata.MD }

func (c metadataCarrier) Get(key string) string {
	if values := c.values.Get(key); len(values) > 0 {
		return values[0]
	}
	return ""
}
func (c metadataCarrier) Set(key, value string) { c.values.Set(key, value) }
func (c metadataCarrier) Keys() []string {
	keys := make([]string, 0, len(c.values))
	for key := range c.values {
		keys = append(keys, key)
	}
	return keys
}

func inject(ctx context.Context) context.Context {
	md, _ := metadata.FromOutgoingContext(ctx)
	md = md.Copy()
	otel.GetTextMapPropagator().Inject(ctx, metadataCarrier{values: md})
	return metadata.NewOutgoingContext(ctx, md)
}

func methodName(fullMethod string) string {
	index := strings.LastIndex(fullMethod, "/")
	if index < 0 {
		return "other"
	}
	return safeMethod(fullMethod[index+1:])
}

func UnaryClientInterceptor() grpc.UnaryClientInterceptor {
	return func(ctx context.Context, fullMethod string, request, response any, conn *grpc.ClientConn, invoke grpc.UnaryInvoker, options ...grpc.CallOption) error {
		method := methodName(fullMethod)
		ctx, span, started := StartGRPC(ctx, method)
		err := invoke(inject(ctx), fullMethod, request, response, conn, options...)
		EndGRPC(ctx, span, started, method, err)
		return err
	}
}

type observedStream struct {
	grpc.ClientStream
	finish func(error)
	once   sync.Once
	done   chan struct{}
}

func (s *observedStream) SendMsg(message any) error {
	err := s.ClientStream.SendMsg(message)
	if err != nil {
		s.end(err)
	}
	return err
}

func (s *observedStream) RecvMsg(message any) error {
	err := s.ClientStream.RecvMsg(message)
	s.end(err)
	return err
}

func (s *observedStream) end(err error) {
	s.once.Do(func() {
		s.finish(err)
		close(s.done)
	})
}

func StreamClientInterceptor() grpc.StreamClientInterceptor {
	return func(ctx context.Context, descriptor *grpc.StreamDesc, conn *grpc.ClientConn, fullMethod string, open grpc.Streamer, options ...grpc.CallOption) (grpc.ClientStream, error) {
		method := methodName(fullMethod)
		ctx, span, started := StartGRPC(ctx, method)
		stream, err := open(inject(ctx), descriptor, conn, fullMethod, options...)
		if err != nil {
			EndGRPC(ctx, span, started, method, err)
			return nil, err
		}
		observed := &observedStream{
			ClientStream: stream,
			finish: func(err error) {
				EndGRPC(ctx, span, started, method, err)
			},
			done: make(chan struct{}),
		}
		go func() {
			select {
			case <-ctx.Done():
				observed.end(ctx.Err())
			case <-observed.done:
			}
		}()
		return observed, nil
	}
}
