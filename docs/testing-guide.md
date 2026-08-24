# RAG MVP 功能测试与验证指南

本文说明如何在当前 Windows 本机环境验证 Python RAG 服务的功能、可靠性规则、真实基础设施与检索质量。快速门禁会走真实的 gRPC、application、Outbox、Worker、pipeline 和 retrieval 调用链，但以测试专用 Fake 保持确定性；`integration` 与 Compose 验收则使用真实 MySQL、Elasticsearch、NATS JetStream 和模型服务。

报告结果时必须区分 Mock Functional/Resilience、真实 adapter integration、真实模型、Docker E2E 和 Docker Resilience。当前真实 adapter、Compose 拓扑与四格式 gRPC E2E 已可验证；容器强杀恢复需等对应测试任务完成后才能声明通过。

## 1. 先准备环境

在仓库根目录执行：

```powershell
uv sync --frozen --group dev
uv run python scripts/check_generated.py
```

要求为 Python 3.12+ 和 `uv`。测试不需要 Docker，也不需要配置真实模型密钥。`.env` 只在需要启动本地进程时复制和填写，不能提交。

如果 protobuf 检查提示生成物落后，先重新生成，再复查：

```powershell
uv run python scripts/generate_proto.py
uv run python scripts/check_generated.py
git diff -- src/rag_mvp/rpc/generated
```

生成目录只能由脚本更新；确认 diff 合理后再纳入相应改动。

## 2. 推荐的日常验证顺序

