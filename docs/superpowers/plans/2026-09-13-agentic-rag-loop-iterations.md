# Go Agentic RAG Loop 五阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Go `Model → rag_retrieve → Model` 基础循环迭代为可控、可收敛、可评测的 Agentic RAG Loop，使普通交流稳定跳过检索，知识问题经过证据充分性判断，并在证据不足时按缺口重写查询后有限重试。

**Architecture:** Go 继续拥有意图路由、Agent 状态机、Chat Model、Tool 调度、Citation 和 SSE；Python 继续只通过版本化 gRPC 提供 `Retrieve` evidence，不拥有 Agent Loop。主要参考 RAGFlow 的外层 Router、`formalize`、Sufficient Context Agent（SCA）和 Query Rewriter 机制，但只实现当前单知识库、单只读检索工具所需的最小闭环，不复制 RAGFlow 源码、Canvas 或通用工具平台。

**Tech Stack:** Go、OpenAI-compatible Chat Completions、function calling、Gin、gRPC、SSE、Python RAG、Elasticsearch、Go tests、Vue 3 tests。

**Spec:** `SPEC.md` §2.5、§5.6、§5.7、§7.3；`docs/superpowers/plans/2026-09-06-agent-and-rag-development-tasks.md` 阶段 B/C/D；参考 `references/ragflow/rag/advanced_rag/agentic_rag.py` 与 `references/ragflow/rag/advanced_rag/agentic_rag_graph.py`。

## Global Constraints

- Python 的唯一接口仍是版本化 gRPC；不得新增 HTTP/FastAPI adapter，也不得把 Agent Loop、Tool Calling、会话策略或 SSE 下沉到 Python。
- Go 是唯一 Agent 决策者；模型不能指定 `dataset_id`、用户身份、密钥、deadline 或授权范围，这些值必须由服务端绑定。
- `rag_retrieve` 保持只读；不得改变 Python 的 MySQL、Outbox、NATS、索引版本、generation fence 或检索可见性语义。
- 本增量不修改 protobuf；若执行中发现必须修改 `.proto`，停止当前工作包，先更新规格和计划，再同步两端生成代码与契约测试。
- 保留现有 SSE `retrieval/token/final/error` 事件类型；可增加向后兼容字段，但不能让前端依赖模型私有思维链。
- 日志、事件和评测产物不得记录 API Key、完整模型私有推理、未脱敏工具凭据或真实用户文档内容。
- 单次 Run 默认最多 6 次模型调用、3 次检索轮次、2 次查询重写、每轮最多 2 条重写查询、40 条去重 Evidence、128 KiB 工具结果，并受现有上下文预算和请求取消约束。
- 每个工作包执行 TDD：先加入能够稳定复现目标行为的失败测试，再写最小实现，再运行与改动相称的测试。
- 每完成一个可独立验收的工作包，检查 `git status`，只暂存该包文件，并按仓库 Conventional Commits 中文格式立即提交；不得自动 push。
- 若新增、删除或改名根目录 `tests/` 下的测试、fixture、marker 或 Fake port，必须同步更新 `tests/TEST.md`；只修改 `backend/go-api` 内 Go 测试时不伪造该目录更新。

---

## 参考机制与明确取舍

| RAGFlow 机制 | 本项目采用方式 | 本期不采用 |
|---|---|---|
| 外层 Router 决定回答或调用 `rag` | Go 路由结果映射为明确 Tool Policy | Canvas DSL、任意多工具自动规划 |
| 单轮自包含问题保持原文 | 普通知识问题原样进入首次检索 | 无条件 LLM 改写每个问题 |
| 多轮 `formalize` 解决代词和省略 | 保留 `StandaloneQuery`，后续可由受限模型实现替换规则实现 | 把整个历史直接拼进检索词 |
| SCA 判断证据是否充分 | 结构化 `SufficiencyDecision`，只返回缺口，不保存思维链 | 无上限反思、自我讨论 |
| Query Rewriter 针对缺口补检索 | 最多两轮、去重、保留尝试账本 | 开放式 Deep Research、多 Agent fan-out |

## 目标文件结构

