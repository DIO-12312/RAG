# Ragas 评估与全链路 Trace 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 产品建立可复现的检索门禁、基于 Ragas 的离线回答质量评测，以及不泄露内容的摄取与问答 Trace。

**Architecture:** 确定性指标继续作为 CI 硬门禁；Ragas 只在显式的真实模型评测运行中读取当次 Chat 输出、检索 Evidence 和金标答案，生成可审计的报告。Python 保持 gRPC RAG 计算边界，Go 负责 Chat Run Trace；两端先输出统一的脱敏结构化事件，不新增独立 Dashboard 或 Python HTTP 接口。

**Tech Stack:** Python 3.12、pytest、Ragas 0.4.3、OpenAI-compatible judge model、Go 1.26、slog、structlog、gRPC、Docker Compose。

**Spec:** `SPEC.md`（§1、§2.2、§2.3、§2.5、§3.1、§4 测试与安全边界）。

## Global Constraints

- Python 只提供版本化 gRPC；不得新增 FastAPI/HTTP 调试或评测接口。
- Go 是 Chat Model、Agent、SSE 与所有公网/产品 API 的唯一所有者。
- MySQL Job/Task/Document 是权威业务状态；Trace 不能改变状态机、Outbox 或 ACK/NAK 语义。
- Ragas 调用真实 Judge 会消耗额度，必须使用显式 marker 与环境变量，不能进入 `make ci`。
- 不保存 API Key、完整 Prompt、用户原文、完整 Evidence 或 LLM 自由文本快照；报告只保存哈希、ID、来源定位、分数、耗时和脱敏失败原因。
- 评测样本和每次评测输出分离：金标 fixture 可入库，运行报告必须位于 Git 忽略的 `tests/eval/log/`。
- 新增或变更测试文件、marker、fixture 时同步更新 `tests/TEST.md`。
- 修改 Makefile、Earthfile、Docker Compose 或 CI 时运行 `tests/contract/test_build_entrypoints.py`。
- 每个 Task 先完成实现、配置和文档，再在该 Task 末尾编写或调整测试并运行验证；不安排预期失败的测试运行。

## Review Focus

- 无答案问题必须被评为“正确拒答”，不能因缺少参考答案被当作 Ragas 运行错误；Task 2 覆盖。
- Chunk 重建会改变 `chunk_id`；评测真值必须优先按文档摘要和 locator 匹配；Task 1 覆盖。
- Judge 配置缺失、超时或返回不可解析结果时，评测报告必须标明 `unavailable`，不能伪造零分或通过；Task 3 覆盖。
- Trace 事件不得包含 question、Prompt、Evidence 正文、密钥或模型私有推理；Task 4 和 Task 5 覆盖。
- 摄取失败、NATS redelivery、Chat 取消都必须产生终态 Trace 事件，且不能影响 ACK/NAK、Job 状态或 SSE 语义；Task 4 和 Task 5 分别覆盖。

---

## 交付范围和执行顺序

本计划拆为五个可独立验收的工作包。Task 1--3 构成“离线 Ragas 评测”子项目；Task 4--5 构成“结构化 Trace”子项目。不得以增加 Dashboard 替代 Task 4--5；外部 Trace 平台接入只在本计划全部稳定后另立计划。

### Task 1: 建立版本化 Chat 金标契约与样本校验

**Files:**
- Create: `tests/eval/fixtures/chat_quality.json`
- Create: `tests/eval/chat_quality_contract.py`
- Create: `tests/eval/test_chat_quality_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Consumes: 已有 `tests/eval/fixtures/retrieval_quality.json` 的检索样本和真实 RAG Evidence。
- Produces: `ChatQualityCase`、`ExpectedEvidence`、`load_chat_quality_cases(path: Path) -> tuple[ChatQualityCase, ...]`，供 Task 2 的 Chat 执行器和 Task 3 的 Ragas Runner 使用。

- [ ] **Step 1: 实现不可变样本契约**

```python
@dataclass(frozen=True, slots=True)
class ExpectedEvidence:
    document_sha256: str
    locator: Mapping[str, str | int]

@dataclass(frozen=True, slots=True)
class ChatQualityCase:
    case_id: str
    question: str
    expects_answer: bool
    reference_answer: str
    required_claims: tuple[str, ...]
    evidence: tuple[ExpectedEvidence, ...]
    labels: tuple[str, ...]
