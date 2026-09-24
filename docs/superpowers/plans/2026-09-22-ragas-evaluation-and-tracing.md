# Ragas 评估与 OpenTelemetry/Prometheus 管理员观测实施计划

> 2026-09-24 排期调整：本文件 Task 1--3（Ragas 评测）暂缓，不属于当前执行范围。管理员观测改以独立的 [`2026-09-24-admin-observability.md`](2026-09-24-admin-observability.md) 为准；本文件 Task 4--9 仅保留历史设计背景，避免两份计划同时执行。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 产品建立可复现的离线回答质量评测，以及跨 Go/Python 的脱敏 Trace、Metric 和仅系统管理员可访问的观测仪表盘。

**Architecture:** 确定性指标继续作为 CI 硬门禁；Ragas 只在显式真实模型评测中读取当次 Chat 输出并生成脱敏报告。Go API、Python gRPC Server、Worker 和 Outbox 分别用 OpenTelemetry SDK 输出 Trace/Metric，经私网 Collector 将 Metric 送往 Prometheus、Trace 送往 Tempo；现有 JSON 日志继续保留并带关联 ID。Go 产品 API 查询两个后端并执行管理员授权，Vue 仪表盘只调用 Go 的受限只读接口；Python 不新增 HTTP 业务接口。

**Tech Stack:** Python 3.12、pytest、Ragas 0.4.3、OpenAI-compatible judge model、Go 1.26、Vue、OpenTelemetry SDK/Collector、Prometheus、Tempo、slog、structlog、gRPC、Docker Compose。

**Spec:** `SPEC.md`（§1、§2.2、§2.3、§2.5、§3.1、§4 测试与安全边界）。

## Global Constraints

- Python 只提供版本化 gRPC；不得新增 FastAPI/HTTP 调试或评测接口。
- Go 是 Chat Model、Agent、SSE 与所有公网/产品 API 的唯一所有者。
- MySQL Job/Task/Document 是权威业务状态；Trace 不能改变状态机、Outbox 或 ACK/NAK 语义。
- 本计划按现有单租户产品设计采用全站 `admin`/`user` 两级角色；普通注册只产生 `user`，只有离线运维命令可授予或撤销 `admin`。角色变更和观测 API 的服务器端授权由 Go 控制，不能信任前端菜单或 JWT 中的旧角色。
- Prometheus、Tempo、Collector OTLP/Metric 接口只在 Compose 私网；Caddy 不发布它们。浏览器不直连、不嵌入这些后端的 UI；Go 只提供预设、限时、限范围的只读查询，不接受任意 PromQL/TraceQL/URL。
- NATS 消息仍只携带 `task_id`。同步 Go→Python gRPC 用 W3C `traceparent` 传播；异步 Worker 新建独立 Trace，以 `task_id/job_id` 关联，不声称跨队列存在连续父子 Span。不得为观测改变 Outbox/消息语义。
- `run_id`、`trace_id`、`job_id`、`task_id`、`document_id` 只进脱敏日志/Span，绝不作 Metric 标签；Metric 标签限服务、固定操作/阶段、结果及稳定错误码。自动埋点不得采集正文、Prompt、Evidence、文件名、凭据、URL 查询参数或模型私有推理。
- Collector/Prometheus/Tempo 故障不得影响业务请求、SSE、Worker ACK/NAK 或 Job 状态；仪表盘给出明确不可用状态，不伪造零值。保留现有 JSON 日志和 MySQL 业务状态，不用 Metric/Trace 替代审计。
- Ragas 调用真实 Judge 会消耗额度，必须使用显式 marker 与环境变量，不能进入 `make ci`。
- 不保存 API Key、完整 Prompt、用户原文、完整 Evidence 或 LLM 自由文本快照；报告只保存哈希、ID、来源定位、分数、耗时和脱敏失败原因。
- 评测样本和每次评测输出分离：金标 fixture 可入库，运行报告必须位于 Git 忽略的 `tests/eval/log/`。
- 新增或变更测试文件、marker、fixture 时同步更新 `tests/TEST.md`。
- 修改 Makefile、Earthfile、Docker Compose 或 CI 时运行 `tests/contract/test_build_entrypoints.py`。
- 每个 Task 先完成实现、配置和文档，再在该 Task 末尾编写或调整测试并运行验证；不安排预期失败的测试运行。

