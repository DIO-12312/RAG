# OpenTelemetry/Prometheus 管理员观测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先完成跨 Go/Python 的脱敏 Trace、Metric 和仅系统管理员可访问的产品内观测仪表盘；Ragas 评测另行排期。

**Architecture:** Go API、Python gRPC Server、Worker、Outbox 各自用 OpenTelemetry SDK 输出数据，经私网 Collector 将 Metric 送往 Prometheus、Trace 送往 Tempo。Go API 执行管理员授权并查询后端，Vue 只调用受限只读 Go API；保留 JSON 日志、MySQL 权威状态和 Python gRPC 业务边界。

**Tech Stack:** Python 3.12、Go 1.26、Vue、OpenTelemetry SDK/Collector、Prometheus、Tempo、gRPC、Docker Compose。

**Spec:** `SPEC.md`（§1、§2.2、§2.3、§2.5、§3.1、§4、§5.7）。当前规格先约定 JSON 日志和未来 trace context；Task 1--6 必须同步修订对应章节后才能验收。

## Global Constraints

- Python 只提供版本化 gRPC 业务接口；OTLP 仅为出站私网观测流量，不新增 Python HTTP/FastAPI adapter。
- Go 拥有产品认证、管理员授权、受限查询与前端 API；MySQL Job/Task/Document 仍为权威业务状态。
- 管理员按当前单租户系统采用全站 `admin`/`user` 两级角色；普通注册不得提权，离线命令授予/撤销，Go 每次请求复核角色。
- NATS 消息继续只传 `task_id`；同步 Go→Python gRPC 传播 W3C `traceparent`；Worker 为独立 Trace，以 `task_id/job_id` 关联。
- Prometheus、Tempo、Collector 仅私网可达。浏览器不直连，Go 不接受任意 PromQL、TraceQL、URL 或高基数标签。
- 不采集问题/Prompt/Evidence/凭据/私有推理；ID 仅在脱敏 Span/日志中，Metric 标签限枚举。观测后端故障不改变回答、SSE、ACK/NAK 或状态机。
- Metric 与 Trace 分别配置容量预算、独立持久卷和宿主机预留空间；容量治理属于观测部署，不由 Go/Python 业务进程扫描或删除后端数据文件。容量不足时允许丢失旧观测数据或暂停新 Trace 摄取，但不能影响业务。
- 每个 Task 先完成实现、配置、必要文档及 `SPEC.md`，最后调整对应测试并运行验证。新增/移动测试同步 `tests/TEST.md`；修改 Compose/Make/Earthfile 运行 `tests/contract/test_build_entrypoints.py`。
- 已验收计划的小模块验证后各自单独提交；提交前检查 `git status`，只暂存本模块文件，不推送。

## Review Focus

- Go→Python 同步 Span 必须同 trace 且父子关系正确；Worker 不能伪装为跨队列连续 Span；Task 2--3。
- 采集与查询不得暴露问题、Prompt、Evidence、密钥或推理内容；Task 1--3、5--6。
- Collector/Prometheus/Tempo 故障不影响业务；摄取失败、redelivery、取消仍保留终态语义；Task 2--3、5。
- 超过观测存储预算时优先淘汰最旧的完整数据块；清理是异步的，须覆盖 WAL、压缩临时空间和清理滞后，不能把保留时长或数据块上限误称为宿主磁盘硬配额；Task 1、1A。
- 普通用户、未登录或撤权管理员不能查 Metric/Trace，前端隐藏菜单不能代替服务端授权；Task 4--6。
- 查询超时、空结果和部分后端失败必须显示真实状态；拒绝任意查询及高基数标签；Task 5--6。

---

## 交付范围和执行顺序

本计划有七个观测工作包：Task 1 私网管道，Task 1A 容量控制，Task 2 Python 埋点，Task 3 Go 埋点和跨语言传播，Task 4 角色，Task 5 只读 API，Task 6 仪表盘。Task 1A、2--3 依赖 Task 1；Task 5 依赖 Task 1/4；Task 6 依赖 Task 1A/5。Ragas 的原 Task 1--3 不在本轮实施、验收或提交范围。