```

拒答 Case 设 `expects_answer=false`，其 `reference_answer` 和 `required_claims` 必须为空；可回答 Case 必须至少有一个稳定 evidence locator 与一条参考答案。禁止仅用 `chunk_id` 作为证据真值。

- [ ] **Step 2: 写入首批样本**

从现有固定语料选择至少 30 个可回答问题、5 个无答案/冲突问题；所有来源均为仓库可复现 fixture。每条 Case 写入 `labels`（例如 `pdf`、`chm`、`api`、`procedure`、`no_answer`），供后续切片报告使用。不要在 fixture 保存生产用户问题或完整生产回答。

- [ ] **Step 3: 更新测试目录文档**

更新 `tests/TEST.md` 的 Eval 目录树、职责表和运行边界，明确该 fixture 是 Chat 金标而非 LLM 输出快照。

- [ ] **Step 4: 编写契约测试**

覆盖可回答 Case 必须有参考答案和稳定 locator、拒答 Case 无需参考答案、重复 ID 与非法字段被拒绝、样本数量和标签符合约定。

- [ ] **Step 5: 运行质量契约与既有检索评测**

Run: `uv run pytest tests/eval/test_chat_quality_contract.py tests/eval/test_retrieval_quality.py -v`

Expected: PASS。

- [ ] **Step 6: 检查改动并提交**

```bash
git add tests/eval/fixtures/chat_quality.json tests/eval/chat_quality_contract.py tests/eval/test_chat_quality_contract.py tests/TEST.md
git commit -m "test(eval): 增加回答质量金标契约"
```

### Task 2: 为真实 Chat 运行生成可供评测的脱敏记录

**Files:**
- Create: `backend/go-api/internal/agent/evaluation.go`
- Create: `backend/go-api/internal/agent/evaluation_test.go`
- Modify: `backend/go-api/internal/agent/runtime.go`
- Modify: `backend/go-api/internal/httpapi/chat.go`
- Modify: `backend/go-api/internal/httpapi/integration_test.go`
- Modify: `tests/TEST.md`

**Interfaces:**
- Consumes: `agent.RunState`、`agent.Citation`、`agent.RunEvent` 和 Task 1 的 Case 结构。
- Produces: 仅在显式评测运行中通过本机 Unix domain socket 发送的 `agent.EvaluationPayload`，以及可安全落盘的 `agent.EvaluationRecord`；生产 Chat 响应不包含二者。

- [ ] **Step 1: 定义评测记录**

```go
type EvaluationCitation struct {
    DocumentID string `json:"document_id"`
    IndexVersion int `json:"index_version"`
    ChunkID string `json:"chunk_id"`
    SourceName string `json:"source_name"`
    Locator string `json:"locator"`
    Ordinal int `json:"ordinal"`
}

type EvaluationRecord struct {
    CaseID string `json:"case_id"`
    RunID string `json:"run_id"`
    AnswerSHA256 string `json:"answer_sha256"`
    AnswerLength int `json:"answer_length"`
    Citations []EvaluationCitation `json:"citations"`
    StopReason string `json:"stop_reason"`
    ModelCalls int `json:"model_calls"`
    RetrievalCalls int `json:"retrieval_calls"`
}
```

另定义只在内存和 Unix domain socket 上传输的 `EvaluationPayload { CaseID, RunID, Answer, RetrievedContexts, Citations, StopReason }`。`RetrievedContexts` 是送入 Judge 的实际检索片段，禁止写日志或文件；`EvaluationRecord` 仅保存上述哈希、长度和 locator。评测调用必须同时要求 `RAG_EVAL_CASE_ID` 与权限为 `0600` 的 `RAG_EVAL_COLLECTOR_SOCKET`，缺失时不产生任何评测输出。

- [ ] **Step 2: 将记录限定在评测执行路径**

在 `chat.go` 中仅当 `RAG_EVAL_CASE_ID` 和 `RAG_EVAL_COLLECTOR_SOCKET` 同时存在时，在终态通过 socket 发送一次 `EvaluationPayload`，再调用 `agent.WriteEvaluationRecord` 写脱敏记录。socket 连接、写入或超时失败必须只使该 Case 成为 `unavailable`，不得阻塞 Chat；普通 SSE 与会话持久化路径不得变化，评测完成后仍按现有逻辑写 assistant 消息和 `final` 事件。

- [ ] **Step 3: 更新测试目录文档**

更新 `tests/TEST.md` 的职责表和运行边界，说明评测数据出口只用于显式评测。

- [ ] **Step 4: 编写 Go 单元与 HTTP 集成测试**

验证评测记录不含 Evidence 正文、拒答 Case 的 stop reason 正确；评测请求的 socket 收到最终回答、检索片段、Citation ID/locator 与 stop reason，但磁盘记录只有哈希和 locator。验证 socket 超时不会影响最终 SSE，普通 `/chat/stream` 请求既不创建评测文件，也不在 JSON/SSE 中暴露内部 Trace 或评测数据。

- [ ] **Step 5: 运行 Go 测试，检查改动并提交**

Run: `cd backend/go-api && go test ./internal/agent ./internal/httpapi`

Expected: PASS。

```bash
git add backend/go-api/internal/agent/evaluation.go backend/go-api/internal/agent/evaluation_test.go backend/go-api/internal/agent/runtime.go backend/go-api/internal/httpapi/chat.go backend/go-api/internal/httpapi/integration_test.go tests/TEST.md
git commit -m "feat(agent): 输出受控的评测运行记录"
```

### Task 3: 接入 Ragas 离线 Runner 与报告门禁

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/eval/ragas_runner.py`
- Create: `tests/eval/test_ragas_runner.py`
- Create: `tests/eval/test_real_chat_ragas.py`
- Modify: `tests/eval/conftest.py`
- Modify: `Earthfile`
- Modify: `Makefile`
- Modify: `tests/TEST.md`
- Modify: `docs/testing-guide.md`
- Modify: `SPEC.md`