```text
backend/go-api/internal/agent/
├─ agent.go                    # Harness 外观、输入校验、最终 Citation 校验
├─ intent.go                   # 现有轻量路由与 standalone query
├─ model.go                    # OpenAI-compatible adapter；按 ToolPolicy 构造请求
├─ tool_policy.go              # ToolMode/ToolPolicy 及意图到策略映射
├─ state.go                    # RunPhase、RunState、RunLimits、StopReason
├─ runtime.go                  # 显式状态机执行循环
├─ evidence_pool.go            # 跨轮 Evidence 去重、稳定 Citation 编号与大小限制
├─ sufficiency.go              # SufficiencyAssessor 接口、结构化结果及模型实现
├─ query_rewriter.go           # QueryRewriter 接口、尝试去重及模型实现
├─ observer.go                 # 脱敏 RunEvent 与 Observer 接口
├─ *_test.go                   # 对应单元和 adapter 契约测试
└─ testdata/
   └─ agent_eval.json          # 固定路由、充分性、重写与收敛样本

backend/go-api/internal/httpapi/
├─ chat.go                     # 装配 Harness、run_id、observer，保持 SSE 契约
└─ integration_test.go         # HTTP/SSE 全链路回归
```

---

## 迭代 1：修复工具路由，让意图结果真正控制检索

### 工作包 I1-1：引入显式 Tool Policy

**Files:**
- Create: `backend/go-api/internal/agent/tool_policy.go`
- Modify: `backend/go-api/internal/agent/agent.go`
- Modify: `backend/go-api/internal/agent/model.go`
- Modify: `backend/go-api/internal/agent/agent_test.go`
- Modify: `backend/go-api/internal/agent/model_test.go`
- Modify: `SPEC.md`

**Interfaces:**
- Produces: `type ToolMode string`，值为 `none`、`auto`、`required`。
- Produces: `type ToolPolicy struct { Mode ToolMode; RequiredName string }`。
- Changes: `Model.Complete(context.Context, []Message, ToolPolicy) (Message, error)`。
- Changes: `StreamingModel.Stream(context.Context, []Message, ToolPolicy, func(string) error, func(ToolCall) error) error`。
- Produces: `func PolicyForIntent(IntentResult, int) ToolPolicy`；参数二是零基 round。

- [ ] **Step 1: 写失败测试，复现“你好仍被强制检索”**

  在 `model_test.go` 使用 `httptest.Server` 捕获请求 JSON，分别断言：

  ```go
  ToolPolicy{Mode: ToolNone}     // payload 不包含 tools/tool_choice
  ToolPolicy{Mode: ToolAuto}     // tools 存在，tool_choice == "auto"
  ToolPolicy{Mode: ToolRequired, RequiredName: "rag_retrieve"}
  // tools 存在，tool_choice 指向 rag_retrieve
  ```

  在 `agent_test.go` 增加 `TestHarnessOrdinaryConversationWithOpenAIAdapterSkipsRetrieval`，通过 `httptest.Server` 驱动真实 `OpenAI` adapter；输入 `你好`、`您好`、`谢谢`，断言 Retriever 调用次数为 0。该测试必须能在旧实现上因请求含强制 tool choice 而失败，不能只依赖现有会忽略 `force` 参数的 Fake Model。

- [ ] **Step 2: 运行定向测试并确认失败原因**

  Run:

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestOpenAIToolPolicy|TestHarnessOrdinaryConversationWithOpenAIAdapterSkipsRetrieval' -count=1 -v
  ```

  Expected: FAIL，显示 `ToolNone` 请求仍携带 `rag_retrieve`，或普通交流触发 Retriever。

- [ ] **Step 3: 实现 Tool Policy 和 provider payload 映射**

  `ToolNone` 必须完全省略 `tools` 与 `tool_choice`；`ToolAuto` 添加 `rag_retrieve` schema 并设置 `auto`；`ToolRequired` 添加 schema 并指定函数名。保留 DeepSeek thinking 的兼容策略，但 `ToolNone` 在任何供应商下都不得暴露工具。

- [ ] **Step 4: 将意图映射到每轮策略**

  规则固定为：

  ```text
  reply / reuse  -> ToolNone（所有轮次）
  retrieve       -> round 0 为 ToolRequired(rag_retrieve)，后续为 ToolAuto
  clarify        -> 不调用模型，不构造 ToolPolicy
  非法 action    -> 保守返回错误，不默认直接回答
  ```

  对 `reply/reuse` 若模型仍返回 ToolCall，Harness 必须拒绝且 Retriever 调用次数保持 0。

- [ ] **Step 5: 更新规格中的职责表述**

  在 `SPEC.md` 明确“Python 不实现 Agent Loop；产品 Go 已实现并负责 Agent Loop”，并写明路由结果必须控制工具暴露和强制策略，避免“识别为普通交流但仍强制检索”。

- [ ] **Step 6: 运行 Agent 与 HTTP 测试**

  Run:

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go && go test ./internal/agent ./internal/httpapi -count=1
  ```

  Expected: PASS。

