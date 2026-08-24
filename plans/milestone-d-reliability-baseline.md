# Milestone D：可靠性基线（Mock Reliability）实施计划

> 状态：Mock Reliability 已完成；真实基础设施发布验收延期
> 范围：在测试专用 Fake ports 上完成 Python 状态机、并发栅栏和故障恢复逻辑；真实 MySQL 行锁/事务、ES/NATS 与 Docker KILL 验收仍明确延期。
> 对应规格：T1～T25、`SPEC.md` 2.2、2.5、4.4～4.6、5.5

## D1：Finalizer、Relay 与 staging 恢复

- Finalizer 单次失败递增 Outbox attempt；达到上限原子收敛 Job/Task FAILED、Outbox CANCELLED、fingerprint RELEASED。
- staging sweeper 只删除超过 TTL 且未被 WAITING Outbox 引用的对象。
- 覆盖 Finalizer promote 后条件更新失败补偿、Relay publish 后崩溃重复投递、发布暂时失败恢复。

提交：`fix(outbox): 补齐 Finalizer 终态与 staging 恢复`

## D2：并发 fingerprint 与 Retry 唯一性

- 并发相同文件不同幂等键只产生一个 canonical Job，落选 staging 立即删除。
- 并发 RetryJob 只创建一个活跃子 Job，retry_count 只加一；FAILED_RETRYABLE 与 RELEASED 状态严格复用/释放。
- 覆盖重建 index_version 唯一分配的 repository 契约。

提交：`test(ingestion): 验证并发去重与 Retry 唯一性`

## D3：CancelJob 与已发布 delivery 竞态

- 开放 CancelJob，仅允许 INGEST_DOCUMENT；PENDING 原子取消 Task/Job/未发布 Outbox，RUNNING 设置 cancel request 并由 Worker 完成栅栏收敛。
- 取消后不得切换 active_version；已发布但未认领的 delivery 只 ACK。
- SUCCEEDED/FAILED/CANCELLED 与删除 Job 返回稳定业务错误。

提交：`feat(ingestion): 实现 CancelJob 与 Worker 收敛`

## D4：generation fence、不可见版本清理与可靠性总门禁

- Delete/Cancel 与 index write 并发时完成事务不得复活 Document；创建 CLEANUP_INDEX_VERSION 系统任务清除不可见版本。
- 覆盖 Delete/Finalizer、Delete/Worker、ACK 丢失、重建版本与清理幂等。
- 建立 T1～T25 Mock Reliability 矩阵，README/PLAN 明确哪些测试只能由真实基础设施完成。

提交：`test(resilience): 验证 generation fence 与故障恢复矩阵`

## 验收清单

- [x] D1 Finalizer 终态与 staging 恢复通过并提交。
- [x] D2 并发 fingerprint/Retry/rebuild 唯一性通过并提交。
- [x] D3 CancelJob 与已发布 delivery 竞态通过并提交。
- [x] D4 generation fence、不可见版本清理和总门禁通过并提交。
- [x] 未将 Fake 结果表述为真实 MySQL/ES/NATS/Compose 发布验收。