**Interfaces:**
- Consumes: Task 1 的 `ChatQualityCase`、Task 2 通过本机 Unix domain socket 接收的短生命周期 `EvaluationPayload`，以及显式的 Judge 配置 `RAG_EVAL_JUDGE_BASE_URL`、`RAG_EVAL_JUDGE_API_KEY`、`RAG_EVAL_JUDGE_MODEL`。
- Produces: `RagasCaseResult` 与 Git 忽略的 `tests/eval/log/ragas-<timestamp>.json`；退出码非零仅在评测基础设施不可用或确定性验收失败时产生。

- [ ] **Step 1: 增加独立依赖组和 marker**

在 `pyproject.toml` 增加 `eval-llm = ["ragas==0.4.3"]`，并注册 `ragas: calls a configured judge model for offline RAG quality evaluation` marker；运行 `uv lock` 更新锁文件。Runner 只接受 `RAG_EVAL_JUDGE_*`，不得读取默认生产模型密钥。

- [ ] **Step 2: 实现 Ragas 输入映射和不可用分类**

```python
@dataclass(frozen=True, slots=True)
class RagasCaseResult:
    case_id: str
    context_precision: float | None
    context_recall: float | None
    faithfulness: float | None
    answer_relevancy: float | None
    status: Literal["scored", "unavailable", "invalid_case"]
    reason_code: str | None
```

使用 Ragas 0.4 的 `ragas.metrics.collections` API，逐 Case 调用 `ContextPrecision`、`ContextRecall`、`Faithfulness` 和 `AnswerRelevancy` 的 `ascore`，输入 `user_input`、`response`、`reference` 和 `retrieved_contexts`。Judge 超时、网络失败、非结构化响应或缺少环境变量时返回 `status="unavailable"` 和稳定 reason code；不能写 0.0 伪装为真实分数。

- [ ] **Step 3: 实现确定性伴随断言**

每个可回答 Case 在调用 Judge 前检查：至少一个 Citation 的 document/version/locator 命中允许证据、回答包含所有 `required_claims` 的规范化关键短语、所有 `[n]` 都有实际 Citation。拒答 Case 要求 `StopReasonEvidenceInsufficient` 且不包含 Citation。Ragas 分数作为语义补充，不能覆盖这些确定性失败。

- [ ] **Step 4: 实现真实 Chat 评测支撑能力**

在 `ragas_runner.py` 实现权限 `0600`、单 Case 单连接的 Unix domain socket collector，以及测试用户、真实 Chat API、真实 RAG gRPC 栈与显式 Judge 配置的调用支撑。收到 `EvaluationPayload` 后立即在内存调用 Ragas 并关闭、删除 socket；只将脱敏 report 写入 `tests/eval/log/`，不得把回答或 Evidence 正文写入 Git。后续真实环境测试只由 `make docker-test SUITE=eval RAGAS=1` 调用，默认 `make ci` 与默认 eval 不调用 Judge。

- [ ] **Step 5: 写入报告阈值与报告格式**

