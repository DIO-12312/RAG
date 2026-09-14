package agent

import (
	"strings"
	"testing"
)

func TestEvidencePoolDeduplicatesAndKeepsOrdinals(t *testing.T) {
	pool := NewEvidencePool(DefaultRunLimits())

	first, err := pool.Add([]Evidence{
		{DocumentID: "d1", IndexVersion: 1, ChunkID: "c1", Content: "first"},
		{DocumentID: "d1", IndexVersion: 1, ChunkID: "c2", Content: "second"},
	})
	if err != nil {
		t.Fatalf("add failed: %v", err)
	}
	if len(first) != 2 || first[0].Ordinal != 1 || first[1].Ordinal != 2 {
		t.Fatalf("unexpected ordinals: %+v", first)
	}

	repeat, err := pool.Add([]Evidence{{DocumentID: "d1", IndexVersion: 1, ChunkID: "c1", Content: "first"}})
	if err != nil {
		t.Fatalf("duplicate add failed: %v", err)
	}
	if len(repeat) != 1 || repeat[0].Ordinal != 1 {
		t.Fatalf("duplicate evidence must keep its ordinal: %+v", repeat)
	}
	if pool.Len() != 2 {
		t.Fatalf("duplicate evidence must not grow the pool: %d", pool.Len())
	}

	versioned, err := pool.Add([]Evidence{{DocumentID: "d1", IndexVersion: 2, ChunkID: "c1", Content: "first-v2"}})
	if err != nil {
		t.Fatalf("versioned add failed: %v", err)
	}
	if versioned[0].Ordinal != 3 {
		t.Fatalf("same chunk in another index version must be new evidence: %+v", versioned)
	}
}

func TestEvidencePoolEnforcesBudget(t *testing.T) {
	limits := DefaultRunLimits()
	limits.MaxEvidence = 2
	pool := NewEvidencePool(limits)

	if _, err := pool.Add([]Evidence{
		{DocumentID: "d", IndexVersion: 1, ChunkID: "a"},
		{DocumentID: "d", IndexVersion: 1, ChunkID: "b"},
	}); err != nil {
		t.Fatalf("budget-sized add failed: %v", err)
	}
	if _, err := pool.Add([]Evidence{{DocumentID: "d", IndexVersion: 1, ChunkID: "c"}}); err == nil {
		t.Fatal("evidence over budget must fail")
	}
	if pool.Len() != 2 {
		t.Fatalf("failed add must not mutate the pool: %d", pool.Len())
	}
}

func TestEvidencePoolEncodesToolResultWithinBudget(t *testing.T) {
	pool := NewEvidencePool(DefaultRunLimits())
	citations, err := pool.Add([]Evidence{{DocumentID: "d", IndexVersion: 1, ChunkID: "c", Content: "evidence"}})
	if err != nil {
		t.Fatalf("add failed: %v", err)
	}
	body, err := pool.EncodeResult(citations)
	if err != nil {
		t.Fatalf("encode failed: %v", err)
	}
	if !strings.Contains(string(body), `"ordinal":1`) {
		t.Fatalf("encoded tool result missing citation: %s", body)
	}

	tiny := DefaultRunLimits()
	tiny.MaxToolOutputBytes = 16
	small := NewEvidencePool(tiny)
	smallCitations, err := small.Add([]Evidence{{DocumentID: "d", IndexVersion: 1, ChunkID: "c", Content: strings.Repeat("x", 128)}})
	if err != nil {
		t.Fatalf("add failed: %v", err)
	}
	if _, err := small.EncodeResult(smallCitations); err == nil {
		t.Fatal("tool result over budget must fail")
	}
}