## Review Focus

- 无答案问题必须被评为“正确拒答”，不能因缺少参考答案被当作 Ragas 运行错误；Task 2 覆盖。
- Chunk 重建会改变 `chunk_id`；评测真值必须优先按文档摘要和 locator 匹配；Task 1 覆盖。
- 原始测试文档丢失或改动时，真实 Chat 评测必须失败并指出语料版本不匹配，不能只凭金标文件伪装端到端回归；Task 1、3 覆盖。
- Judge 配置缺失、超时或返回不可解析结果时，评测报告必须标明 `unavailable`，不能伪造零分或通过；Task 3 覆盖。
- 自动和手工 Span 均不得包含 question、Prompt、Evidence 正文、密钥或模型私有推理；Task 5--6 覆盖。
- Go→Python 同步调用必须同 `trace_id` 且父子关系正确；异步 Worker 依业务 ID 关联并明确显示为独立 Trace；Task 5--6 覆盖。
- 摄取失败、NATS redelivery、Chat 取消，以及 Collector/Tempo/Prometheus 不可用，不得改变 ACK/NAK、Job、SSE 或回答；Task 5--6、8 覆盖。
- 普通用户、过期登录和撤权后的管理员均不能查询 Metric/Trace；只隐藏前端入口不算授权；Task 7--9 覆盖。
- 仪表盘查询超时、空结果和部分观测后端故障必须显示真实状态；禁止任意查询及高基数标签；Task 8--9 覆盖。

---

## 交付范围和执行顺序

本计划拆为九个可独立验收的工作包。Task 1--3 是“离线 Ragas 评测”；Task 4 是私网观测基础设施；Task 5--6 是 Python/Go 埋点与跨语言传播；Task 7--9 是系统管理员权限、受限查询和仪表盘。Task 1--3 可独立推进；Task 5--6 依赖 Task 4 的 OTLP 接口，Task 8 依赖 Task 4/7，Task 9 依赖 Task 8。每个工作包实现、配置和文档先完成，对应测试最后调整，验证通过后按仓库 Phase 小模块规则单独提交。

### Task 1: 建立版本化 Chat 金标契约与样本校验

