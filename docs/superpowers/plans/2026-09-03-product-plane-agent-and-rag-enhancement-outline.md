# 产品控制面、Agent 与 RAG 增强迭代任务大纲

> **For agentic workers:** 本文件是项目级任务拆解，不替代逐文件实施计划。开始每个工作包前，必须再创建独立的实施计划并使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按任务执行。

**目标：** 在不破坏已验收 Python RAG 可靠性语义的前提下，交付 Go/Gin 产品控制面、Vue/TypeScript 用户界面、Go Agent Harness，以及可度量的 DeepDoc/AST 检索增强能力。

**架构：** 浏览器只访问 Go/Gin 的 HTTP 与 SSE 接口；Go 通过共享 protobuf 生成的 client 调用 Python `RagService`；Python 继续是摄取、Task、Job、Outbox、NATS 和索引版本状态的唯一写入方。Agent 的 Chat Loop、Tool Calling、Citation 编号和 SSE 协议均属于 Go；Python `Retrieve` 仅作为一个受控工具返回 evidence。

**技术栈：** Go、Gin、gRPC、protobuf、MySQL、Vue 3、TypeScript、Vite、Pinia、Vue Router、SSE、Python RAG、Elasticsearch、NATS JetStream。

**规格依据：** `SPEC.md` §5.6、§6 Phase 4、§7.3；`PLAN.md` §9 Milestone E；`AGENTS.md`。

## 全局约束

- Python 的唯一服务接口仍为版本化 gRPC；不得新增 FastAPI 或 HTTP adapter。
- Go 是唯一公网入口，承担认证、租户、会话、Agent、Chat Model 与 SSE；浏览器不得直连 Python gRPC、MySQL、NATS 或 Elasticsearch。
- Go 不得直接写 Python 维护的 `Document`、`Job`、`Task`、`OutboxEvent`、`IndexBuild` 等状态表；所有 RAG 状态变更必须经 Python gRPC 用例。
- Python `Retrieve` 返回 evidence、分数和 locator；Go 负责 Prompt、`[n]` Citation、工具选择与循环停止。
- 上传仍必须遵循 Python 的 staging object → Finalizer → Outbox → NATS 可靠路径；不得由 Go 在写入后直接 publish。
- Agent Tool Calling 必须带 tenant、dataset 授权范围、deadline、调用预算和审计信息；模型自由文本不能直接成为 SQL、系统命令或未校验的 RAG 请求。
- DeepDoc/AST 的任何优化必须保持 `Parser` / `Chunker` 契约、稳定 `chunk_id` 规则、locator、`index_version` 和 generation fence 语义；优化效果由固定评测集度量，不接受只凭主观回答质量上线。
- 修改 proto、Earthfile、Makefile、Compose 或工作流时，必须同步更新生成物/测试文档，并运行相应 contract tests。

---

## 目标态文件树（基于当前仓库的增量迭代）

以下树以当前仓库为基线：保留根目录的 `src/rag_mvp/`、`migrations/`、`docker/`、`tests/`、`proto/` 与现有 Compose/Earthly 文件，仅新增 `apps/web/` 和 `apps/go-api/`。本迭代**不迁移** Python 到 `services/rag-python/`，也不将 `docker/` 改为 `deploy/`；这类纯目录重构应在产品闭环稳定后独立立项，以避免改动既有导入、Alembic、Docker 构建和测试路径。

