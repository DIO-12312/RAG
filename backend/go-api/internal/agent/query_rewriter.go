package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
)

const (
	maxRewriteQueries     = 2
	maxRewriteQueryLength = 4096
)

// ErrNoNewQuery 表示重写没有给出未尝试过的新查询；状态机必须据此收敛，
// 而不是继续请求模型。
var ErrNoNewQuery = errors.New("no new query")

// ErrRewriteUnavailable 表示重写器不可用或输出非法；调用方必须降级收敛。
var ErrRewriteUnavailable = errors.New("query rewriter unavailable")

// RewriteRequest 是一次补检索重写的输入；AttemptedQueries 是本 Run 的尝试账本。
type RewriteRequest struct {
	OriginalQuestion   string
	StandaloneQuestion string
	MissingFacts       []string
	AttemptedQueries   []string
}

// RewriteResult 只包含去重后的新查询。
type RewriteResult struct {
	Queries []string
}

// QueryRewriter 依据缺口生成新的检索查询。
type QueryRewriter interface {
	Rewrite(context.Context, RewriteRequest) (RewriteResult, error)
}

// ModelQueryRewriter 用受限模型实现重写；模型调用始终使用 ToolNone。
type ModelQueryRewriter struct {
	Model  Model
	Budget *ContextBudget
}

const rewritePrompt = `Rewrite the user's question into at most 2 short retrieval queries that close the listed missing facts. ` +
	`Reply with exactly one JSON object and no other text: {"queries": ["..."]}. ` +
	`Do not repeat any already attempted query, do not introduce entities that are absent from the question, ` +
	`each query must be 1-4096 characters, and return an empty array when no new query is needed. ` +
	`Never include reasoning, explanations or markdown fences.`

// NormalizeAttemptedQuery 只用于稳定去重：忽略大小写与空白差异，不改变实际发送文本。
func NormalizeAttemptedQuery(query string) string {
	return strings.ToLower(strings.Join(strings.Fields(query), " "))
}

// Rewrite 返回去重后的新查询；没有新查询时返回 ErrNoNewQuery。
func (r ModelQueryRewriter) Rewrite(ctx context.Context, request RewriteRequest) (RewriteResult, error) {
	if r.Model == nil {
		return RewriteResult{}, fmt.Errorf("%w: model is not configured", ErrRewriteUnavailable)
	}
	if err := ctx.Err(); err != nil {
		return RewriteResult{}, err
	}

	messages := []Message{
		{Role: "system", Content: rewritePrompt},
		{Role: "user", Content: rewriteInput(request)},
	}
	if r.Budget != nil {
		messages = r.Budget.TrimMessages(messages)
		if !r.Budget.Fits(messages) {
			return RewriteResult{}, fmt.Errorf("%w: rewrite input does not fit the context budget", ErrRewriteUnavailable)
		}
	}

	msg, err := r.Model.Complete(ctx, messages, ToolPolicy{Mode: ToolNone})
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return RewriteResult{}, ctxErr
		}
		return RewriteResult{}, fmt.Errorf("%w: %v", ErrRewriteUnavailable, err)
	}

	return parseRewriteResult(msg.Content, request)
}

// rewriteInput 渲染重写输入；账本原文只在本次 Run 的内存与请求中出现。
func rewriteInput(request RewriteRequest) string {
	var builder strings.Builder
	fmt.Fprintf(&builder, "Original question:\n%s\n\nStandalone question:\n%s\n", request.OriginalQuestion, request.StandaloneQuestion)

	builder.WriteString("\nMissing facts:\n")
	if len(request.MissingFacts) == 0 {
		builder.WriteString("(none)\n")
	}
	for _, fact := range request.MissingFacts {
		fmt.Fprintf(&builder, "- %s\n", fact)
	}

	builder.WriteString("\nAlready attempted queries:\n")
	if len(request.AttemptedQueries) == 0 {
		builder.WriteString("(none)\n")
	}
	for _, query := range request.AttemptedQueries {
		fmt.Fprintf(&builder, "- %s\n", query)
	}

	return builder.String()
}

// parseRewriteResult 执行严格 JSON 契约、去重与最小实体校验。
func parseRewriteResult(content string, request RewriteRequest) (RewriteResult, error) {
	trimmed := strings.TrimSpace(content)
	if !strings.HasPrefix(trimmed, "{") || !strings.HasSuffix(trimmed, "}") {
		return RewriteResult{}, fmt.Errorf("%w: rewrite must be a single JSON object", ErrRewriteUnavailable)
	}

	var wire struct {
		Queries *[]string `json:"queries"`
	}
	decoder := json.NewDecoder(strings.NewReader(trimmed))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&wire); err != nil {
		return RewriteResult{}, fmt.Errorf("%w: invalid rewrite json", ErrRewriteUnavailable)
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return RewriteResult{}, fmt.Errorf("%w: trailing content after rewrite", ErrRewriteUnavailable)
	}
	if wire.Queries == nil {
		return RewriteResult{}, fmt.Errorf("%w: rewrite is missing queries", ErrRewriteUnavailable)
	}
	queries := *wire.Queries
	if len(queries) > maxRewriteQueries {
		return RewriteResult{}, fmt.Errorf("%w: too many rewritten queries", ErrRewriteUnavailable)
	}

	allowed := rewriteAllowedText(request)
	attempted := map[string]bool{}
	for _, query := range request.AttemptedQueries {
		attempted[NormalizeAttemptedQuery(query)] = true
	}

	result := RewriteResult{}
	seen := map[string]bool{}
	for _, query := range queries {
		candidate := strings.TrimSpace(query)
		if candidate == "" {
			return RewriteResult{}, fmt.Errorf("%w: empty rewritten query", ErrRewriteUnavailable)
		}
		if len([]rune(candidate)) > maxRewriteQueryLength {
			return RewriteResult{}, fmt.Errorf("%w: rewritten query too long", ErrRewriteUnavailable)
		}
		if introducesNewEntity(candidate, allowed) {
			return RewriteResult{}, fmt.Errorf("%w: rewritten query introduces new entity", ErrRewriteUnavailable)
		}
		normalized := NormalizeAttemptedQuery(candidate)
		if attempted[normalized] || seen[normalized] {
			continue
		}
		seen[normalized] = true
		result.Queries = append(result.Queries, candidate)
	}

	if len(result.Queries) == 0 {
		return RewriteResult{}, ErrNoNewQuery
	}
	return result, nil
}

// rewriteAllowedText 是实体校验的对照文本：问题、独立问题与缺口。
func rewriteAllowedText(request RewriteRequest) string {
	parts := []string{request.OriginalQuestion, request.StandaloneQuestion}
	parts = append(parts, request.MissingFacts...)
	return strings.ToLower(strings.Join(parts, " "))
}

var asciiTokenPattern = regexp.MustCompile(`[A-Za-z][A-Za-z0-9_.-]+`)

// introducesNewEntity 做最小机械校验：重写查询不得引入问题与缺口里没有出现过的
// ASCII 标识符（库名、参数名等）；中文改写不做分词判断。
func introducesNewEntity(query, allowed string) bool {
	for _, token := range asciiTokenPattern.FindAllString(query, -1) {
		if !strings.Contains(allowed, strings.ToLower(token)) {
			return true
		}
	}
	return false
}