**Files:**
- Create: `tests/eval/fixtures/chat_quality.json`
- Create: `tests/eval/fixtures/chat_corpus.json`
- Create: `tests/eval/chat_quality_contract.py`
- Create: `tests/eval/test_chat_quality_contract.py`
- Modify: `tests/eval/test_real_retrieval_quality.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Consumes: `tests/eval/test_real_retrieval_quality.py` 中现有的 10 份自造文本 `CORPUS` 和对应 30 条 `QUESTIONS`；`tests/eval/fixtures/retrieval_quality.json` 只有预设 chunk 排名，不含问题或原始文档，不作为 Chat 金标来源。本地《计组复习》PDF 的 50 问 fixture 与未入库 PDF 仍是另一套真实资料验收，不混入本任务。
- Produces: 版本化的 `chat_corpus.json` 原始测试文档、`ChatQualityCase`、`ExpectedEvidence`、`load_chat_quality_cases(path: Path) -> tuple[ChatQualityCase, ...]`，供 Task 2 的 Chat 执行器和 Task 3 的 Ragas Runner 使用。真实 Chat 回归必须先摄取同版本原始测试文档，金标不能代替语料。

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

- [ ] **Step 2: 固定原始测试语料及首批金标**

把现有 `CORPUS` 的 10 份自造文本抽取到 `chat_corpus.json`，让既有真实检索测试和新的 Chat 评测共用同一份原文与 SHA-256；保留既有 30 问的检索验收语义。从这 30 问生成至少 30 个可回答 Chat Case，并增加至少 5 个明确无法从这 10 份文档回答的问题；每个可回答 Case 记录来源文档 SHA-256、行号 locator、参考答案及关键事实，拒答 Case 明确 `expects_answer=false`。每条 Case 写入与真实语料相符的 `labels`（例如 `text`、`no_answer`），不能凭空标为 PDF/CHM。不要在 fixture 保存生产用户问题或完整生产回答。

- [ ] **Step 3: 更新测试目录文档**

更新 `tests/TEST.md` 的 Eval 目录树、职责表和运行边界，明确该 fixture 是 Chat 金标而非 LLM 输出快照。

- [ ] **Step 4: 编写契约测试**

覆盖可回答 Case 必须有参考答案和稳定 locator、来源 SHA-256 与 `chat_corpus.json` 实际字节一致、拒答 Case 无需参考答案、重复 ID 与非法字段被拒绝、样本数量和标签符合约定；源文档变化但金标未同步时必须失败。

- [ ] **Step 5: 运行质量契约与既有检索评测**

Run: `uv run pytest tests/eval/test_chat_quality_contract.py tests/eval/test_retrieval_quality.py -v`；真实检索链路验收走 `make docker-test SUITE=eval`，不在普通离线 pytest 中直接启动。

Expected: 契约/固定排名测试 PASS；真实检索测试需要 Docker、Embedding 和 gRPC 环境，条件不具备时明确记录未运行，不能声称通过。

- [ ] **Step 6: 检查改动并提交**

```bash
git add tests/eval/fixtures/chat_corpus.json tests/eval/fixtures/chat_quality.json tests/eval/chat_quality_contract.py tests/eval/test_chat_quality_contract.py tests/eval/test_real_retrieval_quality.py tests/TEST.md
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
- Modify: `docs/test/testing-guide.md`
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

在 `ragas_runner.py` 实现权限 `0600`、单 Case 单连接的 Unix domain socket collector，以及测试用户、真实 Chat API、真实 RAG gRPC 栈与显式 Judge 配置的调用支撑。运行前加载 Task 1 的 `chat_corpus.json`，校验文档 SHA-256 后用 gRPC 上传到专用随机 Dataset，等待摄取完成再问答；结束时按既有 eval 清理契约删除 Dataset 并轮询 purge。语料缺失、摘要不符或摄取失败时标记评测不可用，不得只用 `chat_quality.json` 离线伪造真实链路。收到 `EvaluationPayload` 后立即在内存调用 Ragas 并关闭、删除 socket；只将脱敏 report 写入 `tests/eval/log/`，不得把回答或 Evidence 正文写入 Git。后续真实环境测试只由 `make docker-test SUITE=eval RAGAS=1` 调用，默认 `make ci` 与默认 eval 不调用 Judge。

- [ ] **Step 5: 写入报告阈值与报告格式**

首个稳定基线只要求所有 Case 可完成、确定性断言通过、Judge 成功率 100%；记录指标均值、按 `labels` 的分组均值、模型版本、Ragas 版本、耗时和成本摘要。连续三次基线稳定后，另行提出 PR 将各分数阈值写入 fixture；不得在首次接入时猜测质量阈值。

- [ ] **Step 6: 更新构建入口、规格和测试文档**