首个稳定基线只要求所有 Case 可完成、确定性断言通过、Judge 成功率 100%；记录指标均值、按 `labels` 的分组均值、模型版本、Ragas 版本、耗时和成本摘要。连续三次基线稳定后，另行提出 PR 将各分数阈值写入 fixture；不得在首次接入时猜测质量阈值。

- [ ] **Step 6: 更新构建入口、规格和测试文档**

`Earthfile` 新增仅用于 Docker eval 的 Ragas target；`Makefile` 保持 `make docker-test SUITE=eval` 入口，以 `RAGAS=1` 显式选择。更新 `SPEC.md` 的测试章节，明确 Ragas 属于离线、真实模型、非确定性辅助评测；更新 `tests/TEST.md` 与 `docs/testing-guide.md`。

- [ ] **Step 7: 编写 Runner 与真实 Chat 评测测试**

在 `test_ragas_runner.py` 与 `test_real_chat_ragas.py` 中覆盖：Judge 配置隔离、Ragas 输入映射、拒答与引用的确定性断言、`unavailable` 分类、脱敏报告、socket 传输与资源清理。真实测试使用测试用户、真实 Chat API 和真实 RAG gRPC 栈。更新 `tests/TEST.md`，记录新增测试文件、marker 和真实环境运行边界。

- [ ] **Step 8: 运行验证，检查改动并提交**

Run:

```bash
uv run pytest tests/eval/test_ragas_runner.py tests/eval/test_chat_quality_contract.py -v
uv run pytest tests/contract/test_build_entrypoints.py -v
make docker-test SUITE=eval RAGAS=1
```

Expected: 单元/契约测试 PASS；Docker 运行只在已配置 Judge、真实模型和 Docker 基础设施时 PASS，否则清晰报告配置缺失，不能声称通过。

```bash
git add pyproject.toml uv.lock tests/eval Earthfile Makefile tests/TEST.md docs/testing-guide.md SPEC.md
git commit -m "feat(eval): 接入 Ragas 离线回答评测"
```

### Task 4: 扩展 Python 摄取 Trace schema

**Files:**
- Modify: `src/rag_mvp/observability.py`
- Modify: `src/rag_mvp/ingestion/pipeline.py`
- Modify: `src/rag_mvp/ingestion/worker.py`
- Create: `tests/unit/test_observability_trace.py`
- Modify: `tests/TEST.md`
- Modify: `SPEC.md`

**Interfaces:**
- Consumes: 现有 `emit_event`、Job/Task 关联字段和 pipeline 阶段。
- Produces: 带 `trace_id`、`parent_span_id`、`operation`、`outcome` 和数值属性的 Python 结构化事件；不创建业务表。

- [ ] **Step 1: 增加最小 Trace 事件模型**

```python
def emit_trace(
    operation: str, *, trace_id: str, stage: str, duration_ms: float,
    outcome: str, job_id: str | None = None, task_id: str | None = None,
    document_id: str | None = None, dataset_id: str | None = None,
    attributes: Mapping[str, int | float | str | bool | None] = {},
) -> None: ...
```

禁止 `attributes` 的键为 `content`、`prompt`、`query`、`evidence`、`api_key`、`authorization` 或任何以 `_text` 结尾的字段；违反时抛出 `ValueError` 并由单元测试覆盖。

- [ ] **Step 2: 在摄取阶段发射 Span 摘要**

为 object read、parse、chunk、embedding、index 和 worker complete/failed/cancelled 发射事件。Embedding 记录 `input_chars`、`batch_count`、`request_count`、`retry_count`、`pacing_wait_ms`、`http_429_count`；不记录文本、文件名或 Provider 返回正文。

- [ ] **Step 3: 更新规格和测试目录文档**

在 `SPEC.md` 描述脱敏 Trace 字段与边界；在 `tests/TEST.md` 登记新增单元测试的职责和运行边界。

- [ ] **Step 4: 编写 Trace 单元测试**

覆盖各摄取阶段、限速等待和重试计数、失败与取消终态，以及敏感字段拒绝写入；补充 resilience 场景，检查 Trace 记录异常、redelivery 和强杀恢复不会改变 Worker 的 ACK/NAK、幂等性或 Job 结果。

- [ ] **Step 5: 运行 Python 测试，检查改动并提交**

Run:

```bash
uv run pytest tests/unit/test_observability_trace.py tests/unit/ingestion -v
make docker-test SUITE=resilience
```

Expected: 单元测试和 resilience 测试 PASS；若 Docker 基础设施不可用，记录未运行项，不得声称通过。