### 前置修复 P0：恢复既有格式门禁

实施时发现当前主线 7 个 `src/rag_mvp/ports/*.py` 文件的中文注释混用 LF/CRLF，未改动的 `HEAD` 内容即被 `ruff format --check` 拒绝。该基线问题阻断 `make ci`，因此先作为独立的小模块统一其行尾，范围仅限这 7 个文件。最后运行 `uv run ruff format --check src/rag_mvp/ports`、`uv run pytest tests/contract/test_container_artifacts.py -q` 和 `make ci`；门禁通过后检查 `git status`，只暂存这 7 个文件，提交 `style(ports): 修复既有格式门禁`。若还有其他基线失败，先定位而不将无关改动并入观测提交。

### Task 1: 私网 Collector、Prometheus 与 Tempo 管道

**Files:** Create `deploy/observability/{collector,prometheus,tempo}.yaml`；Modify `docker-compose.yml`、`compose.product.yml`、`compose.production.yml`、`docs/deployment-production.md`、`SPEC.md`、`tests/contract/test_container_artifacts.py`、`tests/contract/test_build_entrypoints.py`、`tests/TEST.md`。

**Interfaces:** 四个应用进程向 `otel-collector:4318` 发送 OTLP/HTTP；Collector 在私网 `:9464/metrics` 暴露聚合指标供 Prometheus 抓取，并向 Tempo 发送 Trace；仅 Go API 可查询私网 `prometheus:9090` 和 `tempo:3200`。