`Earthfile` 新增仅用于 Docker eval 的 Ragas target；`Makefile` 保持 `make docker-test SUITE=eval` 入口，以 `RAGAS=1` 显式选择。更新 `SPEC.md` 的测试章节，明确 Ragas 属于离线、真实模型、非确定性辅助评测；更新 `tests/TEST.md` 与 `docs/test/testing-guide.md`。

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
git add pyproject.toml uv.lock tests/eval Earthfile Makefile tests/TEST.md docs/test/testing-guide.md SPEC.md
git commit -m "feat(eval): 接入 Ragas 离线回答评测"
```

### Task 4: 私网 Collector、Prometheus 与 Tempo 管道

**Files:** Create `deploy/observability/{collector,prometheus,tempo}.yaml`；Modify `docker-compose.yml`、`compose.product.yml`、`compose.production.yml`、`docs/deployment-production.md`、`SPEC.md`、`tests/contract/test_container_artifacts.py`、`tests/contract/test_build_entrypoints.py`、`tests/TEST.md`。

**Interfaces:** 四个应用进程向 `otel-collector:4318` 发送 OTLP/HTTP；Collector 在私网 `:9464/metrics` 暴露聚合指标供 Prometheus 抓取，并向 Tempo 发送 Trace；仅 Go API 可查询私网 `prometheus:9090` 和 `tempo:3200`。

- [ ] **Step 1: 配置观测管道。** Collector 配 OTLP receiver、`memory_limiter → attributes/redact → batch` processors、Prometheus exporter 和 Tempo OTLP exporter。脱敏清除请求头、`url.query`、`db.query.text`、`gen_ai.input.messages`、`gen_ai.output.messages`、正文、Prompt、Evidence 等属性；应用端也须先执行允许列表。Prometheus 保留 15 天，Tempo 保留 7 天并使用命名卷；三个镜像锁定确定版本与来源校验，不使用 `latest`。
- [ ] **Step 2: 接入三套 Compose。** Go API、Python gRPC Server、Worker、Outbox 均获得私网 OTLP endpoint 和分别为 `rag-go-api`、`rag-python-server`、`rag-python-worker`、`rag-python-outbox` 的 `service.name`。Collector、Prometheus、Tempo 不发布宿主端口，Caddy 不代理观测后端；业务服务不得用观测后端的 healthcheck 作为启动前提。保持 Secret 隔离和持久卷保护。
- [ ] **Step 3: 更新规格和部署文档。** 明确 OTel、Prometheus、Tempo、JSON 日志的职责、网络边界、保留期、故障降级和备份/升级操作。
- [ ] **Step 4: 最后调整契约测试。** 覆盖三套 Compose 的服务、网络、无公网观测端口、卷及 Collector 脱敏；同步 `tests/TEST.md`。运行 `uv run pytest tests/contract/test_container_artifacts.py tests/contract/test_build_entrypoints.py -v`，再运行现有公开 Compose 配置验证入口；Docker/Secret 不可用则记为未运行。
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(observability): 建立私网观测管道`；报告实际运行与未运行的命令。**

### Task 5: Python gRPC、摄取和检索埋点

**Files:** Create `src/rag_mvp/telemetry.py`、`tests/unit/test_telemetry.py`；Modify `pyproject.toml`、`uv.lock`、`src/rag_mvp/bootstrap/container.py`、`src/rag_mvp/rpc/server.py`、`src/rag_mvp/observability.py`、`src/rag_mvp/ingestion/pipeline.py`、`src/rag_mvp/ingestion/worker.py`、`src/rag_mvp/outbox/main.py`、`src/rag_mvp/application/retrieval_service.py`、`SPEC.md`、`tests/TEST.md`；按需调整既有 resilience 测试。

**Interfaces:** 接收 Go gRPC client 的 W3C `traceparent`；产生 server/retrieval/ingestion/outbox Span、有限标签的 counters/histograms；保留现有 JSON 日志并追加 `trace_id/span_id`。Worker 独立创建 Trace，仅靠 `task_id/job_id` 关联上传链路。