```text
RAG/
├─ apps/
│  ├─ web/                                  # Vue 3 + TypeScript：唯一浏览器应用
│  │  ├─ src/
│  │  │  ├─ api/                            # 仅调用 Go /api/v1 与 SSE，不直连 Python
│  │  │  │  ├─ http.ts                      # bearer token、request ID、Problem Detail 映射
│  │  │  │  ├─ datasets.ts
│  │  │  │  ├─ documents.ts
│  │  │  │  ├─ jobs.ts
│  │  │  │  └─ chat.ts                      # SSE 事件解码：retrieval/token/final/error
│  │  │  ├─ components/
│  │  │  │  ├─ datasets/
│  │  │  │  ├─ jobs/
│  │  │  │  └─ chat/                        # Citation、Evidence、流式消息组件
│  │  │  ├─ views/
│  │  │  │  ├─ DatasetListView.vue
│  │  │  │  ├─ DatasetDetailView.vue
│  │  │  │  ├─ JobDetailView.vue
│  │  │  │  └─ ChatView.vue
│  │  │  ├─ stores/                         # auth、workspace/dataset、chat UI 状态
│  │  │  ├─ router/
│  │  │  ├─ types/                          # HTTP/SSE DTO；非 Python domain model 镜像
│  │  │  ├─ App.vue
│  │  │  └─ main.ts
│  │  ├─ tests/                             # unit、component、SSE event replay、E2E
│  │  ├─ package.json
│  │  └─ vite.config.ts
│  │
│  └─ go-api/                               # Go + Gin：唯一公网控制面
│     ├─ cmd/api/main.go                     # 组装 HTTP、gRPC client、Model Gateway
│     ├─ internal/
│     │  ├─ http/
│     │  │  ├─ router.go                     # /healthz、/readyz、/api/v1
│     │  │  ├─ middleware/                   # auth、tenant、request ID、rate limit、audit
│     │  │  ├─ handler/                      # dataset、document、job、chat HTTP handler
│     │  │  └─ sse/                          # 事件序列、flush、断连取消、心跳
│     │  ├─ application/                     # 只编排公网用例，不重写 Python RAG 状态机
│     │  │  ├─ dataset_service.go
│     │  │  ├─ document_service.go
│     │  │  ├─ job_service.go
│     │  │  └─ chat_service.go
│     │  ├─ grpcclient/
│     │  │  └─ rag_client.go                 # Python RagService client、deadline、错误映射
│     │  ├─ agent/
│     │  │  ├─ runtime.go                    # Chat Loop、调用预算、取消与终止条件
│     │  │  ├─ model_gateway.go              # Chat Model port 与 provider adapters
│     │  │  ├─ prompt_builder.go
│     │  │  ├─ citations.go                  # evidence → [1]...[N]，Go 独有职责
│     │  │  └─ tools/
│     │  │     └─ retrieve_knowledge.go      # Python Retrieve 的受控 Tool 封装
│     │  ├─ identity/                        # OIDC/JWT、主体、tenant membership
│     │  ├─ conversation/                    # Go 所有的会话与消息持久化
│     │  ├─ repository/                      # 仅 Go 控制面自身表；禁止 RAG Job/Task CRUD
│     │  ├─ observability/                   # trace context、metrics、结构化日志
│     │  └─ generated/rag/v1/                # 由根目录 proto/rag/v1 生成，禁止手改
│     ├─ migrations/                         # identity、membership、conversation 等 Go-owned schema
│     ├─ tests/                              # unit、gRPC contract、HTTP/SSE integration、Agent eval
│     ├─ go.mod
│     └─ go.sum
│
├─ src/rag_mvp/                              # 既有 Python RAG 执行域：本迭代保持原路径
│  ├─ domain/                                # Job/Task/Document/fence 等纯领域规则
│  ├─ application/                           # 摄取、检索、删除、取消、重试用例
│  ├─ ports/                                 # Parser、Chunker、Model、Repository、Search 抽象
│  ├─ adapters/
│  │  ├─ parsers/                            # text、markdown、pdf、DeepDoc（经决策门后）
│  │  ├─ chunkers/                           # recursive、structure-aware、AST-aware
│  │  ├─ metadata/                           # MySQL：RAG 状态唯一写入方
│  │  ├─ search_engine/                      # Elasticsearch
│  │  └─ task_queue/                         # NATS JetStream
│  ├─ bootstrap/                             # Python concrete adapter 组装
│  ├─ ingestion/                             # 唯一 ACK/NAK consumer
│  ├─ outbox/                                # Finalizer 与 Relay
│  ├─ retrieval/                             # hybrid、rerank、provenance 等纯算法
│  ├─ rpc/                                   # Python RagService gRPC 实现；不新增 HTTP
│  └─ dev/                                   # generated gRPC client 调试工具
│
├─ migrations/                               # 已有 Python/Alembic RAG schema；Go 不得写入
├─ tests/                                    # 已有 Python unit、contract、resilience、integration、eval、E2E
│
├─ proto/
│  └─ rag/v1/rag_service.proto               # Python/Go 唯一 RAG 交互契约；只增不破坏
│
├─ docker/                                   # 已有 Search Guard、entrypoints、Compose 支持文件
├─ docker-compose.yml                        # 本迭代扩展 go-api/web service 与 profile
├─ docker-compose.debug.yml
├─ Dockerfile*                               # 既有 Python/ES 镜像；按需新增 Go/Web Dockerfile
├─ Earthfile                                 # 扩展 Go/Web lint、test、build 与跨端 E2E target
├─ Makefile                                  # 保持公开入口，避免复制 Earthfile 全命令
│
├─ docs/
│  ├─ plans/                                 # 逐文件实施计划
│  ├─ superpowers/plans/                     # 项目级任务大纲与执行计划
│  ├─ setup/
│  ├─ test/
│  └─ bug/
│
├─ scripts/
│  ├─ generate_proto.py                      # 现有 Python 生成入口；扩展或协调 Go 生成步骤
│  └─ check_generated.py                     # 校验 Python/Go 生成物和跨语言兼容性
├─ pyproject.toml                            # 现有 Python 项目配置，保持根目录
├─ alembic.ini
├─ SPEC.md
├─ PLAN.md
└─ AGENTS.md
```