- [ ] **Step 7: 提交 I1-1**

  ```bash
  git status --short
  git add SPEC.md backend/go-api/internal/agent/tool_policy.go backend/go-api/internal/agent/agent.go backend/go-api/internal/agent/model.go backend/go-api/internal/agent/agent_test.go backend/go-api/internal/agent/model_test.go
  git commit -m "fix(agent): 让意图路由控制检索工具策略"
  ```

### 工作包 I1-2：补齐轻量路由边界

**Files:**
- Modify: `backend/go-api/internal/agent/intent.go`
- Modify: `backend/go-api/internal/agent/agent_test.go`

**Interfaces:**
- Keeps: `func RouteIntent(question string, history []Message) IntentResult`。
- Produces: 对明显问候、致谢、告别做规范化匹配；混合事实问题仍返回 `retrieve`。

- [ ] **Step 1: 写表驱动失败测试**

  覆盖 `你好啊`、`你好！`、`谢谢你`、`再见`、`你好，文档里怎么配置超时？`、`谢谢，另外 timeout 最大是多少？`。前四项必须 `reply`，后两项必须 `retrieve`。

- [ ] **Step 2: 运行测试确认边界失败**

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestRouteIntent' -count=1 -v
  ```

- [ ] **Step 3: 实现最小规范化规则**

  去除首尾空白和常见末尾语气词/标点后匹配有限 allowlist；只要同一消息包含知识问句或新增事实标记，就不得因问候前缀跳过检索。不要在此工作包引入新的分类模型。

- [ ] **Step 4: 运行测试并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/intent.go internal/agent/agent_test.go && go test ./internal/agent -count=1
  git status --short
  git add backend/go-api/internal/agent/intent.go backend/go-api/internal/agent/agent_test.go
  git commit -m "fix(agent): 补齐普通交流与混合问题路由边界"
  ```

---

## 迭代 2：把隐式 for-loop 重构为显式状态机

### 工作包 I2-1：定义 Run 状态、限制和终止原因

**Files:**
- Create: `backend/go-api/internal/agent/state.go`
- Create: `backend/go-api/internal/agent/state_test.go`
- Modify: `backend/go-api/internal/agent/agent.go`
- Modify: `backend/go-api/internal/agent/context_budget.go`
- Modify: `SPEC.md`

**Interfaces:**
- Produces: `RunPhaseRoute|RunPhaseModel|RunPhaseTool|RunPhaseAssess|RunPhaseRewrite|RunPhaseFinalize|RunPhaseDone|RunPhaseFailed`。
- Produces: `RunLimits{MaxModelCalls, MaxRetrievalRounds, MaxRewriteRounds, MaxToolCallsPerRound, MaxEvidence, MaxToolOutputBytes int}`。
- Produces: `StopReasonCompleted|DirectReply|Clarification|EvidenceSufficient|EvidenceInsufficient|BudgetExceeded|Cancelled|ProviderError|InvalidToolCall`。
- Produces: `RunState` 保存 phase、messages、intent、model/retrieval/rewrite 计数、attempted queries、EvidencePool 和 stop reason。

- [ ] **Step 1: 写状态转换失败测试**

  测试合法主路径：

  ```text
  Route -> Model -> Tool -> Assess -> Finalize -> Done
  Route -> Model -> Done                  # 普通交流
  Route -> Done                           # 澄清
  Assess -> Rewrite -> Tool -> Assess     # 证据不足
  ```

  并断言非法转换如 `Done -> Tool`、超出检索轮次、取消后继续执行均返回稳定错误。

- [ ] **Step 2: 运行测试确认新类型尚不存在**

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestRunState' -count=1 -v
  ```

- [ ] **Step 3: 实现纯状态类型和转换函数**

  状态文件不得调用 HTTP、gRPC 或模型 SDK。所有计数在进入动作前检查，动作成功后递增；取消、预算不足和不可恢复错误进入终态，不允许重新打开。

- [ ] **Step 4: 将默认限制集中到 `RunLimits`**

  保留现有对外行为：模型调用最多 6 次、每轮工具调用最多 4 次、Evidence 最多 40 条、工具结果最多 128 KiB；新增检索最多 3 轮、改写最多 2 轮。`ContextBudget` 继续独立负责消息 token 预算。

- [ ] **Step 5: 更新 SPEC 状态图和停止条件**

  在 Go Agent 章节记录状态、上限、取消传播和终态不可重开；不得修改 Python Job/Task 状态机定义。

- [ ] **Step 6: 运行并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go && go test ./internal/agent -count=1
  git status --short
  git add SPEC.md backend/go-api/internal/agent/state.go backend/go-api/internal/agent/state_test.go backend/go-api/internal/agent/agent.go backend/go-api/internal/agent/context_budget.go
  git commit -m "refactor(agent): 定义可收敛的运行状态机"
  ```