- [ ] **Step 1: 在 `telemetry.py` 封装 Python tracer/meter provider、OTLP exporter、W3C propagator、允许列表属性 helper 和有界 shutdown；由现有进程入口及 Container 初始化，不在 `domain/` 创建 SDK 依赖。Collector 不可用不能让业务启动或状态迁移失败。**
- [ ] **Step 2: 接入 grpc.aio server context；为 retrieval 的 dense/sparse、复核、rerank、evidence 及摄取的 object read、parse、chunk、embedding、index、complete/failed/cancelled 建立 Span。Outbox 仅观测 finalization、publish/retry，不消费 Task。redelivery 记录数值属性，不改 NATS 消息体。**
- [ ] **Step 3: 固定 `rag_ingestion_tasks_total{stage,outcome}`、`rag_ingestion_stage_duration_seconds{stage}`、`rag_retrieval_duration_seconds{stage}`、`rag_outbox_publish_total{outcome}` 的语义与单位；最终导出名称在契约测试中固定。ID、查询哈希和来源名称不进入 Metric 标签；Span 属性只允许 ID、阶段、计数、耗时和稳定错误码。同步更新 `SPEC.md`。**
- [ ] **Step 4: 最后添加内存 exporter 测试，覆盖各阶段、同 trace 的 gRPC server span、脱敏、故障 exporter、取消/redelivery/强杀恢复后的 ACK/NAK 和 Job 幂等性；更新 `tests/TEST.md`。运行 `uv run pytest tests/unit/test_telemetry.py tests/unit/test_observability.py tests/unit/ingestion -v`、`make ci`、`make docker-test SUITE=resilience`；若触及 adapter 另跑对应 integration/contract suite。**
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(ingestion): 接入脱敏 OpenTelemetry 观测`；报告实际验证与未运行项。**

### Task 6: Go Agent 埋点与同步 gRPC Trace 传播

**Files:** Create `backend/go-api/internal/telemetry/telemetry.go`、`backend/go-api/internal/telemetry/telemetry_test.go`；Modify `backend/go-api/go.mod`、`backend/go-api/go.sum`、`backend/go-api/cmd/api/main.go`、`backend/go-api/internal/ragclient/client.go`、`backend/go-api/internal/agent/observer.go`、`backend/go-api/internal/agent/runtime.go`、`backend/go-api/internal/httpapi/chat.go`、`backend/go-api/internal/agent/observer_test.go`、`backend/go-api/internal/httpapi/integration_test.go`、`SPEC.md`、`tests/TEST.md`。

**Interfaces:** Go Chat 根 Span 经 gRPC client 将 `traceparent` 注入现有 metadata；Python server Span 必须继承同一个 OTel `trace_id`。`run_id` 保持独立业务关联 ID，不强行充当 OTel trace ID。

- [ ] **Step 1: 在 `internal/telemetry` 初始化 Go tracer/meter、OTLP exporter、W3C propagator 及有界 shutdown；Collector 失败时保留 JSON 日志、Chat 和 SSE 业务语义。**
- [ ] **Step 2: 为 Route、Model、Tool、Assess、Rewrite、Finalize、Complete 建立子 Span；gRPC client 接入传播。保留 `JSONLogObserver` 的 run ID、错误码、终止原因，追加当前 trace/span ID；取消/失败只发一个终态业务事件。**
- [ ] **Step 3: 固定 `rag_chat_runs_total{outcome,stop_reason}`、`rag_chat_duration_seconds`、`rag_grpc_client_duration_seconds{method,outcome}`、`rag_agent_model_calls_total{phase}`；全部标签为枚举。更新 `SPEC.md` 的同步跨语言传播、`run_id`/`trace_id` 区别和脱敏边界。**
- [ ] **Step 4: 最后用内存 exporter 和真实 gRPC 连接测试同 trace 及 client/server 父子关系；覆盖取消、模型错误、证据不足、Observer/exporter 故障不影响回答/SSE/持久化，敏感内容不进入 Span/Metric；更新 `tests/TEST.md`。运行 `cd backend/go-api && go test ./internal/telemetry ./internal/agent ./internal/ragclient ./internal/httpapi`、`make ci` 和实际 Docker 跨语言验收；区分已运行和未运行。**
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(agent): 接入跨语言 OpenTelemetry 问答观测`。**

### Task 7: 系统管理员角色和受控授予

