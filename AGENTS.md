# RAG MVP 仓库指令

## 规格优先

- 实现、架构或接口变更前先阅读 `docs/SPEC.md`。
- 当实现与 `docs/SPEC.md` 不一致时，先指出差异；未经明确要求，不要悄悄改变既定架构。
- 改动架构、RPC 契约、状态机、存储或消息语义时，同步更新 `docs/SPEC.md` 和相关测试。

## 职责边界

- Python 是 RAG 计算服务：文档摄取、解析、规范化、切块、Embedding、Elasticsearch 检索、Rerank 与 evidence 返回。
- Go 是未来产品控制面：公网 API、认证、租户、会话、Chat Model 调用、Agent Harness 与 SSE。
- Python 的唯一接口是版本化 gRPC；本地调试同样必须调用 gRPC，不得新增 HTTP/FastAPI adapter。
- Python 不拥有 Agent Loop、Tool Calling、MCP、会话记忆策略或 SSE 对外协议。

## 技术与数据约束

- 元数据和 RAG 执行状态使用 MySQL 8/InnoDB。
- 检索使用 Elasticsearch 的 `dense_vector` KNN 和 BM25；不得重新引入 Qdrant、SQLite FTS5 或第二套搜索引擎。
- 异步任务使用 NATS JetStream；不得使用数据库 claim/lease 替代 JetStream 的 durable consumer、ACK、NAK、redelivery 语义。
- NATS 消息只传递 `task_id`，Worker 必须从 MySQL 读取 Task 和 Job 的真实状态。
- 创建任何待投递 Task 时，必须在同一个 MySQL 事务创建 OutboxEvent。依赖 staging object 的首个摄取 Task 使用 `WAITING_OBJECT`，由 `outbox/finalizer.py` 提升对象后条件更新为 `READY_TO_PUBLISH`；已有正式对象的重试、删除和清理 Task 可直接 READY。`outbox/relay.py` 只可重试地发布 READY 的 `task_id`，禁止应用服务直接采用“写 Task 后立即 publish”的双写方式。
- Job 是用户可查询的摄取聚合；Task 是 Worker 的最小调度单元。两者状态机均为 `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`。
- `FAILED` 是终态；`RetryJob` 必须创建带 `retry_of_job_id` 的新 Job/Task/OutboxEvent，不能将旧 Job 或 Task 改回 `PENDING`。删除必须创建 `DELETE_DOCUMENT` Job 与 `CLEANUP_DOCUMENT` Task。
- `RetryJob` 必须在原 FAILED Job 行锁下限制一个活跃子 Job，并受 `max_user_retries` 约束；并发调用只能返回同一子 Job。
- `CancelJob` 仅支持摄取 Job：PENDING 时撤销未发布 Outbox，RUNNING 时只设置 cancel 请求并由 Worker checkpoint 收敛；不得取消删除 Job，也不得让已取消摄取切换 `active_version`。
- `DeleteDocument` 必须在 Document 行锁内设为 `DELETED` 并递增 `lifecycle_generation`。Worker 认领/完成均须条件验证 Task、cancel 状态和 generation fence；失配时只能取消并创建 `CLEANUP_INDEX_VERSION` 系统 Job，绝不能重新激活 Document。
- Delete 同时必须将该 Document 的 IngestionFingerprint 置为 `RELEASED`、取消所有未终态摄取 Task 与未发布 Outbox；Finalizer 仅可在 Outbox=WAITING 且 Document 未删除时置 READY，条件失配后必须补偿删除刚提升的正式对象。
- `chunk_id` 必须遵循 `docs/SPEC.md` 中的 RAGFlow xxHash64 规则。ES 物理 `_id` 必须包含 `document_id`、`index_version` 和 `chunk_id`，以保留新旧索引版本。
- 删除先在 MySQL 中逻辑删除并立刻使文档不可检索；ES 和对象文件由可重试清理 Task 异步清除。
- 上传字节先写入由 `idempotency_key` 派生的 staging object；Finalizer 成功后才提升为正式对象并解锁 Outbox 发布。未被 MySQL 引用的中断/失败 staging object 必须由 TTL sweeper 清理；不得清理 `WAITING_OBJECT` 所引用的对象，不能假设对象存储与 MySQL 有跨库事务。
- 新文档上传在 MySQL 事务内以唯一 `(dataset_id, file_sha256, config_digest)` 的 `IngestionFingerprint` 锁定 canonical Job；并发同内容上传必须复用而不是创建第二个摄取，未被选中的 staging object 立即清理。`FAILED_RETRYABLE` 必须返回 canonical Job，只有 `RELEASED` 可重新占用创建新 Document。
- Finalizer 超过 `max_finalize_attempts` 必须把关联 Task/Job 标记为 `FAILED(OBJECT_FINALIZATION_FAILED)` 并取消 Outbox，不能让 Job 永久 PENDING；这类没有正式对象的失败不可 RetryJob，只能以新幂等键重新上传。

