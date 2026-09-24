package retention

import (
	"context"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

var retentionHours = [...]int{168, 96, 48, 24, 6, 1}

type Config struct {
	DataPath    string
	OverrideDir string
	BudgetBytes uint64
	TempoURL    string
	Interval    time.Duration
}

type Snapshot struct {
	UsedBytes      uint64
	BudgetBytes    uint64
	FreeBytes      uint64
	CapacityBytes  uint64
	RetentionHours int
	Paused         bool
	Error          string
}

type Controller struct {
	cfg      Config
	client   *http.Client
	mu       sync.RWMutex
	snapshot Snapshot
}

func New(cfg Config) (*Controller, error) {
	if cfg.DataPath == "" || cfg.OverrideDir == "" || cfg.TempoURL == "" || cfg.BudgetBytes < 1024*1024 {
		return nil, fmt.Errorf("invalid retention configuration")
	}
	if cfg.Interval <= 0 {
		cfg.Interval = time.Minute
	}
	return &Controller{cfg: cfg, client: &http.Client{Timeout: 5 * time.Second}, snapshot: Snapshot{
		BudgetBytes: cfg.BudgetBytes, RetentionHours: persistedRetention(cfg.OverrideDir), Paused: true, Error: "NOT_READY",
	}}, nil
}

func persistedRetention(dir string) int {
	content, err := os.ReadFile(filepath.Join(dir, "overrides.yaml"))
	if err != nil {
		return retentionHours[0]
	}
	for _, line := range strings.Split(string(content), "\n") {
		var hours int
		if _, err := fmt.Sscanf(strings.TrimSpace(line), "block_retention: %dh", &hours); err == nil {
			for _, allowed := range retentionHours {
				if hours == allowed {
					return hours
				}
			}
		}
	}
	return retentionHours[0]
}

func diskUsage(root string) (uint64, uint64, uint64, error) {
	var used uint64
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if !entry.Type().IsRegular() {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		used += uint64(info.Size())
		return nil
	})
	if err != nil {
		return 0, 0, 0, err
	}
	var stat syscall.Statfs_t
	if err := syscall.Statfs(root, &stat); err != nil {
		return 0, 0, 0, err
	}
	return used, stat.Bavail * uint64(stat.Bsize), stat.Blocks * uint64(stat.Bsize), nil
}

func nextRetention(hours int, shrink bool) int {
	index := 0
	for i, candidate := range retentionHours {
		if candidate == hours {
			index = i
			break
		}
	}
	if shrink && index < len(retentionHours)-1 {
		return retentionHours[index+1]
	}
	if !shrink && index > 0 {
		return retentionHours[index-1]
	}
	return hours
}

func writeOverride(dir string, hours int) error {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(dir, ".overrides-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	// Tempo replaces an entire tenant override. Preserve ingestion limits when changing retention.
	if _, err := fmt.Fprintf(file, "overrides:\n  single-tenant:\n    ingestion:\n      rate_limit_bytes: 15000000\n      burst_size_bytes: 20000000\n      max_traces_per_user: 10000\n    global:\n      max_bytes_per_trace: 5000000\n    compaction:\n      block_retention: %dh\n", hours); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Chmod(file.Name(), 0644); err != nil {
		return err
	}
	return os.Rename(file.Name(), filepath.Join(dir, "overrides.yaml"))
}

func (c *Controller) Check() Snapshot {
	used, free, capacity, err := diskUsage(c.cfg.DataPath)
	c.mu.Lock()
	defer c.mu.Unlock()
	s := c.snapshot
	if err != nil {
		s.Paused = true
		s.Error = "STORAGE_SCAN_FAILED"
		c.snapshot = s
		return s
	}
	s.UsedBytes, s.FreeBytes, s.CapacityBytes, s.Error = used, free, capacity, ""
	if used*100 >= c.cfg.BudgetBytes*80 || free*100 < capacity*20 {
		s.RetentionHours = nextRetention(s.RetentionHours, true)
	} else if used*100 < c.cfg.BudgetBytes*70 && free*100 >= capacity*20 {
		s.RetentionHours = nextRetention(s.RetentionHours, false)
	}
	if err := writeOverride(c.cfg.OverrideDir, s.RetentionHours); err != nil {
		s.Paused = true
		s.Error = "RETENTION_UPDATE_FAILED"
		c.snapshot = s
		return s
	}
	s.Paused = used*100 >= c.cfg.BudgetBytes*90 || free*100 < capacity*10
	c.snapshot = s
	return s
}

func (c *Controller) Run(ctx context.Context) {
	c.Check()
	ticker := time.NewTicker(c.cfg.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.Check()
		}
	}
}

func (c *Controller) Snapshot() Snapshot {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.snapshot
}

func (c *Controller) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/healthz":
		if c.Snapshot().Error != "" {
			http.Error(w, "storage check unavailable", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	case "/metrics":
		s := c.Snapshot()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprintf(w, "rag_observability_tempo_storage_bytes %d\nrag_observability_tempo_budget_bytes %d\nrag_observability_tempo_free_bytes %d\nrag_observability_tempo_retention_hours %d\n", s.UsedBytes, s.BudgetBytes, s.FreeBytes, s.RetentionHours)
		if s.Paused {
			fmt.Fprintln(w, "rag_observability_trace_ingest_paused 1")
		} else {
			fmt.Fprintln(w, "rag_observability_trace_ingest_paused 0")
		}
	case "/v1/traces":
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if c.Snapshot().Paused {
			http.Error(w, "trace storage budget reached", http.StatusTooManyRequests)
			return
		}
		request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, c.cfg.TempoURL+"/v1/traces", http.MaxBytesReader(w, r.Body, 4<<20))
		if err != nil {
			http.Error(w, "trace backend unavailable", http.StatusBadGateway)
			return
		}
		request.Header.Set("Content-Type", r.Header.Get("Content-Type"))
		if encoding := r.Header.Get("Content-Encoding"); encoding != "" {
			request.Header.Set("Content-Encoding", encoding)
		}
		response, err := c.client.Do(request)
		if err != nil {
			log.Printf("trace backend unavailable: %v", err)
			http.Error(w, "trace backend unavailable", http.StatusBadGateway)
			return
		}
		defer response.Body.Close()
		w.Header().Set("Content-Type", response.Header.Get("Content-Type"))
		w.WriteHeader(response.StatusCode)
		_, _ = io.CopyN(w, response.Body, 1<<20)
	default:
		http.NotFound(w, r)
	}
}
