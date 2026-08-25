# RAG MVP 功能测试与验证指南

本文说明如何验证 Python RAG 服务的确定性行为、真实基础设施、故障恢复和检索质量。报告结果时必须区分 Fake/Mock、真实 adapter、真实模型、Docker E2E、Docker Resilience 与 Real Eval；离线结果不能替代真实发布验收。

## 1. 环境准备

公共构建与测试入口要求：

- Docker Engine 与 Docker Compose；Earthly 的可复现执行环境依赖 Docker。
- GNU Make。
- Earthly 0.8.16。
- Windows 推荐在 WSL2 中运行 Make、Earthly 和 Docker CLI。

Python 3.12+ 与 `uv` 只在需要直接定位单个测试或本机启动进程时使用，不是公共门禁的前置条件。

真实测试需要从模板创建未提交的运行时配置：

```powershell
Copy-Item .env.example .env
```

`.env` 必须包含真实 Embedding provider 的 URL、模型名、API Key 和声明维度，禁止提交。离线 `make lint`、`make test` 和 `make ci` 使用独立空白 Earthly 环境文件，不读取 `.env`。

## 2. 公共验证入口

修改 protobuf 后重新生成并校验生成物：

```bash
make proto
git diff -- src/rag_mvp/rpc/generated
```

日常开发使用：

```bash
make lint
make test
# 或一次运行完整无 Secret 门禁
make ci
```

- `make lint`：Ruff lint/format check、mypy、protobuf 生成物一致性。
- `make test`：确定性 unit、contract、functional、Fake resilience、offline eval 与 85% 覆盖率门禁。
- `make ci`：聚合 `lint` 与 `test`，也是 pre-commit 和无 Secret GitHub Actions 的唯一入口。

为当前 clone 启用版本控制的提交钩子：

```bash
git config core.hooksPath .githooks
```

钩子执行 `make ci`。不要使用 `--no-verify` 代替修复失败原因。

## 3. 测试分层

| 层级 | 基础设施 | 主要职责 | 正式入口 |
| --- | --- | --- | --- |
| Unit | 纯函数、Fake | 状态机、ID/digest、解析、切块、融合与重排 | `make test` |
| Contract | Fake/静态 artifact | protobuf、Port、Make/Earthly、Hook/CI 契约 | `make test` |
| Functional | 真实 gRPC + Fake ports | upload → outbox → worker → retrieve 闭环 | `make test` |
| Fake Resilience | Fake ports + failpoint | redelivery、取消、删除、generation fence | `make test` |
| Offline Eval | 固定 fixture | Recall@6、MRR@6、locator accuracy | `make test` |
| Integration/E2E | MySQL、ES、NATS、真实模型 | adapter、四格式 gRPC 全链路 | `make docker-test SUITE=integration` |
| Docker Resilience | 完整 Compose + Docker 控制权 | KILL、停启、重复投递和恢复 | `make docker-test SUITE=resilience` |
| Real Eval | 真实 gRPC、ES、模型 | 固定语料的 30 问真实检索评测 | `make docker-test SUITE=eval` |

`tests/fakes/` 不会被生产 `bootstrap/container.py` 导入。Fake 可证明编排和业务不变量，但不能证明 MySQL 锁、ES mapping/KNN/BM25、JetStream ACK/NAK 或进程级恢复。

## 4. 定位单个失败

下列底层命令仅用于失败定位，不是 Hook、CI 或 README 的公共入口。附加 `-vv -s` 可查看详细测试名与输出。

| 要验证的行为 | 定位命令 |
| --- | --- |
| 上传、Finalizer、Relay、Worker、检索主链 | `uv run pytest -vv tests/functional/test_mock_upload_ingest_retrieve.py` |
| TXT、Markdown、Python、文本 PDF | `uv run pytest -vv tests/functional/test_mock_four_formats.py` |
| 幂等与重复投递 | `uv run pytest -vv tests/functional/test_mock_dedup_and_redelivery.py` |
| Retry/Cancel/Delete | `uv run pytest -vv tests/functional/test_mock_retry_job.py tests/functional/test_mock_cancel_job.py tests/functional/test_mock_delete_document.py` |
| 混合检索 | `uv run pytest -vv tests/unit/retrieval` |
| 解析与稳定切块 | `uv run pytest -vv tests/unit/ingestion/test_multiformat_parsers.py tests/unit/ingestion/test_recursive_chunker.py` |
| gRPC 与 Port 契约 | `uv run pytest -vv tests/contract` |
| Mock 可靠性 | `uv run pytest -vv -m resilience tests/resilience` |
| 离线评测 | `uv run pytest -vv -m "eval and not e2e" tests/eval` |

