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

const rewritePrompt = `Generate at most 2 short, complementary retrieval queries that target the listed missing facts while preserving the user's original product, language binding, version, and intent.

- Query 1 should favor exact lexical matching: preserve API names, type names, QoS policy names, enum values, error codes, log fragments, commands, paths, configuration keys, and other distinctive terms already present in the input.
- Query 2 should favor semantic retrieval: resolve pronouns and omitted subjects, remove conversational filler, and express the same need as a concise question in the user's language.
- You may introduce a canonical DDS entity that is absent from the literal question only when it is a controlled normalization of wording already present: DP or 域参与者 -> DomainParticipant; DPF or 域参与者工厂 -> DomainParticipantFactory; DW, 数据写入, 写数据, 写入端, or 发布端 -> DataWriter; DR, 数据读取, 读数据, 读取端, 订阅端, or 接收端 -> DataReader; Pub or 发布者 -> Publisher; Sub or 订阅者 -> Subscriber; CFT or 内容过滤主题 -> ContentFilteredTopic; 服务质量, 可靠传输, or 可靠性策略 -> QoS or Quality of Service; 等待集 -> WaitSet; 等待对端确认 -> wait_for_acknowledgments.
- Do not introduce any other product name, version, API, type, enum, error code, command, path, or identifier that is absent from the original or standalone question. Missing-fact text does not authorize a new product or identifier.
- Keep the original product name exactly as written. Never replace it with a guessed vendor, edition, language binding, or version.
- Do not repeat an already attempted query. Return an empty array when no genuinely new, authorized query is needed.

Reply with exactly one JSON object and no other text: {"queries": ["..."]}. Each query must be 1-4096 characters. Never include reasoning, explanations, or markdown fences.`

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

	msg, err := r.Model.Complete(ctx, messages, DeterministicToolNone())
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

	approvedEntityTokens := approvedRewriteEntityTokens(request)
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
		if len(unapprovedIntroducedEntities(candidate, approvedEntityTokens)) > 0 {
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

var asciiTokenPattern = regexp.MustCompile(`[A-Za-z][A-Za-z0-9_.-]+`)

type controlledDDSEntity struct {
	canonical       string
	aliases         []string
	chineseTriggers []string
}

var controlledDDSEntities = []controlledDDSEntity{
	{canonical: "DomainParticipant", aliases: []string{"dp", "domainparticipant"}, chineseTriggers: []string{"域参与者"}},
	{canonical: "DomainParticipantFactory", aliases: []string{"dpf", "domainparticipantfactory"}, chineseTriggers: []string{"域参与者工厂"}},
	{canonical: "DataWriter", aliases: []string{"dw", "datawriter"}, chineseTriggers: []string{"数据写入", "写数据", "写入端", "发布端", "发布数据"}},
	{canonical: "DataReader", aliases: []string{"dr", "datareader"}, chineseTriggers: []string{"数据读取", "读数据", "读取端", "订阅端", "接收端", "接收数据"}},
	{canonical: "Publisher", aliases: []string{"pub", "publisher"}, chineseTriggers: []string{"发布者"}},
	{canonical: "Subscriber", aliases: []string{"sub", "subscriber"}, chineseTriggers: []string{"订阅者"}},
	{canonical: "ContentFilteredTopic", aliases: []string{"cft", "contentfilteredtopic"}, chineseTriggers: []string{"内容过滤主题"}},
	{canonical: "QoS Quality of Service", aliases: []string{"qos"}, chineseTriggers: []string{"服务质量", "可靠传输", "可靠性策略"}},
	{canonical: "WaitSet", aliases: []string{"waitset"}, chineseTriggers: []string{"等待集"}},
	{canonical: "wait_for_acknowledgments", chineseTriggers: []string{"等待对端确认", "等待确认", "确认等待"}},
}

// approvedRewriteEntityTokens 对每个查询中的 ASCII 实体逐项核对。
// 原问题、独立问题和已尝试查询中的实体可继续使用；新增实体只允许来自受控 DDS
// 归一化表。SCA 生成的 MissingFacts 不参与授权，避免它意外扩展产品名或 API。
func approvedRewriteEntityTokens(request RewriteRequest) map[string]bool {
	parts := []string{request.OriginalQuestion, request.StandaloneQuestion}
	parts = append(parts, request.AttemptedQueries...)
	source := strings.Join(parts, " ")
	sourceLower := strings.ToLower(source)
	sourceTokens := asciiTokenSet(source)
	approved := make(map[string]bool, len(sourceTokens)+8)
	for token := range sourceTokens {
		approved[token] = true
	}
	// DDS 是当前知识域而不是产品名；允许检索查询补充该通用领域限定词。
	approved["dds"] = true

	for _, entity := range controlledDDSEntities {
		if !controlledEntityTriggered(entity, sourceLower, sourceTokens) {
			continue
		}
		for token := range asciiTokenSet(entity.canonical) {
			approved[token] = true
		}
	}
	return approved
}

func controlledEntityTriggered(entity controlledDDSEntity, sourceLower string, sourceTokens map[string]bool) bool {
	for _, alias := range entity.aliases {
		if sourceTokens[strings.ToLower(alias)] {
			return true
		}
	}
	for _, trigger := range entity.chineseTriggers {
		if strings.Contains(sourceLower, strings.ToLower(trigger)) {
			return true
		}
	}
	return false
}

func asciiTokenSet(text string) map[string]bool {
	result := map[string]bool{}
	for _, token := range asciiTokenPattern.FindAllString(text, -1) {
		result[strings.ToLower(token)] = true
	}
	return result
}

// unapprovedIntroducedEntities 返回所有未经授权的新 ASCII 实体；调用方只要发现
// 任一项就拒绝整次改写，中文自然语言本身不在这里做不可靠的自动分词。
func unapprovedIntroducedEntities(query string, approved map[string]bool) []string {
	var result []string
	for _, token := range asciiTokenPattern.FindAllString(query, -1) {
		if !approved[strings.ToLower(token)] {
			result = append(result, token)
		}
	}
	return result
}