**Files:** Create `backend/go-api/cmd/admin-role/main.go`、`backend/go-api/internal/storage/admin_role_test.go`；Modify `backend/go-api/internal/storage/schema.sql`、`backend/go-api/internal/storage/storage.go`、`backend/go-api/internal/httpapi/server.go`、`backend/go-api/internal/httpapi/server_test.go`、`apps/web/src/stores/auth.ts`、`apps/web/src/api/auth.ts`、`SPEC.md`、`docs/deployment-production.md`、`tests/TEST.md`。

**Interfaces:** `users.role` 为 `user|admin`，默认 `user`；`Store.UserRole(ctx,userID)` 每次从 DB 读取；`Store.SetUserRole(ctx,userID,role)` 只供离线 CLI；`GET /me` 返回当前角色。管理员路由使用 `authenticate + requireAdmin`，不得信任旧 Cookie/JWT 中的角色。

- [ ] **Step 1: 修改新库 schema 和 `Store.Migrate` 的幂等旧库迁移，旧用户一律为 `user`。CLI 只接受已存在 user ID 和目标角色，写脱敏操作审计；授予/撤销在事务和行锁下执行，拒绝并发撤销最后一名管理员。注册永远只创建普通用户，不提供在线自助提权。**
- [ ] **Step 2: 在 Go 服务端增加 `requireAdmin`，每次查当前 DB 角色，DB 不可用时拒绝；更新 `/me` 和前端 auth 类型。同步 `SPEC.md` 和运维首次授权/撤权说明。**
- [ ] **Step 3: 最后测试新库/旧库/重复迁移、注册默认角色、普通用户对受保护测试路由为 403、管理员可访问、撤权后旧 Cookie 立即 403、并发撤销最后管理员被拒及 DB 故障时拒绝；更新 `tests/TEST.md`。运行 `cd backend/go-api && go test ./internal/storage ./internal/httpapi ./cmd/admin-role` 和 `make ci`。**
- [ ] **Step 4: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(auth): 增加系统管理员观测权限`。**

### Task 8: Go 管理员只读查询 API

**Files:** Create `backend/go-api/internal/observability/query.go`、`backend/go-api/internal/observability/query_test.go`、`backend/go-api/internal/httpapi/observability.go`、`backend/go-api/internal/httpapi/observability_test.go`；Modify `backend/go-api/internal/httpapi/server.go`、`backend/go-api/cmd/api/main.go`、`compose.product.yml`、`compose.production.yml`、`SPEC.md`、`tests/TEST.md`。

**Interfaces:** 仅 Go API 使用固定 `PRODUCT_PROMETHEUS_URL`/`PRODUCT_TEMPO_URL`。管理员接口为 `GET /admin/observability/metrics?window=15m|1h|6h|24h`、`GET /admin/observability/traces?service=<固定枚举>&window=<固定枚举>`、`GET /admin/observability/traces/:trace_id`。前者返回预设图表序列，列表至多 100 条，详情只接受 32 位十六进制 ID 并返回脱敏 Span 树。

- [ ] **Step 1: 实现只读查询客户端。** 内置 PromQL 白名单（Go/Python 吞吐、错误率、p50/p95、摄取/Outbox 结果）；Tempo 只按四个固定 `service.name` 和时间窗搜索。每次查询 3 秒超时、1 MiB 响应上限、固定采样点数；不得接收任意 PromQL/TraceQL、URL 或 label key/value。服务端二次过滤 Span 属性，只返回 service/stage/duration/outcome/error_code 和允许的 run/job/task ID。
- [ ] **Step 2: 将全部路由放在 `authenticate + requireAdmin` 后；未登录 401、非管理员/撤权 403。空结果、单后端失败和查询超时返回独立状态与稳定错误码，不伪造零值、不回显内网 URL。Go 经私网访问后端，Caddy 仍不代理后端；同步 `SPEC.md`。**
- [ ] **Step 3: 最后用假 Prometheus/Tempo server 测试查询映射、恶意输入、非法 ID、空值、超时/超限、敏感属性过滤、403/撤权/后端失败；更新 `tests/TEST.md`。运行 `cd backend/go-api && go test ./internal/observability ./internal/httpapi`、`uv run pytest tests/contract/test_build_entrypoints.py -v` 和 `make ci`。**
- [ ] **Step 4: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(observability): 提供管理员只读指标与链路查询`。**