### 文件树职责红线

| 目录 | 可拥有的状态 | 明确禁止 |
|---|---|---|
| `apps/web` | 浏览器 UI、短暂展示状态、Go API DTO | 直连 Python/MySQL/NATS/ES；分配 Citation；保存模型密钥 |
| `apps/go-api/identity`、`conversation`、`repository` | 用户、租户成员关系、会话、消息、审计 | 直接更新 Python 的 Document/Job/Task/Outbox/IndexBuild |
| `apps/go-api/agent` | Chat Loop、Tool 调度、Prompt、Citation、SSE | 直接执行摄取/索引；替代 Python 检索算法或状态机 |
| `src/rag_mvp`、`migrations`、`tests` | RAG 摄取、检索、Task/Job/Outbox、索引版本和 evidence | 用户认证、会话记忆、SSE 公网协议、Agent 工具选择 |
| `proto/rag/v1` | 跨语言稳定 RPC 契约 | 作为前端 HTTP DTO 或承载浏览器会话/UI 状态 |

---

## 一、公司任务单模板

每张 Jira/飞书/禅道任务单统一采用以下字段：

| 字段 | 填写要求 |
|---|---|
| 编号 / 标题 | 使用 `域-序号`，标题说明可观察交付物，不用“完善/优化”等空泛描述 |
| 类型 / 优先级 | `Epic`、`Feature`、`Spike`、`Quality` 或 `Release`；优先级用 P0–P2 |
| 角色 / 工作量 | 指定主责角色和预估人日；预估不含等待外部账号、模型配额等阻塞时间 |
| 背景与目标 | 写清用户问题及完成后可见行为 |
| 范围 | 明确包含与不包含项，避免跨越 Python/Go 职责边界 |
| 依赖 | 上游任务、外部服务、密钥/模型、设计决策 |
| 交付物 | 代码、迁移、接口契约、页面、评测数据、运维文档等可检查产物 |
| 验收标准 | 可自动化或可演示的 Given/When/Then 条目；列出必须运行的检查 |
| 风险与回滚 | 数据/安全/兼容性风险和 feature flag、版本回退或隔离方案 |

## 二、团队与并行分工

| 工作流 | 建议角色 | 主要职责 |
|---|---|---|
| CP | Go 后端工程师 | Gin API、身份租户、gRPC client、会话、限流、审计 |
| WEB | 前端工程师 | Vue/TypeScript、信息架构、上传/Job/检索/Chat 页面、SSE 状态机 |
| AGENT | Go/LLM 工程师 | Chat Loop、Model Gateway、Tool schema、Prompt/Citation、Agent eval |
| RAG | Python/检索工程师 | DeepDoc 解析、AST 解析/切块、检索算法、离线评测 |
| QE/PLAT | 测试/平台工程师 | 跨语言契约、E2E、可观测性、CI、安全/性能验收 |

建议以 5 人小队、8–10 周为一期估算；具体人日需结合模型供应商、UI 设计和 DeepDoc 方案选型后复核。