开发一个小模块后，按下面顺序运行与改动相称的检查；准备提交或需要完整本机验证时，执行全部命令。

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract tests/functional
uv run pytest -m resilience tests/resilience
uv run pytest -m eval tests/eval
uv run pytest --cov=rag_mvp.domain --cov=rag_mvp.application --cov=rag_mvp.ingestion --cov=rag_mvp.retrieval --cov-fail-under=85 tests/unit tests/contract tests/functional tests/resilience tests/eval
```

最后一条会再次执行测试以计算聚合覆盖率，这是正常的。通过条件是全部命令退出码为 0，且核心覆盖率不低于 85%。当前固定评测集的门槛为 Recall@6 ≥ 0.85、MRR@6 ≥ 0.70、locator accuracy = 1.0。

可启用与 CI 相同的本地提交门禁：

```powershell
git config core.hooksPath .githooks
```

该 hook 与 GitHub Actions 的 Python 质量命令保持一致。不要用 `--no-verify` 代替排查失败原因。

## 3. 按功能验证

下面的命令适合定位某一项功能。附加 `-vv -s` 可以显示更完整的测试名称和输出。

| 要验证的行为 | 执行命令 | 覆盖重点 |
| --- | --- | --- |
| 上传、Finalizer、Relay、Worker、检索主链 | `uv run pytest -vv tests/functional/test_mock_upload_ingest_retrieve.py` | TXT 从 upload 到 evidence 的异步闭环 |
| 四类文件 | `uv run pytest -vv tests/functional/test_mock_four_formats.py` | TXT、Markdown、Python、文本 PDF 的解析、切块和检索 |
| 幂等与重复投递 | `uv run pytest -vv tests/functional/test_mock_dedup_and_redelivery.py` | idempotency、fingerprint、redelivery、索引幂等 |
| RetryJob | `uv run pytest -vv tests/functional/test_mock_retry_job.py` | 失败任务创建新的 Job/Task/Outbox，不复活旧 Job |
| CancelJob | `uv run pytest -vv tests/functional/test_mock_cancel_job.py` | PENDING 撤销和 RUNNING checkpoint 收敛 |
| DeleteDocument | `uv run pytest -vv tests/functional/test_mock_delete_document.py` | 立即不可检索及异步清理 |
| 混合检索算法 | `uv run pytest -vv tests/unit/retrieval/test_hybrid.py tests/unit/retrieval/test_rerank.py tests/unit/retrieval/test_context_builder.py` | Dense/Sparse 候选、RRF、重排降级与 token budget |
| 四类解析器和稳定切块 | `uv run pytest -vv tests/unit/ingestion/test_multiformat_parsers.py tests/unit/ingestion/test_recursive_chunker.py` | locator、chunk 边界和稳定 ID |
| gRPC 与 Port 契约 | `uv run pytest -vv tests/contract` | proto、RPC DTO、Repository/Search/Queue/Model 等端口语义 |

无 Docker 的功能回归权威入口是 `tests/functional/`。生产 bootstrap 不导入测试 Fake ports；完整 Docker 服务启动后，`rag-dev` 可通过 generated gRPC client 执行 Dataset 创建、流式上传、Job 查询/重试/取消、检索和删除。

## 4. 验证可靠性规则

运行全部 Mock Reliability 场景：

```powershell
uv run pytest -vv -m resilience tests/resilience
```

重点文件如下：

| 文件 | 验证内容 |
| --- | --- |
| `test_finalizer_recovery.py` | staging 恢复、Finalizer 终态和 Relay 投递窗口 |
| `test_concurrent_uniqueness.py` | 并发内容去重与 Retry 子 Job 唯一性 |
| `test_cancel_races.py` | 已发布 delivery 与取消之间的竞态 |
| `test_generation_fences.py` | 删除与索引写入并发时不复活 Document，清理不可见版本 |
| `test_redelivery_idempotency.py` | ACK 丢失/重复 delivery 时只得到一次有效结果 |
| `test_spec_invariant_matrix.py` | SPEC T1～T25 与测试证据的映射完整性 |

`tests/fixtures/reliability_matrix.json` 记录每个 T1～T25 不变量的 Mock 证据，以及是否仍需真实基础设施复验。出现此类测试失败时，不要只看最终 Job 状态；应同时检查 Task、Outbox、Document generation、IndexBuild 和 FakeQueue 的 ACK/NAK 记录。

## 5. 验证检索质量

```powershell
uv run pytest -vv -m eval tests/eval
```

评测集位于 `tests/eval/fixtures/retrieval_quality.json`，包含固定问题、相关 chunk 和预期 locator。它验证的是可重复的检索质量：Recall@6、MRR@6 和 evidence 来源定位；不对 LLM 自由文本答案做逐字 snapshot。

当指标下降时，先运行相应的 `tests/unit/retrieval/` 测试，再检查 fixture 是否被有意更新。不能为了让门禁通过而随意降低阈值或修改相关 chunk 标注；这类变化应有数据依据并同步更新规格或计划。

## 6. 常见失败处理

| 现象 | 建议处理 |
| --- | --- |
| `uv sync --frozen` 失败 | 检查 Python 版本是否在 3.12～3.14；不要手工改 lockfile 后跳过同步。 |
| `check_generated.py` 失败 | 使用 `generate_proto.py` 重新生成，检查 `.proto` 与生成物是否一起更新。 |
| Ruff 或 mypy 失败 | 先修复源代码；格式化可使用 `uv run ruff format src tests scripts`，随后再运行 `--check`。 |
| functional 失败 | 用 `-vv -s` 重跑单个测试，按 upload → Finalizer → Relay → Worker → Retrieve 顺序定位。 |
| resilience 失败 | 重点比较 Task/Job 终态、Outbox 是否撤销、generation 是否匹配，以及重复 delivery 是否被 ACK。 |
| coverage 低于 85% | 为新增分支补充 unit/functional 测试，不应直接降低 `--cov-fail-under`。 |

## 7. 真实基础设施与 Compose 验证

`.env` 必须包含真实 Embedding provider 的 URL、模型名、API Key 和声明维度。禁止运行会输出渲染配置的 `docker compose config`；只使用 `--quiet`：

```powershell
docker compose config --quiet
docker compose build rag-server rag-worker rag-outbox
docker compose up -d mysql elasticsearch nats
docker compose run --rm rag-migrate
docker compose up -d rag-server rag-worker rag-outbox
uv run python scripts/docker_healthcheck.py
```

运行真实 adapter 与真实模型测试：

```powershell
uv run pytest -m "integration and not model_integration" tests/integration
uv run pytest -m model_integration tests/integration/test_real_embedding_model.py
```

运行只通过 generated gRPC client 驱动的四格式真实 E2E：

```powershell
uv run python scripts/build_test_fixtures.py --check
docker compose --profile test run --rm rag-test uv run pytest -m e2e tests/e2e/test_real_upload_ingest_retrieve.py -q
```

该测试分别上传 TXT、Markdown、Python 和 PDF，等待异步 Job/Task 终态，再验证真实 Embedding、ES Dense/BM25 候选、RRF evidence、active index version 和 line/symbol/language/page provenance。测试每次生成新的 request/idempotency key；重复运行后还应只读核对 MySQL 没有遗留 PENDING/RUNNING 状态、ES 记录数与 ChunkManifest 一致、NATS consumer 没有 pending 或 ack pending。

验证测试目标、runtime 内容和日志密钥脱敏：

```powershell
docker compose --profile test build rag-test
docker compose --profile test run --rm rag-test uv run pytest tests/contract/test_container_artifacts.py
docker compose run --rm --no-deps rag-server sh -c 'test ! -e /app/tests && test ! -e /app/.env && test "$(id -u)" -ne 0'
docker compose logs --no-color 2>&1 | uv run python scripts/check_secret_leaks.py
```

以上结果证明真实 adapters、迁移顺序、角色装配、容器安全边界和四格式 gRPC RAG 闭环；它仍不替代 Task 14 的 Worker/Relay 强杀恢复。不要在恢复测试中使用 `docker compose down -v`，它会删除应被验证的持久状态。

## 8. 记录验证结果的模板

每次完成一个可验收模块，可在提交说明或工作记录中采用以下格式：

```text
验证范围：<功能或模块>
已运行：<实际命令及结果>
指标：<覆盖率或 Eval 指标；不适用则写不适用>
未运行：<真实基础设施、Docker KILL 等原因>
结论：Mock Functional / Mock Reliability 通过；或失败原因与后续动作
```

只有实际执行过的命令才能标记为通过；Mock 结果和真实基础设施结果必须分开记录。