## 代码组织

- `domain/` 只能放领域模型、状态转换和纯规则，禁止依赖 gRPC、MySQL、NATS 或 Elasticsearch SDK。
- `application/` 编排用例，只依赖 `ports/` 中的抽象。
- 具体 SDK 调用只放在 `adapters/`；按能力目录组织实现，例如 `adapters/search_engine/elasticsearch.py`。
- `rpc/rag_service.py` 只完成 protobuf DTO 与 application DTO 的转换和服务调用；不得直接导入或调用 `adapters/`。
- `dev/cli.py` 只能作为 generated gRPC client；不得直接调用 `application/` 或 `adapters/`。
- `ingestion/worker.py` 是唯一消费 NATS 消息并执行 ACK/NAK 的位置；它调用 `application/ingestion_service.py`，后者只执行一个 Task、状态转换和 pipeline，不自行消费消息。
- `Task.last_delivery_sequence` 是 Worker 条件认领与 attempt 去重依据；收到已取消或无法认领的 delivery 只能 ACK，不能执行 pipeline。
- `outbox/main.py` 是 Object Finalizer + Relay 的独立进程入口；`outbox/finalizer.py` 只处理 staging object 提升与 Outbox 就绪；`outbox/relay.py` 只读取 READY OutboxEvent 并至少一次发布 `task_id`。两者都不得消费、ACK/NAK 或执行 Task。
- `application/retrieval_service.py` 调用 SearchEngine 返回 Dense/Sparse candidates、调用 MetadataRepository 复核 active version/删除状态，再调用 `retrieval/` 中的纯算法。
- `retrieval/hybrid.py` 是 Dense/BM25 候选 RRF 融合和稳定排序的唯一位置；SearchEngine adapter 只返回各路候选，不重复融合。
- `retrieval/rerank.py` 必须是纯排序函数；`application/retrieval_service.py` 调用 `ModelGateway.rerank` 后将分数传入，不能由该模块调用 SDK。
- `retrieval/provenance.py` 只规范化 evidence 的来源定位；最终 `[n]` Citation 编号和 Prompt 由 Go 生成。
- `bootstrap/container.py` 是唯一创建 concrete adapter 并装配 server/service/worker 的位置。
- protobuf 是 Python/Go 的唯一 RPC 契约来源；修改 `.proto` 时必须更新两端生成代码和契约测试。
- 本地手工调试使用 generated gRPC client、`grpcurl`、`grpcui` 或 `dev` CLI；Server Reflection 只能在开发环境启用。

## 可靠性与测试

- 新的领域规则、状态迁移、ID 或 digest 规则必须有 unit test。
- 新增、删除、移动或重命名 `tests/` 下的测试文件、`test_*` 函数、fixture、marker 或 Fake port 时，必须在同一改动中同步更新 `tests/TEST.md` 的目录树、测试职责表和运行边界；未同步更新不视为测试工作完成。
- 修改 MySQL、Elasticsearch、NATS adapter 时必须增加或运行对应 integration/contract test。
- 修改 Worker、ACK/NAK、重试、取消或版本切换时必须运行 resilience 测试，覆盖 redelivery、强杀恢复和幂等性。
- 修改 Task 创建、Outbox Relay 或 NATS 发布确认时，必须覆盖“Relay 发布成功后进程崩溃、标记 OutboxEvent 前重启”的重复投递场景。
- 修改上传/Finalizer、取消或版本分配时，必须覆盖“正式对象未就绪不发布”“取消不切换 active version”“并发重建版本唯一、失败版本清理”场景。
- 修改删除、Worker 条件完成或 RetryJob 时，必须覆盖“删除与 ingest 并发不复活 Document”“已发布 delivery 的取消竞态”“并发重试只创建一个子 Job”。
- 修改 Finalizer 或去重时，必须覆盖“删除与 Finalizer 并发不留孤儿对象/READY Outbox”“并发相同文件只生成一个 fingerprint/canonical Job”。
- 不将 LLM 自由文本作为逐字 snapshot；检索质量使用固定 fixture、Recall@K、MRR 和 citation/evidence 指标验证。
- 未运行测试时，不得声称测试通过；应明确说明已运行与未运行的验证项。