### 工作包 I2-2：迁移 Harness 到状态机执行器

**Files:**
- Create: `backend/go-api/internal/agent/runtime.go`
- Create: `backend/go-api/internal/agent/runtime_test.go`
- Create: `backend/go-api/internal/agent/evidence_pool.go`
- Create: `backend/go-api/internal/agent/evidence_pool_test.go`
- Modify: `backend/go-api/internal/agent/agent.go`
- Modify: `backend/go-api/internal/agent/context_budget_test.go`

**Interfaces:**
- Produces: `func (h Harness) runStateMachine(context.Context, *RunState, Emit) error`。
- Produces: `EvidencePool.Add([]Evidence) ([]Citation, error)`，按 `document_id/index_version/chunk_id` 稳定去重。
- Keeps: `Harness.Run(...) (string, []Citation, error)` 对 HTTP 调用方签名不变。

- [ ] **Step 1: 写失败测试覆盖每个终止路径**

  包括正常检索回答、直接回答、澄清、未知工具、参数非法、模型超限、上下文超限、客户端取消、重复 Evidence 和无效 Citation。测试同时断言模型、Retriever 调用次数和最终 `StopReason`。

- [ ] **Step 2: 运行失败测试**

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestRuntime|TestEvidencePool' -count=1 -v
  ```

- [ ] **Step 3: 抽取 EvidencePool**

  Citation 编号只在首次加入唯一 Evidence 时分配，重复检索不得改变已有编号；达到 40 条或序列化结果超过 128 KiB 时返回明确错误。

- [ ] **Step 4: 迁移循环并保持现有外部契约**

  `agent.go` 只负责构造初始 `RunState`、调用执行器和校验最终引用；`runtime.go` 按 phase 调度模型和工具。每次模型调用前继续执行 `TrimMessages` + `Fits`，tool call/result 必须成对保留。

- [ ] **Step 5: 运行完整 Go 测试并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go && go test ./... -count=1
  git status --short
  git add backend/go-api/internal/agent/agent.go backend/go-api/internal/agent/runtime.go backend/go-api/internal/agent/runtime_test.go backend/go-api/internal/agent/evidence_pool.go backend/go-api/internal/agent/evidence_pool_test.go backend/go-api/internal/agent/context_budget_test.go
  git commit -m "refactor(agent): 使用显式状态机执行工具循环"
  ```

---

## 迭代 3：增加证据充分性判断

### 工作包 I3-1：实现结构化 SufficiencyAssessor

**Files:**
- Create: `backend/go-api/internal/agent/sufficiency.go`
- Create: `backend/go-api/internal/agent/sufficiency_test.go`
- Modify: `backend/go-api/internal/agent/model.go`
- Modify: `backend/go-api/internal/agent/model_test.go`
- Modify: `SPEC.md`

**Interfaces:**
- Produces: `SufficiencyDecision{Sufficient bool, MissingFacts []string, ReasonCode string}`。
- Produces: `SufficiencyAssessor.Assess(context.Context, string, []Citation) (SufficiencyDecision, error)`。
- Produces: `ModelSufficiencyAssessor{Model Model}`，调用模型时使用 `ToolNone`。

- [ ] **Step 1: 写严格 JSON 契约测试**

  合法输出：

  ```json
  {"sufficient":false,"missing_facts":["timeout 的最大允许值"],"reason_code":"missing_parameter_limit"}
  ```

  拒绝 Markdown fence、缺字段、超过 5 个缺口、单个缺口超过 256 字符和未知附加字段。输出不得包含自由推理文本。

- [ ] **Step 2: 写语义测试**

  固定 Fake 覆盖：直接证据充分、跨文档比较缺一项、空召回、证据仅关键词相关但不回答问题、模型返回非法 JSON、超时和取消。