- [ ] **Step 1: 配置观测管道。** Collector 配 OTLP receiver、`memory_limiter → attributes/redact → batch` processors、Prometheus exporter 和 Tempo OTLP exporter。脱敏清除请求头、`url.query`、`db.query.text`、`gen_ai.input.messages`、`gen_ai.output.messages`、正文、Prompt、Evidence 等属性；应用端也须先执行允许列表。Prometheus 保留最多 15 天并设置 `--storage.tsdb.retention.size`；容量与时间限制先到者生效，由 Prometheus 删除最旧的完整数据块。Tempo 保留最多 7 天并使用独立命名卷；Tempo 的容量控制由 Task 1A 验证和交付。容量数值按部署磁盘预算设定，不能仅依赖 15/7 天默认值；三个镜像锁定确定版本与来源校验，不使用 `latest`。
- [ ] **Step 2: 接入三套 Compose。** Go API、Python gRPC Server、Worker、Outbox 均获得私网 OTLP endpoint 和分别为 `rag-go-api`、`rag-python-server`、`rag-python-worker`、`rag-python-outbox` 的 `service.name`。Collector、Prometheus、Tempo 不发布宿主端口，Caddy 不代理观测后端；业务服务不得用观测后端的 healthcheck 作为启动前提。保持 Secret 隔离和持久卷保护。
- [ ] **Step 3: 更新规格和部署文档。** 明确 OTel、Prometheus、Tempo、JSON 日志的职责、网络边界、保留期、故障降级和备份/升级操作。
- [ ] **Step 4: 最后调整契约测试。** 覆盖三套 Compose 的服务、网络、无公网观测端口、卷及 Collector 脱敏；同步 `tests/TEST.md`。运行 `uv run pytest tests/contract/test_container_artifacts.py tests/contract/test_build_entrypoints.py -v`，再运行现有公开 Compose 配置验证入口；Docker/Secret 不可用则记为未运行。
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(observability): 建立私网观测管道`；报告实际运行与未运行的命令。**

### Task 1A: Metric/Trace 存储预算与旧数据淘汰

**Files:** Create `backend/go-api/cmd/observability-retention/main.go`、`backend/go-api/internal/retention/{controller.go,controller_test.go}`；Modify `backend/go-api/Dockerfile`、`deploy/observability/{collector,prometheus,tempo}.yaml`、`docker-compose.yml`、`compose.production.yml`、`deploy/production/boot-start.sh`、`.env.production.example`、`docs/deployment-production.md`、`SPEC.md`、`tests/contract/test_container_artifacts.py`、`tests/TEST.md`。

**Policy:** 部署时为 Prometheus 和 Tempo 分配各自的字节预算，并保留宿主机余量。统计各自数据卷实际占用，同时监控宿主文件系统剩余空间；仪表盘展示占用、预算、清理动作及 Trace 丢弃状态。Prometheus 使用原生时间/容量保留策略。Tempo 没有与 Prometheus 等价的容量保留开关；先验证所锁定版本的运行时保留期覆盖与后台清理机制，容量超阈值时缩短保留期，让 Tempo 自己按时间清理最旧的完整 Trace 数据块。业务程序、控制进程均不得直接 `rm` 后端文件或改写 Parquet/WAL。

- [ ] **Step 1: 定义容量契约和测量口径。** 以可配置的每卷预算为上限目标，预留至少 20% 给 WAL、压缩和清理滞后；记录数据卷总占用、可用空间、近 24 小时增长率。容量按高水位 80%、低水位 70%、紧急水位 90% 处理；预算和阈值在三套 Compose/部署文档中明示，不以宿主机总容量代替卷预算。
- [ ] **Step 2: 验证 Tempo 淘汰能力后实现独立控制。** 在确定版本的真实 Compose 中证明运行时缩短 retention 能触发 Tempo 自身删除最旧数据块、查询不报错，且恢复保留期不会让已删除 Trace 复活；控制进程只读取容量数据并通过受控运行时配置修改 retention，具备原子写入、单实例执行、重启恢复和最短保留期下限。Tempo 租户覆盖会替换摄取限制，写入 retention 时须同时固定非零速率/突发/单 Trace 上限并用真实 OTLP 验证。高水位逐级缩短到低水位或最短保留期；不能保证“刚超限立即删除”，须把后台清理延迟作为验收项。若当前版本或部署方式不能可靠动态调整，则先改用经验证、具容量回收能力的 Trace 后端或部署方式，并修订计划后实施，不以文件级删除充数。
- [ ] **Step 3: 设置紧急兜底。** 达到紧急水位、清理失效或宿主机剩余空间不足时，停止接收新的 Trace/提高采样丢弃率，保留业务和现有 JSON 日志；Metric 保留 Prometheus 原生回收能力。记录稳定错误状态并告警，容量恢复后自动恢复 Trace 摄取。Go/Python 不因该降级改变 RPC、Chat、摄取或状态机结果。
- [ ] **Step 4: 最后增加容量契约和真实后端验证。** 覆盖超限淘汰顺序、异步清理、WAL/压缩余量、控制进程重启、Tempo 查询完整性、紧急暂停与恢复、Prometheus 时间/容量策略、业务不受影响；更新 `tests/TEST.md`。运行对应控制进程测试、`uv run pytest tests/contract/test_container_artifacts.py tests/contract/test_build_entrypoints.py -v`、`make ci` 和真实 Docker 容量验收；不可用的验证明确列为未运行。
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(observability): 增加观测存储容量控制`；报告预算值、实际验证和未运行项。**

### Task 2: Python gRPC、摄取和检索埋点

**Files:** Create `src/rag_mvp/telemetry.py`、`tests/unit/test_telemetry.py`；Modify `pyproject.toml`、`uv.lock`、`src/rag_mvp/bootstrap/container.py`、`src/rag_mvp/rpc/server.py`、`src/rag_mvp/observability.py`、`src/rag_mvp/ingestion/pipeline.py`、`src/rag_mvp/ingestion/worker.py`、`src/rag_mvp/outbox/main.py`、`src/rag_mvp/application/retrieval_service.py`、`SPEC.md`、`tests/TEST.md`；按需调整既有 resilience 测试。

**Interfaces:** 接收 Go gRPC client 的 W3C `traceparent`；产生 server/retrieval/ingestion/outbox Span、有限标签的 counters/histograms；保留现有 JSON 日志并追加 `trace_id/span_id`。Worker 独立创建 Trace，仅靠 `task_id/job_id` 关联上传链路。