## 三、里程碑、依赖与交付顺序

```text
M0 工程边界与契约
 ├─ CP-01 monorepo/Go 基线 ─┬─ CP-02 Go gRPC client ─┬─ CP-03 身份与租户
 └─ WEB-01 Vue 基线 ───────┘                       ├─ CP-04 文档/Job 产品 API ─ WEB-02 管理界面
                                                    └─ AG-01 会话/模型网关 ─ AG-02 Retrieve Tool ─ AG-03 SSE Chat ─ WEB-03 Chat

RAG-01 评测基线 ─┬─ RAG-02 DeepDoc 技术 Spike ─ RAG-03 结构化文档解析
                 ├─ RAG-04 AST 解析与语义切块
                 └─ RAG-05 检索优化与消融评测

M3 跨端验收 = CP-04 + WEB-02 + AG-03 + WEB-03 + RAG-03/04/05 + QE-01
```

## 四、任务 Backlog

### M0：产品面工程基线与契约

#### CP-01｜建立 Go/Gin 控制面骨架

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 4 人日 |
| 主责 | CP（Go 后端） |
| 依赖 | 无 |
| 目标 | 建立独立、可测试的 Go 公网 API 服务，而不侵入 Python RAG 包。 |
| 范围 | 创建 `apps/go-api`（或经架构评审确定的等价路径）、Gin 路由、中间件链、配置、结构化日志、健康/就绪检查、Docker 开发入口。 |
| 不包含 | 用户系统、业务 API、Agent、直接访问 RAG 数据表。 |
| 交付物 | Go module；分层目录（HTTP、application、grpcclient、repository）；`/healthz`、`/readyz`；环境变量样例；本地运行文档。 |
| 验收 | `gofmt -l` 无输出、`go vet ./...` 与 `go test ./...` 通过；未配置 Python gRPC 时 `/readyz` 返回受控未就绪而不是 panic；日志含 request ID。 |
| 风险/回滚 | 新服务不接公网端口，先由 Compose profile 隔离；删除 profile 即可回滚。 |

#### CP-02｜共享 protobuf 与 Go gRPC Client 契约

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 5 人日 |
| 主责 | CP；协作 RAG |
| 依赖 | CP-01；`proto/rag/v1/rag_service.proto` |
| 目标 | Go 可稳定调用 `CreateDataset`、`SubmitDocument`、`GetJob`、`RetryJob`、`CancelJob`、`DeleteDocument`、`DeleteDataset`、`Retrieve`。 |
| 范围 | 固化 buf/protoc 生成方案、生成 Go stub、封装连接池/deadline/错误映射/client interceptor，并建立 Python–Go 兼容测试。 |
| 不包含 | 为迎合 Go 改动 Python RPC 语义；浏览器直连 gRPC。 |
| 交付物 | Go generated code、`RagClient` 接口与 Fake、错误码映射表、跨语言契约测试。 |
| 验收 | 每个 RPC 的成功和业务失败 oneof 可被 Go 正确解码；deadline/cancel 透传；生成物可从干净工作区重复生成且 `git diff` 为空。 |
| 风险/回滚 | proto 使用只增不破坏的版本策略；不兼容变更另建 `v2`，不得覆盖 `v1` 字段号。 |

#### WEB-01｜Vue 3 + TypeScript 前端基础工程

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 4 人日 |
| 主责 | WEB |
| 依赖 | 无；与 CP-01 并行 |
| 目标 | 建立不依赖管理后台脚手架的、可维护的产品前端。 |
| 范围 | Vite、Vue 3、TypeScript strict、Vue Router、Pinia、HTTP client、运行时配置、统一错误页、布局和设计 token。 |
| 不包含 | 完整组件库封装、业务页面、假定后端字段。 |
| 交付物 | `apps/web`、路由守卫骨架、API 类型生成/校验约定、lint/test/build、开发代理配置。 |
| 验收 | `pnpm lint`、typecheck、unit test、production build 通过；刷新深链路不丢路由；未登录路由行为与 API 401 处理可演示。 |
| 风险/回滚 | 前端以独立静态站点部署，不绑定 Python 服务；失败可退回仅使用 Python dev CLI。 |