## 安全与协作

- 不提交 `.env`、API Key、令牌、真实用户数据、`data/`、缓存或日志。
- 不复制未授权项目的源码或文档内容；可以借鉴设计思想。复制 Apache-2.0 来源代码时必须保留所需的许可证、版权和 NOTICE 信息。
- 保留用户已有的未相关改动；不要执行 `git reset --hard`、强制覆盖或大范围删除。
- 提交前检查 `git status`，并说明提交包含的文件和验证结果。

## Git Commit 约定

- 默认情况下，除非用户明确要求，不要自行执行 `git commit`；任何时候执行 `git push` 都必须再次取得用户明确授权。
- **Phase 小模块提交例外：** 执行已经验收的详细实施计划时，每完成一个可独立验收的小模块并通过与其改动相称的检查后，必须立即自行执行一次 `git commit`，无需再次询问用户。这里的“小模块”以已验收计划中的工作包为准，例如 `A1`、`A2` 或 `P0-1`、`P0-2`；不是单个文件、单条命令或尚未形成可验证闭环的中间步骤。
- 不得把多个已经分别完成的小模块积攒到同一个提交。若一个工作包过大，详细实施计划必须先把它拆成可独立理解、回滚和验证的子模块，再开始实现。
- 小模块只有在实现、对应测试、必要生成物以及应同步更新的 `docs/SPEC.md`/计划文档全部完成后才算完成。存在失败的必跑检查、未解决的规格差异或尚未收敛的中间状态时，不得为了满足提交频率而提前提交。
- 提交前必须检查 `git status`，只暂存当前小模块拥有的文件，保留用户和其他工作的未相关改动；提交后在进度更新或交接消息中报告 commit hash、提交包含的模块以及实际运行和未运行的验证项。
- 使用 Conventional Commits 格式：`<type>(<scope>): <简短中文描述>`；没有明确 scope 时使用 `<type>: <简短中文描述>`。
- 一个提交只表达一个可独立理解、回滚和验证的目的；不要把功能、重构、格式化和无关文档混在同一提交中。
- `scope` 使用受影响的模块，例如 `rpc`、`ingestion`、`retrieval`、`queue`、`search`、`mysql`、`spec`、`deps`。

| type | 使用条件 | 本仓库示例 |
|---|---|---|
| `feat` | 新增调用方或业务可使用的能力 | `feat(retrieval): 支持 ES 稠密与 BM25 混合召回` |
| `fix` | 修复可复现的错误行为、竞态或异常 | `fix(queue): 修复 ACK 丢失后的重复计数` |
| `docs` | 仅改文档、注释或说明 | `docs(spec): 明确 Job 与 Task 的调度边界` |
| `refactor` | 只调整内部结构，外部行为不变 | `refactor(ingestion): 拆分任务状态迁移逻辑` |
| `perf` | 外部行为不变的性能优化 | `perf(search): 批量写入 ES chunk 文档` |
| `test` | 仅新增、调整或修复测试 | `test(queue): 覆盖 JetStream redelivery 场景` |
| `build` | 构建、依赖、代码生成或打包改动 | `build(deps): 升级 grpcio 依赖` |
| `ci` | CI、质量门禁或发布自动化改动 | `ci: 增加集成测试服务启动步骤` |
| `style` | 仅格式化，不改变逻辑 | `style: 执行 Ruff 格式化` |
| `chore` | 不属于以上类别的日常维护 | `chore: 更新 .gitignore` |
| `revert` | 回滚已有提交 | `revert: feat(retrieval): 支持 ES 稠密与 BM25 混合召回` |

- 选择顺序：新增能力用 `feat`；修复明确错误用 `fix`；只改性能用 `perf`；只改内部结构用 `refactor`；只改测试用 `test`；只改文档用 `docs`；再考虑 `build`、`ci`、`style` 或 `chore`。
- 提交前运行与改动相称的检查；提交说明或交接消息必须列出实际运行的命令和未运行项目。
