# Go 产品控制面与 Agent 实施计划

**Goal:** 注册登录、真实文件上传、任务查询和基于 Python Retrieve 工具的问答能够从 Vue 使用。
**Architecture:** Gin 控制面使用独立 MySQL 库。Python 保持 RAG 聚合的唯一写入方。生成的 Go gRPC Client 调用现有 protobuf；Agent 使用 OpenAI-compatible Chat Completions 的 function calling。
**Spec:** SPEC.md §5.6、本文以及 2026-09-05 产品壳设计；用户在 2026-09-06 明确将骨架范围提升为真实实现。

## 边界

- JWT 24 小时；HttpOnly cookie、同源校验与 CSRF header；密码 Argon2id，模型密钥 AES-GCM。
- Go 自有资源索引用于所有权与列表，不直接读取 Python 表。每个资源和会话必须验证 user_id。
- Python 使用部署时 Embedding 配置。Go 以相同模型名/维度创建 Dataset；用户级模型凭据传递不在现有 RPC 中，设置页明确展示部署约束。Rerank 不可伪装成已经生效。
- Agent 只暴露无副作用的 rag_retrieve 工具，dataset_id 由已鉴权的会话绑定，不接受模型指定其他 Dataset。上传/删除/取消仅通过显式 HTTP 用户操作。
- Agent 限制轮数、工具参数/输出与请求时间，传播取消；保存会话消息及完整引用。

## A1 控制面基础

- [x] 建立 backend/go-api/go.mod、internal/security、internal/storage、internal/httpapi。
- [x] MySQL migration 创建 users、三类模型配置、agent_settings、资源索引、conversations、messages、JWT 撤销表。
- [x] 测试无效密文拒绝、密码核验、JWT 过期、未登录与跨用户拒绝；实现注册登录登出、配置脱敏读写。
- [x] go test ./...、go vet ./...。

## A2 gRPC 与资源 API

- [x] 从 proto/rag/v1/rag_service.proto 生成 internal/ragpb，保持 wire schema。
- [x] internal/ragclient 封装创建、上传、查询、检索；资源 HTTP handler 经生成客户端调用 RetryJob、CancelJob、DeleteDocument。
- [x] HTTP multipart 流式上传，限定体积；资源索引保存 Job/Document ID；列表查询通过 RPC 刷新。
- [x] 幂等请求键与失败恢复不改变 Python 终态语义；所有资源操作先验证所有权。
- [x] 显式运行真实 MySQL/Python RPC/Worker/ES 摄取检索集成测试。

## A3 Agent 与前端联通

- [x] internal/agent 实现 Model/Tool 接口、工具循环、证据累计、引用校验、错误和取消。
- [x] OpenAI-compatible 模型网关支持 timeout、thinking 开关；SSE retrieval/token/final/error。
- [x] 前端默认实际 HTTP，Mock 仅显式开启；实现注册、配置保存、文件选择上传、轮询、取消重试、问答与证据展示。
- [x] 固定模型 HTTP + 真实 gRPC 验证 tool call → Retrieve → 最终答案；单元测试覆盖非法工具、预算耗尽和取消。
- [x] go test/vet、前端 lint/typecheck/test/build、真实基础设施验收；分别报告确定性与真实模型结果。

## 验收记录（2026-09-06）

- 前端 lint、构建（含 vue-tsc）通过；10 个测试文件、14 项测试通过。
- Go test/vet 通过；TestLiveProductFlow 显式启用，通过真实上传摄取、证据检索、问答持久化及两个真实账号的资源隔离检查。普通 Go 测试未配置环境变量时跳过此项。
- `make ci` 已通过：Python 240 passed、10 deselected，覆盖率 88.41%。联调发现并修复 Search Guard bulk 分片权限不足，补充原有契约测试断言。
- Chat 初始使用确定性 HTTP 模型测试；用户保存配置并授权后，真实 DeepSeek + Python RAG 闭环验收已通过，并修复思考参数与工具选择兼容性。SSE 当前按完整模型轮次输出文本，不是供应商逐 token 流式。
- 用户级 Embedding 凭据下发、Rerank 执行、多实例会话锁及跨服务后台对账未实现，具体边界见 `docs/development/live-product-plane.md`。
- 本轮尚未生成 Git 提交；保留工作区既有前端改动，不将它们混入后端提交。原计划的逐模块提交未执行，不能作为已完成项。

## 部署

### 2026-09-06 后续体验迭代验收

- [x] 多文件/文件夹展开上传：逐文件队列、错误隔离、同键重试。
- [x] 安全 Markdown 渲染和右侧历史会话抽屉（最近活动时间倒序）。
- [x] Embedding 前端配置写入 Go MySQL；Dataset 加密快照经新增 RPC 传入 Python；运行服务移除模型凭据环境变量。迁移 0003 已执行，旧配置已加密导入；共享密钥卷保留。
- [x] 前端 lint/build 及 17 项测试通过；独立 Chrome 测试验证批量上传、Markdown、会话恢复/Escape、移动端抽屉与设置字段（合成数据）。
- [x] Go test/vet；显式 TestLiveProductFlow 通过真实 MySQL/gRPC/Worker/ES/Embedding，Chat 为确定性供应商；本轮未重复调用真实 Chat。
- [x] 最终 make ci：247 passed、10 deselected，核心覆盖率 88.02%；包含离线韧性门禁。
- [x] 独立 MySQL 测试库中 21 项迁移/事务/并发/Outbox 测试通过；增加首次快照并发绑定测试后，submission 子套件 4 项再次通过（合计 22 个不同用例）。
- [ ] 完整 Docker KILL 韧性与真实模型 eval 未重跑。既有测试会清空默认业务库，不能在用户资料栈直接运行；本轮使用隔离 MySQL 检查及真实产品闭环。

上述替代了原验收记录中的“用户级 Embedding 未实现”限制。模型固定 1024 维；既有快照不可原地换模型/密钥，需新建知识库。代码未提交，不把此前未提交实现和本轮体验改动混为一个提交。

独立 compose.product.yml 接入现有 rag-mvp_default 私网，控制面 MySQL 使用独立卷；公网只映射 localhost HTTP。需要现有 Python 栈和用户 Chat 模型配置；不得用 Mock 验收代替真实模型验收。