### M1：控制面 API 与 RAG 管理体验

#### CP-03｜认证、租户边界与请求治理

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 7 人日 |
| 主责 | CP；协作 QE/PLAT |
| 依赖 | CP-01、CP-02 |
| 目标 | 每个公网请求都能关联认证主体、tenant、request ID、限流与审计记录。 |
| 范围 | OIDC/JWT 方案选型与实现、tenant membership、授权中间件、CORS/CSRF 策略、请求大小限制、按主体/tenant 限流、审计事件。 |
| 不包含 | Python 内部授权模型重写；把身份 token 传入日志或 Prompt。 |
| 交付物 | identity/tenant schema、认证中间件、权限策略、测试身份 provider、威胁模型与运行文档。 |
| 验收 | 非成员不能操作或检索目标 dataset；tenant A 不能通过修改 ID 访问 tenant B；超限返回稳定错误；审计记录不含 token/密码/文件原文。 |
| 风险/回滚 | 先以内部开发 OIDC realm 或可替换 provider 实现；身份服务不可用时 fail closed。 |

#### CP-04｜Dataset、Document、Job 公网 API

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 8 人日 |
| 主责 | CP |
| 依赖 | CP-02、CP-03 |
| 目标 | 通过 Go HTTP API 完成数据集创建、文件提交、Job 查询、取消、重试与文档删除。 |
| 范围 | REST resource 设计、上传流转发至 `SubmitDocument`、幂等键、分页/过滤 DTO、Python 错误到 HTTP problem detail 映射。 |
| 不包含 | Go 直接写 RAG MySQL 表、直接向 NATS publish、直接操作 ES。 |
| 交付物 | OpenAPI/接口文档、handler/application service、gRPC adapter、API 集成测试和速率/上传限制配置。 |
| 验收 | 重复 idempotency key 仍返回同一 Python Job；取消/重试/删除只经 gRPC 生效；上传中断不会在 Go 侧留下未受管文件；Job 状态不被 Go 缓存篡改。 |
| 风险/回滚 | API 采用 `/api/v1`；限制单文件/总上传，超限在读取完整 body 前拒绝。 |

#### WEB-02｜数据集、上传与任务追踪界面

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 8 人日 |
| 主责 | WEB |
| 依赖 | WEB-01；CP-04 的稳定接口草案 |
| 目标 | 用户可在浏览器管理 Dataset、提交文档、查看 Job 和安全地执行取消/重试/删除。 |
| 范围 | Dataset 列表/创建、文档上传进度、Document/Job 列表与详情、失败错误展示、轮询状态机、破坏性操作二次确认。 |
| 不包含 | 直接对数据库 CRUD；聊天/Agent 页面。 |
| 交付物 | 页面、可访问性状态、API mock、组件/端到端测试、用户操作文案。 |
| 验收 | 用户能从上传看到 Python Job 终态；网络重连后状态恢复；取消/重试仅对允许状态显示；页面不展示敏感配置或内部堆栈。 |
| 风险/回滚 | API 版本不稳定时使用 mock contract，不在前端埋业务回退逻辑。 |

### M2：Go Agent Harness 与 Chat 体验

#### AG-01｜会话、Chat Model Gateway 与 Agent 运行时

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 7 人日 |
| 主责 | AGENT；协作 CP |
| 依赖 | CP-01、CP-03 |
| 目标 | Go 持有会话与模型调用边界，提供可中断、可审计、有限预算的单次 Agent 执行。 |
| 范围 | conversation/message 数据模型、ModelGateway 抽象、provider adapter、上下文窗口预算、请求取消、最大轮数/Token/工具次数预算、结构化 trace。 |
| 不包含 | Python 生成答案；任意外部工具；跨会话“长期记忆”策略。 |
| 交付物 | Agent runtime 接口、Fake model、会话 API 内部用例、模型错误分类、单元/契约测试。 |
| 验收 | 模型超时、取消、无效 tool arguments、超出最大循环次数均产生受控终态；任一 trace 不记录 API key、Authorization 或完整敏感文档。 |
| 风险/回滚 | 先仅支持一个模型 provider；以 feature flag 关闭 Agent 后保留普通检索调试能力。 |