- [ ] **Step 3: 运行测试确认失败**

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestSufficiency' -count=1 -v
  ```

- [ ] **Step 4: 实现最小 assessor**

  Prompt 只要求判断给定 Evidence 是否覆盖问题所需事实，并返回上述 JSON。模型调用使用 `ToolNone`；Evidence 先受现有 ContextBudget 限制。非法输出或 assessor 故障返回分类错误，不自动假定“充分”。

- [ ] **Step 5: 明确降级策略并更新 SPEC**

  Assessor 不可用时停止额外检索，将现有 Evidence 交给 Finalize，并在系统提示中要求明确说明证据不足；不得因为 assessor 失败无限重试，也不得把失败当成“可以无引用回答事实”。

- [ ] **Step 6: 运行并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go && go test ./internal/agent -count=1
  git status --short
  git add SPEC.md backend/go-api/internal/agent/sufficiency.go backend/go-api/internal/agent/sufficiency_test.go backend/go-api/internal/agent/model.go backend/go-api/internal/agent/model_test.go
  git commit -m "feat(agent): 增加结构化证据充分性判断"
  ```

### 工作包 I3-2：把 SCA 接入状态机

**Files:**
- Modify: `backend/go-api/internal/agent/agent.go`
- Modify: `backend/go-api/internal/agent/state.go`
- Modify: `backend/go-api/internal/agent/runtime.go`
- Modify: `backend/go-api/internal/agent/runtime_test.go`
- Modify: `backend/go-api/internal/httpapi/chat.go`
- Modify: `backend/go-api/internal/httpapi/integration_test.go`

**Interfaces:**
- Adds: `Harness.Assessor SufficiencyAssessor`。
- Consumes: `SufficiencyDecision`。
- Produces: 检索后进入 `Assess`；充分则 `Finalize`，不足且仍有额度则预留 `Rewrite`，无额度则带不足约束 Finalize。

- [ ] **Step 1: 写状态机失败测试**

  断言充分证据只检索一次；证据不足不会立即编造答案；Assessor 取消立即终止；Assessor 错误不会启动无界重试；普通交流完全不调用 Assessor。

- [ ] **Step 2: 写 HTTP 集成回归**

  模拟模型和 RAG：知识问题按 `retrieval → token → final` 输出；普通问候只有 `token → final`；无证据回答没有伪造 Citation。

- [ ] **Step 3: 接入 Harness 与 Chat 装配**

  Chat handler 使用同一个受限 OpenAI adapter 构造 `ModelSufficiencyAssessor`；Assessor 调用计入 `MaxModelCalls` 和 ContextBudget，不创建第二套 API Key、HTTP Client 或授权路径。

