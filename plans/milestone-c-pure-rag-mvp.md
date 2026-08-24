# Milestone C：纯 RAG MVP（Mock Functional）实施计划

> 状态：实施中（C1～C2 已完成）
> 对应路线图：`PLAN.md` C1～C6、2.1 无 Docker 开发通道
> 对应规格：`SPEC.md` 2.1、2.3、2.5、3.3、4.2～4.6、5.4～5.5、P1-3、P2-3～P2-4、P3-1～P3-4
> 范围：在 Milestone B 的最终 ports 上完成四类文件、Dense + BM25 + RRF、可选 Rerank、基础 Retry/Delete 与离线评测。真实 MySQL/Elasticsearch/NATS adapter、Compose E2E 和并发极端竞态继续延期到真实基础设施环境及 Milestone D。

## 1. 不变量

- Python 仍只提供 `rag.v1.RagService` gRPC；不新增 HTTP、Chat、Agent、SSE 或 Prompt/Citation 编号。
- Parser、检索算法、Retry/Delete 用例继续依赖现有 domain/ports；测试 Fake 不进入 `src/` 或生产 bootstrap。
- SearchEngine 只返回 Dense/BM25 两路候选；`retrieval/hybrid.py` 是 RRF 融合、去重和稳定排序的唯一实现。
- `retrieval/rerank.py` 是纯函数；只有 `RetrievalService` 调用 `ModelGateway.rerank`，可降级错误回退 RRF。
- Retry 创建新 Job/Task/READY Outbox，旧 FAILED Job 不复活；没有正式 object 的失败不可 Retry。
- Delete 先逻辑删除并立即不可检索，再创建 CLEANUP Task 异步删除全部索引版本和正式对象。
- C1～C6 每个工作包通过相称门禁后立即独立 commit，不执行 push。

## 2. C1：Markdown、Code、文本 PDF Parser 与路由

### 文件

```text
src/rag_mvp/adapters/parsers/
├─ markdown.py
├─ code.py
├─ pdf.py
└─ router.py
tests/unit/ingestion/test_multiformat_parsers.py
tests/fixtures/golden_chunks/{markdown,code,pdf}.json
```

- Markdown 按标题保留 section metadata 与行号；代码按类/函数符号优先切段，保留 language/symbol/行号；PDF 按页输出文字与 page_number。
- Router 只接受 `.txt/.md/.py/.go/.js/.ts/.java/.pdf`，其他扩展名返回 `UNSUPPORTED_SOURCE_TYPE`。
- Pipeline 通过 Router 选择 parser，不根据内容猜测格式。

验证：`uv run pytest tests/unit/ingestion -q`

提交：`feat(parser): 支持 Markdown Code 与文本 PDF`

## 3. C2：BM25、RRF 与稳定混合检索

### 文件

```text
src/rag_mvp/retrieval/hybrid.py
src/rag_mvp/application/retrieval_service.py
tests/unit/retrieval/test_hybrid.py
tests/unit/application/test_retrieval_service.py
```

- Dense/Sparse 各取大于最终 top-k 的候选；MetadataRepository 批量复核 active version 后再融合。
- RRF 使用 `1 / (rrf_k + rank)`，按逻辑 chunk 去重，保留 dense/sparse/fusion score；并列以稳定 record ID 排序。
- Dataset 与 metadata filter 同时生效，不跨 dataset。

提交：`feat(retrieval): 实现 BM25 与 RRF 混合召回`

## 4. C3：可选 Rerank 与 ContextPlan

### 文件

```text
src/rag_mvp/retrieval/rerank.py
src/rag_mvp/retrieval/context_builder.py
src/rag_mvp/retrieval/provenance.py
src/rag_mvp/application/retrieval_service.py
tests/unit/retrieval/test_rerank.py
tests/unit/retrieval/test_context_builder.py
```

- 最多把融合 Top-20 送入 ModelGateway，纯函数稳定重排并输出 Top-N。
- 可降级模型错误回退 RRF；输入数/分数数不一致返回稳定错误。
- ContextPlan 只保留完整 evidence，报告 token 估算和 omitted chunk IDs，不生成 Prompt。

提交：`feat(retrieval): 增加可降级 Rerank 与上下文预算`

## 5. C4：基础 RetryJob

- 扩展 MetadataRepository 原子创建 retry Job/Task/READY Outbox；同一 idempotency key 重放返回相同结果。
- 只接受 FAILED + retryable + 正式 object 已存在；新 Job 带 `retry_of_job_id`，旧 Job/Task 保持 FAILED。
- RPC 开放 RetryJob，functional 测试验证 Relay/Worker 可执行新任务。

提交：`feat(ingestion): 实现合规 RetryJob 基础闭环`

## 6. C5：基础 DeleteDocument 与异步清理

- MetadataRepository 原子逻辑删除、释放 fingerprint、创建 DELETE_DOCUMENT Job/CLEANUP_DOCUMENT Task/READY Outbox。
- Retrieve 在 RPC 返回后立即不可见；Worker 按 Task 类型调用 SearchEngine 删除全部版本并删除正式 object，最后条件完成清理 Job。
- 同一 idempotency key 重放复用；新请求删除已删除文档返回 `DOCUMENT_ALREADY_DELETED`。

提交：`feat(ingestion): 实现逻辑删除与异步清理`

## 7. C6：四格式 Functional 与离线评测

- in-process gRPC 分别上传 TXT、Markdown、代码与生成的文本 PDF，执行异步摄取并检索 provenance。
- 固定至少 30 个 query/relevant_chunk_ids，计算 Recall@6、MRR@6 和 locator 命中率；门槛分别为 0.85、0.70、1.0。
- CI/hook 增加 `pytest -m eval tests/eval`；README 说明 Mock Functional 能力和真实基础设施缺口。

完整门禁：

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract tests/functional
uv run pytest -m resilience tests/resilience
uv run pytest -m eval tests/eval
```

提交：`test(e2e): 验证四格式混合检索与评测基线`

## 8. 验收清单

- [x] C1 四类 Parser、Router 与 golden locator 通过并提交。
- [x] C2 Dense/BM25/RRF、filter 和 active-version 复核通过并提交。
- [ ] C3 可选 Rerank、降级与 ContextPlan 通过并提交。
- [ ] C4 RetryJob 新 Job/Task/Outbox 闭环通过并提交。
- [ ] C5 DeleteDocument 立即不可见与异步清理通过并提交。
- [ ] C6 四格式 gRPC Functional、30 问评测和全门禁通过并提交。
- [ ] Fake 仍只存在于 `tests/fakes/`，真实 integration/E2E 未运行项明确保留。