#### AG-02｜将 Python `Retrieve` 封装为受控 RAG Tool

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 6 人日 |
| 主责 | AGENT；协作 CP/RAG |
| 依赖 | CP-02、CP-03、AG-01 |
| 目标 | Agent 能按授权 dataset 调用 `Retrieve` 并得到可引用 evidence，且不能借模型参数越权。 |
| 范围 | Tool JSON schema、dataset allow-list 注入、query/filter 校验、deadline、max top-k、调用审计、evidence 到 Prompt context 的转换。 |
| 不包含 | Python 侧 Agent Loop、模型自行指定 tenant/dataset、Tool 调用 SQL/Web/MCP。 |
| 交付物 | `retrieve_knowledge` Tool、调用记录、evidence DTO、citation seed、Fake RagClient 测试。 |
| 验收 | 模型生成非法 dataset ID 不会越权；RAG 失败后 Agent 返回可读错误而非编造答案；每个进入 Prompt 的证据都保留 `chunk_id`、source name、locator 与 score。 |
| 风险/回滚 | Tool schema 版本化；将未授权字段从模型可见参数中移除，而非仅依赖 Prompt 约束。 |

#### AG-03｜Chat SSE API、Citation 与 Agent 评测

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 8 人日 |
| 主责 | AGENT；协作 CP/QE |
| 依赖 | AG-01、AG-02 |
| 目标 | 浏览器获得 `retrieval`、`token`、`final`、`error` 事件；最终回答中的 Citation 与实际 evidence 一一对应。 |
| 范围 | `POST /api/v1/chat/stream` 或等价 SSE 契约、事件序号、断连取消、Prompt builder、`[1]...[N]` 编号、最终消息持久化、固定 Agent eval。 |
| 不包含 | WebSocket 双向协议、多 Agent 编排、MCP、后台无限循环。 |
| 交付物 | SSE schema、Chat handler、Citation builder、SSE integration tests、含“无检索/检索失败/多 citation”的固定评测集。 |
| 验收 | 事件顺序严格为 retrieval（可选）→ token* → final 或 error；客户端断开会取消模型/工具调用；final citations 均能回链到本次 evidence，不能引用未检索内容。 |
| 风险/回滚 | 强制单次请求时长、心跳和代理 buffering 配置；无法流式时返回受控 error，不降级为伪流式。 |

#### WEB-03｜Chat、证据与流式状态界面

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P0 / 7 人日 |
| 主责 | WEB |
| 依赖 | WEB-01、AG-03 |
| 目标 | 用户能选择授权 Dataset 进行聊天，实时看到状态并展开引用证据。 |
| 范围 | 会话列表、消息流、SSE reducer、停止生成、检索状态、Citation 到 source/locator 的跳转、错误重试。 |
| 不包含 | 前端自行调用模型、拼 Prompt、生成 Citation；无限滚动长期记忆。 |
| 交付物 | Chat 页面、SSE client、离线事件回放测试、可访问性与断网恢复行为。 |
| 验收 | token 追加不重复/乱序；停止后不再渲染新 token；citation 展示与 final payload 对齐；刷新后可恢复已完成会话。 |
| 风险/回滚 | SSE 降级仅为错误提示，不将未完成回答伪装为已完成。 |

### M2 并行流：RAG 深度增强与可量化优化

#### RAG-01｜建立增强前评测基线与数据治理

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Quality / P0 / 5 人日 |
| 主责 | RAG；协作 QE |
| 依赖 | 无，可与 M0 并行 |
| 目标 | 任何 DeepDoc、AST 或检索算法变更都可与当前基线做可重复比较。 |
| 范围 | 建立授权的 Markdown/PDF/代码语料、问题–evidence 标注、解析 fidelity 指标、Recall@K、MRR、citation/locator accuracy、延迟和索引成本记录。 |
| 不包含 | 以 LLM 主观评分替代 gold 标注；把真实用户文档提交到仓库。 |
| 交付物 | 版本化 fixture manifest、评测脚本、阈值、结果 JSON/Markdown 报告格式。 |
| 验收 | 在固定模型/配置下至少重复运行两次结果可比；报告能按 parser/chunker/model/index version 归因；敏感样本不进入 Git。 |
| 风险/回滚 | 评测数据走受控存储和匿名化；没有达标数据不推进算法替换。 |