### Task 9: Vue 管理员仪表盘与真实链路验收

**Files:** Create `apps/web/src/views/ObservabilityView.vue`、`apps/web/src/api/observability.ts`、`apps/web/tests/observability.spec.ts`；Modify `apps/web/src/router/index.ts`、`apps/web/src/components/AppShell.vue`、`apps/web/src/stores/auth.ts`、`apps/web/src/api/contracts.ts`、`apps/web/src/mocks/handlers.ts`、`apps/web/src/i18n/messages.ts`、`docs/development/live-product-plane.md`、`tests/TEST.md`。

**Interfaces:** `/admin/observability` 只消费 Task 7 的 `/me.role` 和 Task 8 三个只读 Go API；浏览器不直接访问 Prometheus、Tempo 或 Collector。

- [ ] **Step 1: 仅向 admin 显示“观测”导航；管理员路由首次加载和刷新均复核 `/me`，不能用本地缓存自报角色。**
- [ ] **Step 2: 页面提供预设的吞吐、错误率、延迟、摄取/Outbox 结果图表，固定时间窗和服务筛选，Trace 列表与脱敏 Span 瀑布；分别显示空数据、部分后端不可用和权限撤销。无任意查询编辑器、无后端原生 UI iframe。**
- [ ] **Step 3: 更新产品文档、`tests/TEST.md`；最后编写前端测试，覆盖普通用户无入口且直访被拒、管理员可见、撤权后立即失权、空/错误状态和敏感字段不渲染。真实 Compose 中跑一次 Chat 与摄取，确认 Go/Python Metric 均存在、同步 gRPC 同 trace、Worker 可按 job/task 关联；停止观测服务验证业务正常且页面正确降级。**
- [ ] **Step 4: 运行 `cd apps/web && npm test -- --run`、`cd apps/web && npm run build`、`make ci` 和可用的真实 Docker 验收；检查 `git status`，只暂存本 Task 文件，单独提交 `feat(web): 增加管理员观测仪表盘`，报告实际运行与未运行项。**

## 验收与发布顺序

1. Task 1--3 后，固定金标和 `RAGAS=1` 隔离评测可运行；默认 CI 不调用 Judge。Ragas 分数仍是辅助评测，连续三轮人工审核后另提阈值 PR。
2. Task 4 后，Collector、Prometheus、Tempo 仅在私网运行，Caddy 不发布观测端口；Compose/Secret/持久卷契约通过。
3. Task 5--6 后，一次 Chat 的 Go→Python gRPC Span 同 trace，两个语言的预设 Metric 可查；Worker 用 job/task 关联独立 Trace；观测后端故障不影响业务。
4. Task 7 后，普通注册及旧用户均无管理员权限，离线授予和撤销立即生效。
5. Task 8--9 后，管理员能在产品页面查看预设 Metric 与脱敏 Trace，普通/未登录/撤权用户无法查询；后端不可用显示真实状态。
6. 发布前运行 `make ci`、与变更相称的 Docker integration/resilience suite 和真实跨语言观测验收；未运行项逐一说明。

## 计划自检

- 当前 `SPEC.md` 只约定 JSON 日志和未来 trace context，旧计划排除外部平台与 Dashboard；Task 4--9 各自同步规格，执行前不得将新架构当作既有行为。
- Python gRPC、Go 产品控制面、NATS `task_id` 消息、MySQL 权威状态和 Ragas 独立门禁保持原有边界。
- OTel SDK/Collector 负责采集和路由，Prometheus 只存 Metric，Tempo 存 Trace；Go 授权和只读查询，Vue 展示。
- 不包含多租户 RBAC、任意 PromQL/TraceQL、用户级观测、内容回放、公开观测后端和跨 NATS 连续父子 Span。