- [ ] **Step 1: 在 `telemetry.py` 封装 Python tracer/meter provider、OTLP exporter、W3C propagator、允许列表属性 helper 和有界 shutdown；由现有进程入口及 Container 初始化，不在 `domain/` 创建 SDK 依赖。Collector 不可用不能让业务启动或状态迁移失败。**
- [ ] **Step 2: 接入 grpc.aio server context；为 retrieval 的 dense/sparse、复核、rerank、evidence 及摄取的 object read、parse、chunk、embedding、index、complete/failed/cancelled 建立 Span。Outbox 仅观测 finalization、publish/retry，不消费 Task。redelivery 记录数值属性，不改 NATS 消息体。**
- [ ] **Step 3: 固定 `rag_ingestion_tasks_total{stage,outcome}`、`rag_ingestion_stage_duration_seconds{stage}`、`rag_retrieval_duration_seconds{stage}`、`rag_outbox_publish_total{outcome}` 的语义与单位；最终导出名称在契约测试中固定。ID、查询哈希和来源名称不进入 Metric 标签；Span 属性只允许 ID、阶段、计数、耗时和稳定错误码。同步更新 `SPEC.md`。**
- [ ] **Step 4: 最后添加内存 exporter 测试，覆盖各阶段、同 trace 的 gRPC server span、脱敏、故障 exporter、取消/redelivery/强杀恢复后的 ACK/NAK 和 Job 幂等性；更新 `tests/TEST.md`。运行 `uv run pytest tests/unit/test_telemetry.py tests/unit/test_observability.py tests/unit/ingestion -v`、`make ci`、`make docker-test SUITE=resilience`；若触及 adapter 另跑对应 integration/contract suite。**
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(ingestion): 接入脱敏 OpenTelemetry 观测`；报告实际验证与未运行项。**

### Task 3: Go Agent 埋点与同步 gRPC Trace 传播

**Files:** Create `backend/go-api/internal/telemetry/telemetry.go`、`backend/go-api/internal/telemetry/telemetry_test.go`；Modify `backend/go-api/go.mod`、`backend/go-api/go.sum`、`backend/go-api/cmd/api/main.go`、`backend/go-api/internal/ragclient/client.go`、`backend/go-api/internal/agent/observer.go`、`backend/go-api/internal/agent/runtime.go`、`backend/go-api/internal/httpapi/chat.go`、`backend/go-api/internal/agent/observer_test.go`、`backend/go-api/internal/httpapi/integration_test.go`、`SPEC.md`、`tests/TEST.md`。

**Interfaces:** Go Chat 根 Span 经 gRPC client 将 `traceparent` 注入现有 metadata；Python server Span 必须继承同一个 OTel `trace_id`。`run_id` 保持独立业务关联 ID，不强行充当 OTel trace ID。

- [ ] **Step 1: 在 `internal/telemetry` 初始化 Go tracer/meter、OTLP exporter、W3C propagator 及有界 shutdown；Collector 失败时保留 JSON 日志、Chat 和 SSE 业务语义。**
- [ ] **Step 2: 为 Route、Model、Tool、Assess、Rewrite、Finalize、Complete 建立子 Span；gRPC client 接入传播。保留 `JSONLogObserver` 的 run ID、错误码、终止原因，追加当前 trace/span ID；取消/失败只发一个终态业务事件。**
- [ ] **Step 3: 固定 `rag_chat_runs_total{outcome,stop_reason}`、`rag_chat_duration_seconds`、`rag_grpc_client_duration_seconds{method,outcome}`、`rag_agent_model_calls_total{phase}`；全部标签为枚举。更新 `SPEC.md` 的同步跨语言传播、`run_id`/`trace_id` 区别和脱敏边界。**
- [ ] **Step 4: 最后用内存 exporter 和真实 gRPC 连接测试同 trace 及 client/server 父子关系；覆盖取消、模型错误、证据不足、Observer/exporter 故障不影响回答/SSE/持久化，敏感内容不进入 Span/Metric；更新 `tests/TEST.md`。运行 `cd backend/go-api && go test ./internal/telemetry ./internal/agent ./internal/ragclient ./internal/httpapi`、`make ci` 和实际 Docker 跨语言验收；区分已运行和未运行。**
- [ ] **Step 5: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(agent): 接入跨语言 OpenTelemetry 问答观测`。**