#### RAG-02｜DeepDoc/复杂文档解析技术 Spike

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Spike / P0 / 6 人日 |
| 主责 | RAG |
| 依赖 | RAG-01 |
| 目标 | 对“DeepDocs”明确为可落地方案：例如 DeepDoc/RAGFlow 风格的版面、标题层级、表格、图片/OCR 理解；完成 license、运行成本、精度与安全评估。 |
| 范围 | 对至少两种候选方案做隔离 PoC；以现有 `Parser` 的 `ParsedSegment(text, locator, metadata)` 输出做适配实验；记录 CPU/GPU/依赖、页码/表格 locator 和失败模式。 |
| 不包含 | 未经许可证审查复制外部项目源码；未达标即替换默认 PDF parser。 |
| 交付物 | ADR、PoC、基线对比报告、推荐/不推荐结论和正式任务拆解。 |
| 验收 | 在固定复杂 PDF 集上量化标题/表格/locator fidelity、Recall@K、P95 延迟；结论包含许可与运维风险；保留当前 parser 作为 fallback。 |
| 风险/回滚 | 该任务是决策门，不承诺合入第三方实现；未通过即结束为“保持当前实现”。 |

#### RAG-03｜结构化文档解析与语义切块

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P1 / 10 人日 |
| 主责 | RAG |
| 依赖 | RAG-01、RAG-02 通过决策门 |
| 目标 | 将 PDF/Office/HTML 等复杂文档解析为含标题路径、页/坐标/表格来源的可追溯语义段，再按结构切块。 |
| 范围 | 新 Parser adapter、metadata schema、结构优先 chunker、fallback、解析/切块 golden tests、索引重建与配置 digest 版本化。 |
| 不包含 | 改写 domain 状态机、丢失现有 locator、将 OCR 内容当作无来源纯文本。 |
| 交付物 | 解析器与 chunker、fixture、迁移/重建说明、评测报告和 feature flag。 |
| 验收 | 所有 chunk 保留稳定 ordinal、source locator 与 `content_sha256`；失败回退有明确错误码；相对基线在预注册指标上达到目标且无非预期 Recall 回归。 |
| 风险/回滚 | parser/chunker/model 配置纳入 config digest，触发新 `index_version`；关闭 flag 后新上传恢复默认 parser，旧索引保持可读。 |

#### RAG-04｜代码 AST 解析与语义切块

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P1 / 9 人日 |
| 主责 | RAG |
| 依赖 | RAG-01 |
| 目标 | 以模块、类、函数、方法、注释/文档串为边界建立代码检索语料，支持跨 chunk 的符号定位。 |
| 范围 | 语言优先级选型（建议 Python/Go/TypeScript）、AST parser adapter、语法失败 fallback、symbol/import/path metadata、AST-aware chunker、代码问答 eval。 |
| 不包含 | 执行用户代码、动态分析、任意语言“一次全支持”、在 chunk 内丢失文件路径/行号。 |
| 交付物 | AST parser adapters、fixture repository、golden locator tests、代码检索评测、支持矩阵与错误码文档。 |
| 验收 | 函数/方法命中可回链至正确文件与行区间；语法错误文件以受控 fallback 处理；跨语言支持仅在各自 golden/eval 通过后开放。 |
| 风险/回滚 | 第三方 tree-sitter/解析器版本固定并做供应链审查；AST metadata 变更触发新 index version。 |

#### RAG-05｜检索链路优化与消融验收

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Feature / P1 / 8 人日 |
| 主责 | RAG；协作 QE |
| 依赖 | RAG-01；可并行消费 RAG-03/04 输出 |
| 目标 | 在不破坏可解释性与租户隔离的前提下优化 hybrid retrieval、metadata filter、rerank 和 context budget。 |
| 范围 | 候选池/权重/阈值实验、按文档类型策略、去重/相邻 chunk 合并、rerank 预算、消融报告与默认配置决策。 |
| 不包含 | 移除 BM25、绕过 MySQL active-version 复核、将模型分数当作授权依据。 |
| 交付物 | 配置化策略、单元/contract/eval tests、消融报告、上线阈值和回滚配置。 |
| 验收 | 预注册主指标达到门槛，P95 延迟/成本不超过预算；每个返回 evidence 保留 score、source、locator；删除和 version fence 测试仍全部通过。 |
| 风险/回滚 | 参数以 settings/feature flag 管理；默认值切换可回滚且不会重写既有索引。 |

