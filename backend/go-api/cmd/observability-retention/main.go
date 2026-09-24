package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"rag-mvp/backend/go-api/internal/retention"
)

func main() {
	budget, err := strconv.ParseUint(os.Getenv("OBS_TEMPO_BUDGET_BYTES"), 10, 64)
	if err != nil {
		log.Fatal("OBS_TEMPO_BUDGET_BYTES must be an unsigned integer")
	}
	controller, err := retention.New(retention.Config{
		DataPath: "/var/tempo-data", OverrideDir: "/var/tempo-overrides",
		BudgetBytes: budget, TempoURL: "http://tempo:4318", Interval: time.Minute,
	})
	if err != nil {
		log.Fatal(err)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	go controller.Run(ctx)
	server := &http.Server{Addr: ":9470", Handler: controller, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		_ = server.Shutdown(shutdownCtx)
	}()
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