```bash
git add src/rag_mvp/observability.py src/rag_mvp/ingestion/pipeline.py src/rag_mvp/ingestion/worker.py tests/unit/test_observability_trace.py tests/TEST.md SPEC.md
git commit -m "feat(ingestion): 输出脱敏摄取 Trace"
```

### Task 5: 扩展 Go Agent Trace 的结构化日志

**Files:**
- Modify: `backend/go-api/internal/agent/observer.go`
- Modify: `backend/go-api/internal/agent/runtime.go`
- Modify: `backend/go-api/internal/httpapi/chat.go`
- Create: `backend/go-api/internal/agent/trace_test.go`
- Modify: `tests/TEST.md`
- Modify: `SPEC.md`

**Interfaces:**
- Consumes: `RunEvent`、`RunState` 和 Python 的相同 `trace_id` 语义。
- Produces: 可按 `trace_id`/`run_id` 检索的脱敏 `agent_run` 结构化日志；普通 `/chat/stream`、SSE 和用户路由不暴露 Trace。

- [ ] **Step 1: 实现 RunTrace 汇总**

在 `RunEvent` 添加 `TraceID`、`ParentSpanID`、`InputTokens`、`OutputTokens`、`CandidateCount`、`CitationCount` 和 `FirstTokenMS`；所有字段为计数/耗时/哈希。`JSONLogObserver` 输出这些字段。每个 Chat Run 将 `RunID` 同时作为 `TraceID`，避免新增跨服务传播协议。

- [ ] **Step 2: 在日志边界暴露 Trace**

不新增 HTTP endpoint、MySQL Trace 表或 Vue 页面。`run_id` 作为 `trace_id`，由 `JSONLogObserver` 输出阶段、耗时、计数、错误码和 stop reason。运维通过现有日志查询按 `trace_id` 关联事件；接入 Phoenix、Langfuse、OpenTelemetry Collector 或独立可视化平台须另立设计与计划。

- [ ] **Step 3: 更新规格和测试目录文档**

在 `SPEC.md` 描述 Go Agent Trace 字段与脱敏边界；在 `tests/TEST.md` 记录相应测试职责。

- [ ] **Step 4: 编写 Go Trace 与隐私测试**

覆盖取消、模型失败和证据不足三条终态均有 `complete` Span 与稳定 stop reason；验证日志不含 question、Prompt、Evidence 正文，Chat 响应与 SSE 不暴露 Trace。验证日志编码失败或 Observer panic 均不会影响正常 Chat、assistant 消息持久化或 SSE。

- [ ] **Step 5: 运行验证，检查改动并提交**

Run:

```bash
cd backend/go-api && go test ./internal/agent ./internal/httpapi
```

Expected: PASS。

```bash
git add backend/go-api/internal/agent backend/go-api/internal/httpapi/chat.go tests/TEST.md SPEC.md
git commit -m "feat(agent): 输出脱敏问答 Trace"
```

## 验收与发布顺序

1. 合并 Task 1 后，检索金标与 Chat 金标必须可独立加载、版本化和审核。
2. 合并 Task 2--3 后，`RAGAS=1` 可在真实隔离环境生成脱敏报告；默认 CI 完全不需要 Judge 凭据。
3. 合并 Task 4 后，能从一个 `job_id` 定位排队、解析、Embedding 限速和索引阶段。
4. 合并 Task 5 后，运维能按 `run_id` 在现有日志系统关联回答链路的脱敏阶段摘要；Chat 响应与 SSE 不暴露 Trace。
5. 连续三轮人工审核 Ragas 低分样本后，才为各 Ragas 指标提出门槛 PR；在此之前不把 LLM Judge 均分作为发布硬门禁。

## 计划自检

- SPEC 覆盖：Python gRPC 边界、Go Chat 所有权、MySQL 状态权威性、测试层级、结构化可观测性与敏感数据约束均有对应任务。
- 未覆盖范围：外部 Phoenix/Langfuse/OTel Collector、生产 Trace 长期存储、Trace 查询 API、独立 Dashboard、多人标注工作流和公开用户 Dashboard 均明确排除，须另立设计与计划。
- 依赖一致性：Task 1 的样本契约供 Task 2/3 使用；Task 2 的运行记录供 Task 3 使用；Task 4/5 独立输出同一脱敏 Trace 语义。
- 审查重点已在每个拥有代码的 Task 中安排测试；不存在待定接口或隐式生产密钥依赖。
