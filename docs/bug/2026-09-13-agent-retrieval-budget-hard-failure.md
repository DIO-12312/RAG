# 开发环境问答失败：检索轮次预算用尽被当成致命错误

2026-09-13 本地 `make run` 后，在开发环境（`rag-product` 栈，`http://<IP>:5173`）提问时**有时**返回：

```text
问答未完成，请检查模型连通性、工具调用支持及知识库状态。
```

这句话是 `internal/httpapi/chat.go` 对任何 Agent Run 失败的兜底文案，不代表真的是连通性问题。真正的失败原因可从 Go API 的结构化观测事件直接读出——这条排查路径正是本轮迭代新增的 `agent_run` 事件（只含阶段、计数、耗时、终止原因与查询指纹，不含问题正文与模型推理）。

## 定位过程

聚合 API 容器日志中的错误码，失败**全部**同一类：

```bash
docker logs --tail 2000 rag-product-api-1 2>&1 | grep -oE "error_code=[a-z_]+" | sort | uniq -c
#       5 error_code=budget_exceeded
```

取一条完整失败序列（`run_id=96f577e4dab00b0364175ad658a8d4ff`，2026-09-13 16:15:30–16:15:35）：

```text
route      action=retrieve
model      round=1 action=required                       # 模型要求调用 rag_retrieve
tool       round=1 action=model    retrieval_calls=1 evidence=6
assess     round=1 action=insufficient                   # SCA 判定证据不足
rewrite    round=1 action=rewritten                      # 重写出 2 条查询
tool       round=2 action=rewrite  retrieval_calls=2
tool       round=3 action=rewrite  retrieval_calls=3
assess     round=3 action=insufficient                   # 仍不足
rewrite    round=2 action=rewritten
complete   error_code=budget_exceeded stop_reason=budget_exceeded   # ← 整次请求失败
```

即：第 4 次检索时命中 `RunLimits.MaxRetrievalRounds = 3`，运行循环**直接判定失败**，前端只看到兜底文案。

## 根因

`backend/go-api/internal/agent/runtime.go` 的 `toolPhase` 把预算用尽当作不可恢复错误：

```go
if err := state.CheckRetrievalRound(); err != nil {
    state.MarkFailed(StopReasonBudgetExceeded)   // 缺陷：应当收敛
    return err
}
```

触发条件很常见：`SufficiencyAssessor` 连续两轮判定"证据不足"（开发知识库里确实缺对应事实），而每轮 `QueryRewriter` 最多产出 2 条查询，于是 3 轮检索额度很快被两轮改写吃满。计划（`docs/superpowers/plans/2026-09-13-agentic-rag-loop-iterations.md` I4-2）明确要求"达到任何上限后不再调用下游"，且"不足且无改写额度则带不足约束 Finalize"——检索轮次用尽属于同一类收敛条件，不应变成失败。

## 修复

1. `runtime.go`：检索轮次用尽时改为**收敛**——为尚未执行的工具调用补齐说明性 `tool` 结果（保持 tool call/result 成对，避免 provider 因悬空调用报错）→ 保留 SCA 给出的缺口列表、标记 `retrieval_budget_exhausted` 与"证据不足" → 直接进入 `Finalize` 生成受限回答，用户得到的是明确的"证据不足"回答而不是报错。
2. `failure.go`（新增）+ `chat.go`：`RunError` 携带终止原因，`FailureHint` 映射为稳定错误码与可操作提示——`RUN_BUDGET_EXCEEDED`、`TOOL_CALL_INVALID`、`MODEL_UNAVAILABLE`、`REQUEST_CANCELLED`，未知错误才回落到原 `CHAT_FAILED` 文案；避免再把可收敛问题误报成"模型连通性"。
3. 测试：`runtime_test.go` 新增"检索预算用尽必须收敛且不产生悬空工具调用"与 `FailureHint` 映射用例；既有子用例 `TestRuntimeBudgetAndCancellationAreTerminal/retrieval round budget converges instead of failing` 的旧期望（断言报 budget 错）正是本次缺陷行为，已改为收敛期望。模型预算、非法工具调用与供应商错误仍保持终态失败。
4. `SPEC.md` §5.6 记录该收敛语义与错误码映射。

## 验证状态

代码与测试已写入，但**尚未运行验证**：排查时主机内存已处于临界状态（`free -m` 显示 swap free 40MB、available 2.33GB，3 套 Compose 栈 + `earthly-buildkitd` 常驻），按运维要求（swap free < 400MB 或 available < 1.5GB 时不得跑测试或构建）停手。

内存恢复后需依次执行：

```bash
# 1. 受限容器内跑受影响包（避免并发与无上限内存）
docker run --rm --memory=1536m --memory-swap=1536m -e GOMAXPROCS=2 -e GOFLAGS=-mod=readonly \
  -v /data/RAG/backend/go-api:/app -v rag-go-modcache:/go/pkg/mod -w /app \
  golang:1.26.2-bookworm go test -p 1 ./internal/agent -count=1

# 2. 用当前分支代码重建开发 API（普通 docker build，不使用 earthly/buildx）
docker compose -f compose.product.yml up -d --build api

# 3. 复现同一问法直到触发 3 轮检索上限，确认日志变为正常收敛
docker logs --tail 200 rag-product-api-1 2>&1 | grep agent_run | tail -20
# 期望：stage=complete ... stop_reason=evidence_insufficient（不再是 budget_exceeded）
```

## 影响面

生产栈当前运行 GHCR 上的 `5ae3241`（本轮 Agentic RAG Loop 之前的镜像），因此线上尚未受影响；但本轮迭代一旦发布就会遇到相同触发条件，修复必须在发布前完成。所有"预算/上限用尽"的路径今后都必须在有界收敛与显式失败之间写明语义，并优先选择收敛；观测事件里的 `error_code` 与 `stop_reason` 是这类问题的第一诊断入口。
