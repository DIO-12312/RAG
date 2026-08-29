# MQ 选择原因

## 当前结论

本项目当前选择 **NATS JetStream** 作为摄取任务队列，暂不引入 RocketMQ 或 Kafka。

原因不是 NATS 在所有场景都更强，而是它最符合当前项目的规模和任务模型：单机优先的 Python RAG MVP、一个摄取 Worker 域、消息仅用于可靠唤醒 Worker，而 MySQL 才是 Job、Task、取消、重试和索引版本的权威状态来源。

## 与项目设计的匹配点

当前摄取链路是：

```text
MySQL 事务创建 Document / Job / Task / OutboxEvent
  → Outbox Relay 发布 task_id
  → JetStream durable consumer 投递
  → Worker 回读 MySQL 并执行 Pipeline
  → 成功 ACK；暂时失败 NAK；未确认则 redelivery
```

因此队列消息只传递 `task_id`，不携带可变业务状态。即使 Worker 被强杀、ACK 丢失或消息重复投递，Worker 仍可以通过 MySQL 条件认领、`last_delivery_sequence`、Document generation fence 和 Elasticsearch 的幂等物理 `_id` 收敛为一次可见结果。

JetStream 原生提供 durable consumer、显式 ACK/NAK、`ack_wait`、`max_deliver` 和 redelivery，正好满足这条链路。它让当前项目能把重点放在 RAG 摄取正确性，而不是自行实现消息确认、重投和消费进度协调。

## 为什么当前不选 Kafka

Kafka 更适合长期保存、可回放、分区化的大规模事件流。例如未来有独立的分析、审计、计费、推荐、数据湖或 CDC 消费者，同时都要消费“文档上传/摄取完成/检索反馈”等领域事件时，Kafka 的多消费组和事件回放价值会很高。

但现在只有“执行这个 `task_id`”的工作队列需求。若使用 Kafka，还要额外设计 offset 提交时机、分区键、retry topic、延迟重试和 DLQ；这些复杂度不会替代 MySQL Outbox、任务状态机或 ES 幂等写入。对当前单机 MVP 而言，收益不足以覆盖引入成本。

## 为什么当前不选 RocketMQ

RocketMQ 很适合国内 Java 企业体系，尤其是已有统一 RocketMQ 平台、运维、监控、延迟消息和 DLQ 治理能力的组织。它的消费重试和死信队列能力也适用于可靠任务处理。

本项目当前以 Python Worker 为核心，且只需要一个摄取队列。若没有既有 RocketMQ 基础设施，改用 RocketMQ 不会简化 MySQL + Outbox + 幂等 Worker 的核心设计，反而会增加 SDK、部署、监控和测试维护面。因此暂不替换。

## 后续选择边界

| 出现场景 | 建议 |
| --- | --- |
| 单机或少量 Worker 的 RAG 摄取、可靠 ACK/NAK 与重投 | 继续使用 NATS JetStream |
| 公司已有 RocketMQ 平台，且需要统一延迟消息、DLQ 与运维体系 | 在 `TaskQueue` port 下实现 RocketMQ adapter |
| 多团队消费长期领域事件，需要独立回放、数据分析、CDC 或数据湖 | 新增 Kafka 作为事件总线，不必替换摄取任务队列 |

无论选择哪一个 MQ，以下原则不变：MySQL 仍保存业务事实；Outbox 仍解决数据库与 MQ 的双写问题；Worker 仍必须幂等；Elasticsearch 仍通过版本化物理 `_id` 防止重复写入。MQ 只能提供至少一次投递和消费协调，不能单独实现跨 MySQL、对象存储、模型服务与 Elasticsearch 的端到端 exactly-once。