### Task 4: 系统管理员角色和受控授予

**Files:** Create `backend/go-api/cmd/admin-role/main.go`、`backend/go-api/internal/storage/admin_role_test.go`；Modify `backend/go-api/internal/storage/schema.sql`、`backend/go-api/internal/storage/storage.go`、`backend/go-api/internal/httpapi/server.go`、`backend/go-api/internal/httpapi/server_test.go`、`apps/web/src/stores/auth.ts`、`apps/web/src/api/auth.ts`、`SPEC.md`、`docs/deployment-production.md`、`tests/TEST.md`。

**Interfaces:** `users.role` 为 `user|admin`，默认 `user`；`Store.UserRole(ctx,userID)` 每次从 DB 读取；`Store.SetUserRole(ctx,userID,role)` 只供离线 CLI；`GET /me` 返回当前角色。管理员路由使用 `authenticate + requireAdmin`，不得信任旧 Cookie/JWT 中的角色。

- [ ] **Step 1: 修改新库 schema 和 `Store.Migrate` 的幂等旧库迁移，旧用户一律为 `user`。CLI 只接受已存在 user ID 和目标角色，写脱敏操作审计；授予/撤销在事务和行锁下执行，拒绝并发撤销最后一名管理员。注册永远只创建普通用户，不提供在线自助提权。**
- [ ] **Step 2: 在 Go 服务端增加 `requireAdmin`，每次查当前 DB 角色，DB 不可用时拒绝；更新 `/me` 和前端 auth 类型。同步 `SPEC.md` 和运维首次授权/撤权说明。**
- [ ] **Step 3: 最后测试新库/旧库/重复迁移、注册默认角色、普通用户对受保护测试路由为 403、管理员可访问、撤权后旧 Cookie 立即 403、并发撤销最后管理员被拒及 DB 故障时拒绝；更新 `tests/TEST.md`。运行 `cd backend/go-api && go test ./internal/storage ./internal/httpapi ./cmd/admin-role` 和 `make ci`。**
- [ ] **Step 4: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(auth): 增加系统管理员观测权限`。**

### Task 5: Go 管理员只读查询 API

**Files:** Create `backend/go-api/internal/observability/query.go`、`backend/go-api/internal/observability/query_test.go`、`backend/go-api/internal/httpapi/observability.go`、`backend/go-api/internal/httpapi/observability_test.go`；Modify `backend/go-api/internal/httpapi/server.go`、`backend/go-api/cmd/api/main.go`、`compose.product.yml`、`compose.production.yml`、`SPEC.md`、`tests/TEST.md`。

**Interfaces:** 仅 Go API 使用固定 `PRODUCT_PROMETHEUS_URL`/`PRODUCT_TEMPO_URL`。管理员接口为 `GET /admin/observability/metrics?window=15m|1h|6h|24h`、`GET /admin/observability/traces?service=<固定枚举>&window=<固定枚举>`、`GET /admin/observability/traces/:trace_id`。前者返回预设图表序列，列表至多 100 条，详情只接受 32 位十六进制 ID 并返回脱敏 Span 树。

- [ ] **Step 1: 实现只读查询客户端。** 内置 PromQL 白名单（Go/Python 吞吐、错误率、p50/p95、摄取/Outbox 结果）；Tempo 只按四个固定 `service.name` 和时间窗搜索。每次查询 3 秒超时、1 MiB 响应上限、固定采样点数；不得接收任意 PromQL/TraceQL、URL 或 label key/value。服务端二次过滤 Span 属性，只返回 service/stage/duration/outcome/error_code 和允许的 run/job/task ID。
- [ ] **Step 2: 将全部路由放在 `authenticate + requireAdmin` 后；未登录 401、非管理员/撤权 403。空结果、单后端失败和查询超时返回独立状态与稳定错误码，不伪造零值、不回显内网 URL。Go 经私网访问后端，Caddy 仍不代理后端；同步 `SPEC.md`。**
- [ ] **Step 3: 最后用假 Prometheus/Tempo server 测试查询映射、恶意输入、非法 ID、空值、超时/超限、敏感属性过滤、403/撤权/后端失败；更新 `tests/TEST.md`。运行 `cd backend/go-api && go test ./internal/observability ./internal/httpapi`、`uv run pytest tests/contract/test_build_entrypoints.py -v` 和 `make ci`。**
- [ ] **Step 4: 检查 `git status`，只暂存本 Task 文件，单独提交 `feat(observability): 提供管理员只读指标与链路查询`。**

### Task 6: Vue 管理员仪表盘与真实链路验收

**Files:** Create `apps/web/src/views/ObservabilityView.vue`、`apps/web/src/api/observability.ts`、`apps/web/tests/observability.spec.ts`；Modify `apps/web/src/router/index.ts`、`apps/web/src/components/AppShell.vue`、`apps/web/src/stores/auth.ts`、`apps/web/src/api/contracts.ts`、`apps/web/src/mocks/handlers.ts`、`apps/web/src/i18n/messages.ts`、`docs/development/live-product-plane.md`、`tests/TEST.md`。

**Interfaces:** `/admin/observability` 只消费 Task 4 的 `/me.role` 和 Task 5 三个只读 Go API；浏览器不直接访问 Prometheus、Tempo 或 Collector。

- [ ] **Step 1: 仅向 admin 显示“观测”导航；管理员路由首次加载和刷新均复核 `/me`，不能用本地缓存自报角色。**
- [ ] **Step 2: 页面提供预设的吞吐、错误率、延迟、摄取/Outbox 结果图表，固定时间窗和服务筛选，Trace 列表与脱敏 Span 瀑布；分别显示空数据、部分后端不可用和权限撤销。无任意查询编辑器、无后端原生 UI iframe。**
- [ ] **Step 3: 更新产品文档、`tests/TEST.md`；最后编写前端测试，覆盖普通用户无入口且直访被拒、管理员可见、撤权后立即失权、空/错误状态和敏感字段不渲染。真实 Compose 中跑一次 Chat 与摄取，确认 Go/Python Metric 均存在、同步 gRPC 同 trace、Worker 可按 job/task 关联；停止观测服务验证业务正常且页面正确降级。**
- [ ] **Step 4: 运行 `cd apps/web && npm test -- --run`、`cd apps/web && npm run build`、`make ci` 和可用的真实 Docker 验收；检查 `git status`，只暂存本 Task 文件，单独提交 `feat(web): 增加管理员观测仪表盘`，报告实际运行与未运行项。**

## 验收与发布顺序

1. Task 1 后，Collector、Prometheus、Tempo 仅私网运行，Compose、Secret 和持久卷契约通过；Task 1A 后，两类数据均有可测量的容量预算，旧数据回收与紧急降级经过真实后端验证。
2. Task 2--3 后，Go→Python gRPC Span 同 trace，两个语言的预设 Metric 可查；Worker 用 job/task 关联独立 Trace，观测故障不影响业务。
3. Task 4 后，旧用户和新注册用户均为普通用户，离线授予/撤销管理员即时生效。
4. Task 5--6 后，管理员可在产品页查预设 Metric 和脱敏 Trace，普通/撤权用户不能查询；后端不可用显示真实状态。
5. 发布前执行 `make ci`、与改动相称的 Docker integration/resilience suite 和跨语言真实观测验收；未运行项逐一说明。

## 暂缓工作

Ragas Chat 金标、评测记录及真实 Judge Runner 保留在 `2026-09-22-ragas-evaluation-and-tracing.md` 的原 Task 1--3；本轮不修改其实现或评测门禁。观测完成后再独立审阅该部分，不让 Judge 凭据或成本进入默认 CI。