- [ ] **Step 4: 运行并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go internal/httpapi/*.go && go test ./... -count=1
  git status --short
  git add backend/go-api/internal/agent/agent.go backend/go-api/internal/agent/state.go backend/go-api/internal/agent/runtime.go backend/go-api/internal/agent/runtime_test.go backend/go-api/internal/httpapi/chat.go backend/go-api/internal/httpapi/integration_test.go
  git commit -m "feat(agent): 将证据充分性判断接入运行循环"
  ```

---

## 迭代 4：按证据缺口重写查询并有限补检索

### 工作包 I4-1：实现 QueryRewriter 与尝试账本

**Files:**
- Create: `backend/go-api/internal/agent/query_rewriter.go`
- Create: `backend/go-api/internal/agent/query_rewriter_test.go`
- Modify: `backend/go-api/internal/agent/state.go`
- Modify: `SPEC.md`

**Interfaces:**
- Produces: `RewriteRequest{OriginalQuestion string, StandaloneQuestion string, MissingFacts []string, AttemptedQueries []string}`。
- Produces: `RewriteResult{Queries []string}`。
- Produces: `QueryRewriter.Rewrite(context.Context, RewriteRequest) (RewriteResult, error)`。
- Produces: `ModelQueryRewriter{Model Model}`，模型调用始终使用 `ToolNone`。
- Produces: `NormalizeAttemptedQuery(string) string`，仅用于稳定去重，不改变实际发送文本。

- [ ] **Step 1: 写失败测试**

  覆盖：单缺口生成一条查询、比较问题保留两个主体、多缺口最多两条、不得返回空查询、不得重复已尝试查询、不得加入问题中不存在的新实体、非法 JSON、超时与取消。

- [ ] **Step 2: 运行测试确认失败**

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestQueryRewriter|TestAttemptedQuery' -count=1 -v
  ```

- [ ] **Step 3: 实现严格重写契约**

  模型只返回：

  ```json
  {"queries":["timeout 参数允许的最大值和单位"]}
  ```

  每轮最多 2 条，每条 1–4096 字符；去重后没有新查询时返回 `no_new_query`，由状态机收敛而不是继续请求模型。

- [ ] **Step 4: 记录 attempted query ledger**

  账本必须包含首次 `StandaloneQuery` 和全部后续查询；日志只记录 hash/长度，内存状态可保留原文用于本次 Run。重复查询不调用 Retriever，不消耗检索轮次。

- [ ] **Step 5: 更新 SPEC 并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go && go test ./internal/agent -count=1
  git status --short
  git add SPEC.md backend/go-api/internal/agent/query_rewriter.go backend/go-api/internal/agent/query_rewriter_test.go backend/go-api/internal/agent/state.go
  git commit -m "feat(agent): 增加缺口驱动的查询重写"
  ```

### 工作包 I4-2：完成 SCA 与 Query Rewriter 闭环

**Files:**
- Modify: `backend/go-api/internal/agent/agent.go`
- Modify: `backend/go-api/internal/agent/runtime.go`
- Modify: `backend/go-api/internal/agent/runtime_test.go`
- Modify: `backend/go-api/internal/agent/context_budget_test.go`
- Modify: `backend/go-api/internal/httpapi/chat.go`
- Modify: `backend/go-api/internal/httpapi/integration_test.go`

**Interfaces:**
- Adds: `Harness.Rewriter QueryRewriter`。
- Consumes: I3 的 `MissingFacts` 和 I4-1 的 `RewriteResult`。
- Produces: `Assess(insufficient) -> Rewrite -> Tool -> Assess` 的有界闭环。

- [ ] **Step 1: 写完整闭环失败测试**

  场景至少包括：首次不足、第二次充分；两次都不足后收尾；重写重复原查询时立即收尾；第二轮出现重复 Evidence 时 Citation 编号稳定；取消发生在重写或第二次 gRPC 检索时立即终止。

- [ ] **Step 2: 写预算测试**

  断言每次 Assess、Rewrite、Finalize 都计入 `MaxModelCalls`；每个新查询计入检索轮次；达到任何上限后不再调用下游。当前 interaction、tool call/result 配对和最新 Evidence 不得被错误拆分。

- [ ] **Step 3: 实现闭环**

  每次重写查询仍通过同一个服务端绑定的 `Retriever` 调 Python gRPC；不得让模型提供 dataset。多条查询顺序执行以保持稳定事件顺序，任一不可恢复授权/取消错误立即终止；可恢复的空结果进入下一次 Assess。

- [ ] **Step 4: 保持 SSE 向后兼容**

  每次实际检索继续发送 `retrieval`，增加可选 `round` 和 `reason` 字段；`hits` 结构保持不变。不得把 MissingFacts 的自由文本直接暴露为前端推理链。

- [ ] **Step 5: 运行全量 Go 与前端 SSE 测试**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go internal/httpapi/*.go && go test ./... -count=1
  cd apps/web && npm test -- --run tests/live-chat-stream.spec.ts tests/mock-chat-stream.spec.ts
  ```

  Expected: PASS。

- [ ] **Step 6: 提交 I4-2**

  ```bash
  git status --short
  git add backend/go-api/internal/agent/agent.go backend/go-api/internal/agent/runtime.go backend/go-api/internal/agent/runtime_test.go backend/go-api/internal/agent/context_budget_test.go backend/go-api/internal/httpapi/chat.go backend/go-api/internal/httpapi/integration_test.go apps/web/tests/live-chat-stream.spec.ts apps/web/tests/mock-chat-stream.spec.ts
  git commit -m "feat(agent): 完成证据缺口驱动的有限检索循环"
  ```

---

## 迭代 5：建立 Agent 评测和生产可观测性

### 工作包 I5-1：固定 Agent 行为评测集

**Files:**
- Create: `backend/go-api/internal/agent/testdata/agent_eval.json`
- Create: `backend/go-api/internal/agent/eval_test.go`
- Modify: `docs/test/testing-guide.md`

**Interfaces:**
- Produces: 固定 fixture 字段 `id/question/history/expected_action/required_queries/max_retrieval_rounds/expected_stop_reason`。
- Produces: 测试内 `EvalMetrics{RouteAccuracy, RequiredRetrievalRecall, NoRetrievalPrecision, LoopConvergence, CitationValidity float64}`。

- [ ] **Step 1: 创建固定评测样本**

  至少 30 条，覆盖普通交流、知识问题、问候混合事实问题、多轮指代、已有答案加工、新事实混合加工、无答案、跨文档比较、重复改写、恶意“不要检索”指令和取消。Fixture 不包含真实用户数据。

- [ ] **Step 2: 写确定性评测测试**

  使用脚本化 Model、Assessor、Rewriter 和 Retriever；门槛固定为：应检索问题召回率 100%，明确普通交流不检索精确率 100%，所有样本在限制内收敛，Citation 编号有效率 100%。不得 snapshot LLM 自由文本。

- [ ] **Step 3: 运行评测并修正实现缺陷**

  ```bash
  cd backend/go-api && go test ./internal/agent -run TestAgentEvalFixture -count=1 -v
  ```

  若失败，只修正导致 fixture 行为不符合本计划的最小逻辑；不得为了通过测试降低门槛或删除样本。

- [ ] **Step 4: 记录运行入口并提交**

  ```bash
  git status --short
  git add backend/go-api/internal/agent/testdata/agent_eval.json backend/go-api/internal/agent/eval_test.go docs/test/testing-guide.md
  git commit -m "test(agent): 建立路由与检索循环评测基线"
  ```

### 工作包 I5-2：增加脱敏 Run Observer

**Files:**
- Create: `backend/go-api/internal/agent/observer.go`
- Create: `backend/go-api/internal/agent/observer_test.go`
- Modify: `backend/go-api/internal/agent/runtime.go`
- Modify: `backend/go-api/internal/httpapi/chat.go`
- Modify: `backend/go-api/internal/httpapi/integration_test.go`
- Modify: `SPEC.md`

**Interfaces:**
- Produces: `RunEvent{RunID, Stage, Round, Action, QueryHash, EvidenceCount, ModelCalls, RetrievalCalls, RewriteCalls, DurationMS, ErrorCode, StopReason}`。
- Produces: `Observer.Observe(context.Context, RunEvent)`；观测失败不得改变 Agent 业务结果。
- Adds: `Harness.Observer Observer` 与 `Harness.RunID string`。

- [ ] **Step 1: 写事件序列失败测试**

  成功路径必须按顺序出现 `route/model/tool/assess/finalize/complete`；重写路径包含 `rewrite`；取消和失败只产生一个终态事件。断言事件不含 question、Evidence content、API Key、ReasoningContent 或工具原始参数。

- [ ] **Step 2: 运行测试确认失败**

  ```bash
  cd backend/go-api && go test ./internal/agent -run 'TestObserver|TestRunEvents' -count=1 -v
  ```

- [ ] **Step 3: 实现 Observer 并接入 HTTP 日志**

  `chat.go` 为每次请求生成 run ID，并使用现有日志设施输出 JSON；查询只记录 SHA-256 前 16 个十六进制字符和长度。Observer panic/错误必须被隔离，不能中断回答。

- [ ] **Step 4: 更新 SPEC 可观测字段**

  明确 stage、计数、耗时、错误码和 stop reason；明确不记录私有思维链与正文。

- [ ] **Step 5: 运行并提交**

  ```bash
  cd backend/go-api && gofmt -w internal/agent/*.go internal/httpapi/*.go && go test ./... -count=1
  git status --short
  git add SPEC.md backend/go-api/internal/agent/observer.go backend/go-api/internal/agent/observer_test.go backend/go-api/internal/agent/runtime.go backend/go-api/internal/httpapi/chat.go backend/go-api/internal/httpapi/integration_test.go
  git commit -m "feat(agent): 增加检索循环运行观测事件"
  ```

### 工作包 I5-3：最终门禁与本地产品验收

**Files:**
- Modify only if verification reveals a scoped defect: files owned by the failing work package.
- Update when behavior or commands changed: `docs/development/live-product-plane.md`

**Interfaces:**
- Consumes: I1–I5 全部产物。
- Produces: 可复现验证记录，不创建新的产品接口。

- [ ] **Step 1: 运行 Go 全量检查**

  ```bash
  cd backend/go-api && gofmt -l $(git ls-files '*.go') && go vet ./... && go test ./... -count=1
  ```

  Expected: `gofmt -l` 无输出，`go vet` 与测试退出码均为 0。

- [ ] **Step 2: 运行前端与仓库离线门禁**

  ```bash
  cd apps/web && npm test -- --run && npm run build
  cd /data/RAG && make ci
  ```

  Expected: 全部退出码为 0。若 Earthly 不可用，记录实际错误，不得声称 `make ci` 通过。

- [ ] **Step 3: 启动本地产品栈验证可观察行为**

  ```bash
  cd /data/RAG && make run
  docker compose -f compose.product.yml ps
  ```

  手工验证：

  ```text
  “你好”                         -> 无 retrieval SSE、无 Citation
  “你好，文档里怎么配置超时？”   -> 至少一次 retrieval SSE、有受支持 Citation
  “那它的最大值呢？”             -> 使用历史形成独立检索问题
  首次证据不足的比较问题          -> 有界二次检索，最终停止
  无答案问题                      -> 明确证据不足，不编造 Citation
  ```

- [ ] **Step 4: 检查提交和未运行项目**

  ```bash
  git status --short
  git log --oneline -12
  ```

  不为“门禁通过”创建空提交。若修复了验收发现的问题，回到所属工作包补测试，并以该工作包 scope 单独提交。

---

## 完成定义

- `reply/reuse/clarify` 路径在 provider 请求层面不暴露 `rag_retrieve`，因此普通交流稳定不检索。
- 知识事实问题无法在零 Evidence 情况下被当作成功的有依据回答。
- Agent Loop 使用显式状态、预算和终止原因，取消及不可恢复错误不会继续调用模型或工具。
- 检索后执行结构化充分性判断；不足时只围绕明确缺口生成新查询，达到上限后可靠收敛。
- 跨轮 Evidence 去重且 Citation 编号稳定；无效引用继续被拒绝。
- 固定评测集覆盖路由、循环、无答案和攻击性输入；生产日志能回答一次 Run 的阶段、次数、耗时和终止原因，但不泄露正文或思维链。
- `SPEC.md`、相关开发/测试文档与最终行为一致。
- 所有实际运行和未运行的验证项均在交接中列明；没有运行的测试不得声称通过。

---

## 给执行 Agent 的指导提示词

```text
你正在 /data/RAG 仓库中迭代 Go Agentic RAG Loop。

必须先完整阅读：
1. /data/RAG/AGENTS.md
2. /data/RAG/SPEC.md
3. /data/RAG/docs/superpowers/plans/2026-09-13-agentic-rag-loop-iterations.md
4. /data/RAG/docs/superpowers/plans/2026-09-06-agent-and-rag-development-tasks.md 的阶段 B/C/D
5. RAGFlow 参考：
   - /data/RAG/references/ragflow/rag/advanced_rag/agentic_rag.py 中 sys_prompt/formalize
   - /data/RAG/references/ragflow/rag/advanced_rag/agentic_rag_graph.py 中 SCA/query_rewrite

使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，严格按计划的 I1-1 → I1-2 → I2-1 → I2-2 → I3-1 → I3-2 → I4-1 → I4-2 → I5-1 → I5-2 → I5-3 顺序执行。每个工作包都必须走 TDD：先写并运行失败测试，确认失败原因与目标行为一致；再实现最小改动；最后运行计划列出的验证。

架构边界不可突破：Agent Loop、意图、Tool Policy、SCA、Query Rewriter、Citation 和 SSE 只在 Go；Python 只通过现有 gRPC Retrieve 返回 evidence。不得新增 Python HTTP/FastAPI，不得让模型控制 dataset_id、用户身份、密钥、授权范围或 deadline，不得改写 Python Job/Task/Outbox/NATS/索引版本语义。本计划不需要修改 protobuf；若判断必须修改，停止并报告规格差异，不要自行扩展范围。

以 RAGFlow 为主要机制参考，但不要复制其源码或引入 Canvas、通用 MCP、多 Agent、Deep Research。采用的核心思想只有：外层 Router 决定是否调用 RAG；单轮问题保持原文；多轮问题 formalize；检索后判断证据充分性；不足时围绕缺口有限重写并再次检索。

特别先修复当前已确认缺陷：RouteIntent("你好") 虽返回 reply，但 Harness 第一轮仍传 force=true，OpenAI adapter 因此强制 rag_retrieve。修复必须发生在 provider 请求层：ToolNone 时完全省略 tools/tool_choice，而不只是事后忽略 ToolCall。

保护用户现有改动。开始和每次提交前运行 git status；只暂存当前工作包拥有的文件。每完成一个工作包并通过相称检查后立即按计划执行 Conventional Commit，不积攒多个工作包，不 push。若工作区已有无关改动，不覆盖、不清理、不 reset。

验证要求：至少运行每个工作包列出的 Go 定向测试；阶段完成后运行 gofmt -l、go vet ./...、go test ./...；最终运行前端测试/build 和 make ci。没有运行或因环境失败的检查必须明确报告，禁止声称通过。最终交接列出每个 commit hash、文件范围、实际运行命令、结果和未运行项目。
```
