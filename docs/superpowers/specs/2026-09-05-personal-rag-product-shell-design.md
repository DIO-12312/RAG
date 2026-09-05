# 个人 RAG 产品壳设计

**日期：** 2026-09-05  
**状态：** 已确认，待实施计划评审  
**关联规划：** `docs/superpowers/plans/2026-09-03-product-plane-agent-and-rag-enhancement-outline.md`

## 1. 目标与范围

在不改变现有 Python RAG 服务职责的前提下，新增一个可运行的 Vue 3 + TypeScript 产品前端。前端使用本地 Mock API 演示完整的加载、空态、错误、上传任务和流式对话体验。

同时建立 `backend/go-api` 的 Gin 控制面骨架：包含 MySQL 配置、迁移、连接健康检查、DTO、路由、Repository/Service/Agent 接口及未实现端点；不实现 Go 业务处理、Python gRPC 调用或 Agent Loop。

本期不包含：多用户协作、租户、角色权限、真实模型调用、真实 Agent、Python protobuf 改动、Go 直接读写 RAG 数据表、DeepDoc/AST/RAG 算法改动。

## 2. 架构与职责

```text
apps/web (Vue 3 + TypeScript)
  └─ 本地 Mock API / Mock SSE
       └─ 未来同源 HTTPS API
            └─ backend/go-api (Gin)
                 ├─ 控制面 MySQL（用户、配置、会话归属）
                 └─ 未来版本化 gRPC → Python RAG

src/rag_mvp (现有 Python RAG)
  └─ 仅经版本化 gRPC 提供 Dataset / Document / Job / Retrieve
```

Python 保持 RAG 计算服务：摄取、索引、检索和 evidence。Go 未来负责公网 API、JWT、用户模型配置、会话、Agent 和 SSE。Go 绝不直接修改 Python RAG 的 Dataset、Document、Job、Task、Outbox、ES 或 NATS 状态。

## 3. 个人所有权模型

不引入 Tenant、成员或角色。每位用户是自己知识库的管理员，可拥有多个知识库。Go 在成功创建 Python Dataset 后保存 `dataset_id → user_id` 映射；任何未来 Dataset、Document、Job、Chat 请求都先验证当前用户拥有该 Dataset。

本期对话只选择一个已就绪知识库。多知识库检索推迟：现有 `RetrieveRequest` 仅支持单个 `dataset_id`，未来需以兼容方式演进 protobuf、Python 检索和 `SPEC.md` 后再实现。

## 4. 认证与密钥安全

- 使用 JWT，不使用服务端 Cookie Session。
- JWT 有效期固定 24 小时，放在 `HttpOnly`、`Secure`、`SameSite=Lax` Cookie 中；这只是 JWT 的传输与保存介质，Go 不维护服务器会话。
- 写操作使用 CSRF Token 校验；前端不读取或保存 JWT。
- 密码仅保存 Argon2id 哈希。
- 模型 API Key 仅在服务端加密后写 MySQL；读取配置只返回 `api_key_hint` 和是否已配置，绝不回传原文。
- JWT 载有 `jti` 和用户 `jwt_version`；登出使用撤销记录，改密码或强制退出时递增 `jwt_version`。

## 5. Go 控制面 MySQL 表

| 表 | 字段（省略通用主键/时间字段时已在说明中列出） | 约束与职责 |
|---|---|---|
| `users` | `id`, `email`, `password_hash`, `language`, `jwt_version`, `created_at`, `updated_at` | `email` 唯一；默认语言 `zh-CN`。 |
| `jwt_token_revocations` | `id`, `jti`, `user_id`, `expires_at`, `revoked_at` | `jti` 唯一；过期记录可由维护任务清理。 |
| `chat_model_configs` | `id`, `user_id`, `base_url`, `model_name`, `encrypted_api_key`, `api_key_hint`, `timeout_seconds`, `thinking_enabled`, `created_at`, `updated_at` | `user_id` 唯一；保留 Chat 专属思考模式开关。 |
| `embedding_model_configs` | `id`, `user_id`, `base_url`, `model_name`, `encrypted_api_key`, `api_key_hint`, `timeout_seconds`, `default_top_k`, `embedding_dimension`, `created_at`, `updated_at` | `user_id` 唯一；维度默认 1024。`default_top_k` 是用户默认召回值，不是模型固有参数。 |
| `rerank_model_configs` | `id`, `user_id`, `base_url`, `model_name`, `encrypted_api_key`, `api_key_hint`, `timeout_seconds`, `top_n`, `created_at`, `updated_at` | `user_id` 唯一；只保存配置，不代表当前 RAG 链路已经启用。 |
| `agent_settings` | `user_id`, `rerank_enabled`, `created_at`, `updated_at` | `user_id` 唯一；默认关闭 rerank。 |
| `dataset_ownership` | `dataset_id`, `user_id`, `created_at` | `dataset_id` 唯一；只保存跨服务所有权映射，不跨库外键。 |
| `conversations` | `id`, `user_id`, `dataset_id`, `title`, `created_at`, `updated_at` | 为后续 Agent 会话预留；当前不实现写入。 |
| `conversation_messages` | `id`, `conversation_id`, `role`, `content`, `status`, `citations_json`, `created_at` | 为后续保存完成消息和引用预留；禁止存密钥或内部 trace。 |
| `schema_migrations` | 由迁移工具维护 | 仅记录 Go 控制面 schema 版本。 |