### M3：系统质量、发布与运营闭环

#### QE-01｜跨端 E2E、可靠性与安全验收

| 字段 | 内容 |
|---|---|
| 类型 / 优先级 / 预估 | Release / P0 / 10 人日 |
| 主责 | QE/PLAT；协作全员 |
| 依赖 | CP-04、WEB-02、AG-03、WEB-03；RAG 工作包按选定范围纳入 |
| 目标 | 证明公网只经 Go、Python 状态语义未被破坏、Chat citations 真实可追溯，并让整套系统可重复发布。 |
| 范围 | Compose E2E、跨语言 proto compatibility、上传→摄取→Job→Chat、SSE 断连、Agent tool 越权、多个 Go API/Python Worker 实例、deadline、NATS redelivery、权限/secret 扫描。 |
| 不包含 | Kubernetes/Helm 高可用；其应作为下一迭代独立计划。 |
| 交付物 | CI matrix、测试报告、性能基线、运行手册、告警/仪表盘最小集、发布/回滚 checklist。 |
| 验收 | Go 不直写 RAG 表的架构测试通过；SSE/citation/tenant isolation E2E 通过；多实例下没有重复成功/索引回退；`make ci`、Go、Web、Docker suites 均有明确实跑证据。 |
| 风险/回滚 | 采用 profile/feature flag 分阶段开放 API、Chat、DeepDoc/AST；生产开关关闭时仍保留已验收 Python RAG 服务。 |

## 五、建议排期与阶段门

| 周期 | 并行任务 | 阶段门 |
|---|---|---|
| 第 1–2 周 | CP-01、CP-02、WEB-01、RAG-01、RAG-02 | Go 能调用 Fake/真实 Python gRPC；前端可构建；DeepDoc 给出 ADR 结论 |
| 第 3–4 周 | CP-03、CP-04、WEB-02、RAG-04 | 租户隔离和上传/Job E2E 通过；代码 AST golden/eval 达标 |
| 第 5–6 周 | AG-01、AG-02、AG-03、WEB-03、RAG-03 或 RAG-05 | 受控 Retrieve Tool 与 SSE/citation 端到端闭环；复杂文档方案通过质量门才进入实现 |
| 第 7–8 周 | QE-01、RAG-05、缺陷修复、文档与运行演练 | 发布候选：安全、可靠性、评测、可观测性全部有证据 |

## 六、必须先决策的产品问题

这些问题会改变范围，应在 CP-03、AG-01、RAG-02 开始前由产品/架构评审明确，不能由执行人自行假设：

1. 身份提供方：内部账号、OIDC（例如 Keycloak/Auth0）还是企业 SSO；是否第一期支持多租户。
2. Chat Model 供应商、数据保留策略、速率/成本预算与内容安全策略。
3. Agent 首期是否只有 `retrieve_knowledge` 一个 Tool；Web/SQL/MCP 是否明确排除。
4. “DeepDocs”具体指 DeepDoc/RAGFlow 方向、某个外部产品，还是泛指复杂文档理解；是否允许 GPU/OCR 与相应许可证。
5. AST 首期支持语言顺序（建议 Python、Go、TypeScript），以及是否允许解析不受信任仓库。
6. 面向用户的第一期范围：内部管理台、团队知识库，或具有正式外部用户与计费的 SaaS。

## 七、完成定义（Definition of Done）

- 代码、接口契约、生成物、测试、`tests/TEST.md`（如测试结构变更）和运维/用户文档在同一工作包完成。
- 每项任务都有自动化验收；没有实跑的检查必须在交接记录中标为“未运行”。
- 任何跨 Python/Go 的改动必须保留 gRPC 兼容性，且不绕过 Python 的状态机/Outbox/fence。
- 任何算法优化必须附带基线与消融结果，满足预注册质量、延迟和成本门槛后才能作为默认策略。
- 每个可独立验收工作包以单独 Conventional Commit 提交；不自动 `git push`。