可靠性失败不能只检查 Job 终态，还要比较 Task、OutboxEvent、Document generation、IndexBuild 和 ACK/NAK 记录。`tests/fixtures/reliability_matrix.json` 保存 SPEC T1～T25 到 Mock/真实测试节点的证据映射。

## 5. 真实 Docker 验收

标准流程：

```bash
make docker-up
make docker-test SUITE=integration
make docker-test SUITE=resilience
make docker-test SUITE=eval
make docker-down
```

也可以一次顺序执行全部真实测试：

```bash
make docker-test SUITE=all
make docker-down
```

`SUITE=all` 固定按 `integration → resilience → eval` 执行；任一步失败即停止后续 suite。`docker-test` 会验证、构建、启动并等待完整 Compose 拓扑，但测试结束或失败后不会自动关闭服务，以便保留日志和现场。无论成功失败，最后都应显式运行 `make docker-down`；该入口先扫描服务日志中的 API Key，再执行不删除持久卷的 `down --remove-orphans`，禁止用 `down -v` 代替。

真实测试注意事项：

- `integration` 会调用真实 Embedding API，产生网络请求、延迟和费用；缺少模型配置必须失败，不能静默回退 Fake。
- `resilience` 使用测试专用 Compose override、共享 barrier 和 Docker socket，能够 KILL/stop/start 精确容器；只允许在隔离的测试宿主机运行。
- `eval` 摄取固定语料并调用真实模型完成 30 问，通常是模型请求最多的 suite。
- 真实测试只传必要 Secret 给 `rag-server`、`rag-worker` 和 `rag-test`；Migration 与 Outbox 不应获得模型 API Key。

真实和离线评测门槛均为 `Recall@6 ≥ 0.85`、`MRR@6 ≥ 0.70`、locator accuracy `= 1.0`。不得通过降低阈值、修改向量 snapshot 或 LLM 自由文本 snapshot 消除失败。

## 6. 常见失败

| 现象 | 建议处理 |
| --- | --- |
| Earthly 未安装或版本错误 | 安装 0.8.16；不要用宿主机散装命令冒充 `make ci`。 |
| Docker/BuildKit 不可用 | 先确认 Docker daemon 正常，再重跑原 Make target。 |
| protobuf 生成物落后 | 运行 `make proto`，检查 `.proto` 与两端生成物是否一起更新。 |
| Ruff format check 失败 | 仅在宿主机显式执行 `uv run ruff format ...`，重新 `git add` 后再跑 `make ci`。 |
| Functional 失败 | 按 upload → Finalizer → Relay → Worker → Retrieve 顺序定位。 |
| Resilience 失败 | 检查 Task/Job、Outbox、generation、delivery sequence 和 ACK/NAK。 |
| 覆盖率低于 85% | 为新分支补 unit/functional 测试，不降低 `--cov-fail-under`。 |
| 真实测试失败 | 保留服务，检查容器状态和脱敏日志，随后运行 `make docker-down`。 |
| Secret 扫描失败 | 先修复日志泄漏；扫描器只报告命中，不回显密钥。 |

## 7. CI 边界

- `.github/workflows/quality.yml`：pull request/push 运行 `make ci`，不接收模型 Secret。
- `.github/workflows/docker-quality.yml`：main push/手动运行 integration；夜间或手动运行 resilience 与 eval。两个 job 都安装固定 Earthly，只调用 Make 公共入口，并在 `always()` 清理。
- 必需 Secrets：`EMBEDDING_MODEL_URL`、`EMBEDDING_MODEL_NAME`、`EMBEDDING_MODEL_API_KEY`、`EMBEDDING_MODEL_DIMENSION`。
- 不使用 `pull_request_target`，避免向不受信任 PR 暴露 Secret。

## 8. 记录验证结果

```text
验证范围：<功能或模块>
已运行：<实际 Make target 或定位命令及结果>
指标：<覆盖率或 Eval 指标；不适用则写不适用>
未运行：<真实基础设施、Docker KILL 等原因>
结论：<通过；或失败原因与后续动作>
```

只有实际执行过的命令才能标记为通过；Mock 与真实基础设施结果必须分开记录。