创建 Dataset 后，Embedding 配置的维度要传给 Python gRPC 并在 Dataset 首次可用文档后冻结；本期前端 Mock 仅表现该约束，不调用真实后端。

## 6. 前端信息架构

视觉方向为简洁留白、偏消费级产品，借鉴 RAGFlow 的 Dataset → 上传 → Job → Chat 流程，不复制其工作流画布、模型市场或高密度管理台。

| 页面 | 能力 |
|---|---|
| 概览 | 知识库状态摘要、最近 Job 与快捷入口。 |
| 知识库 | 创建知识库、列表、详情、上传、文档与 Job 状态；取消、重试、删除仅在合法状态出现。 |
| 对话 | 选择一个属于当前用户且已就绪的知识库，显示 Mock SSE 的检索、token、final/error；点击 citation 展开完整命中 evidence chunk 卡片。 |
| 设置 | 中英文切换（默认中文）、账户、Chat/Embedding/Rerank 三组模型配置、Agent rerank 开关。Rerank 标明“已保存，待后端接入”。 |
| 登录/注册 | Mock JWT 登录态、24 小时有效期和登出体验。 |

## 7. 前端 Mock 契约

前端通过本地 Mock API 而非静态页面数据访问下列未来 API 形状：

```text
POST /auth/register
POST /auth/login
POST /auth/logout
GET  /me

GET  /datasets
POST /datasets
GET  /datasets/:id
POST /datasets/:id/documents
GET  /datasets/:id/jobs
POST /jobs/:id/cancel
POST /jobs/:id/retry
DELETE /documents/:id

GET  /settings
PUT  /settings/models/chat
PUT  /settings/models/embedding
PUT  /settings/models/rerank
PUT  /settings/agent
POST /chat/stream
```

Mock 必须覆盖加载、空列表、网络失败、登录过期、上传进度、可重试失败、取消与重试。Mock SSE 严格按 `retrieval → token* → final | error` 输出。`final` 的每个 citation 含 `chunk_id`、完整 `content`、`source_name`、`locator` 和必要分数；点击引用只展开该 evidence chunk，不展示或下载整个原始文档。

## 8. Go 骨架边界

`backend/go-api` 真实实现以下基础设施：配置读取、MySQL 连接池、控制面 migration、`/healthz`、检查 MySQL 的 `/readyz`、路由注册、统一错误 DTO、JWT/CSRF 中间件接口、Repository/Service/Agent/未来 gRPC Client 的接口与 Fake。

业务 Handler 与 Service 不实现认证、配置持久化、Dataset 转发、Chat 或 Agent：端点明确返回 `501 Not Implemented`。Go 不生成或修改 Python protobuf。本期前端使用 Mock，因此可独立运行、测试和构建。

## 9. 验收与测试

- 前端：TypeScript strict、lint、单元测试、production build；Mock API 的成功、加载、错误和 JWT 过期状态；Mock SSE 的顺序、取消和 citation 展开测试。
- Go：格式化、`go vet ./...`、测试；MySQL 未配置时 `/readyz` 受控失败，已配置可连通时就绪；未实现业务端点稳定返回 501。
- 契约：前端 DTO 与 Go DTO 的字段/错误码对齐测试；不调用 Python gRPC，不访问 Python RAG 表的架构测试。
- 既有 Python：不修改源码、protobuf、数据库 migration、Docker/Earthfile 或 RAG 测试结构。

## 10. 明确延期项

- 多知识库单次 Retrieve、对应 protobuf/`SPEC.md` 演进。
- 真实 JWT 签发、登录、密码重置、模型密钥保存和 CSRF 处理业务。
- Go 调用 Python gRPC、Dataset 所有权登记的事务补偿、真实上传与 Job API。
- Chat Model Gateway、Agent Loop、Tool Calling、真实 SSE、会话消息持久化。
- Rerank 的用户级接入、模型调用、DeepDoc、AST 与检索优化。
