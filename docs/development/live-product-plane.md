# 运行 Go 产品后端与真实知识库问答

## 当前运行方式

首次安装先创建共享基础设施密钥卷：`docker volume create rag-product_product-keys`。完整本地开发可直接运行 `make run`，它会启动 Python RAG 栈、Go API、产品 MySQL 和 Vue 开发服务器；也可以按下面的命令分开启动：

```powershell
docker compose -f compose.product.yml up -d --build
pnpm --dir apps/web dev --host 127.0.0.1
```

浏览器打开 http://127.0.0.1:5173/ 。前端以 hash 路由承载页面，同源根路径 HTTP 请求经 Vite 转发至 127.0.0.1:8080；不要使用原先仅 Mock 的任意密码登录方式。

1. 注册账号（密码 8–128 位），登录后在设置填写对话模型的 Base URL、模型名和 API Key。需要 OpenAI-compatible Chat Completions **function calling** 支持；Base URL 通常含 `/v1`，不要填写 `/chat/completions`。
2. 创建知识库，上传 PDF、Markdown、TXT、代码、CHM 或 CHI 文件，等待任务成功。最大文件 32 MB。知识库详情页提供“删除知识库”，二次确认后立即从列表隐藏，底层索引和原文件由异步清理任务删除且不可恢复。
3. 对话页选择已完成索引的知识库并提问。Agent 调用 `rag_retrieve`，映射引用并保存问答历史；点击引用展开完整证据。
4. 停止按钮中断请求并取消后端上下文；失败回答不保存成成功消息。历史会话可以继续提问。

## 职责和实际能力

- `internal/httpapi`：Gin、认证、所有权验证、配置、multipart 上传、会话和 SSE。
- `internal/security`：Argon2id 密码哈希、24 小时 HS256 JWT、AES-GCM 用户绑定加密。
- `internal/storage`：仅控制面 MySQL；用户、三类模型配置、JWT 撤销、资源索引、会话、消息、迁移版本。
- `internal/ragclient` / `internal/ragpb`：已有 Python protobuf 的生成代码和 RPC 封装。
- `internal/agent`：模型接口、只读 RAG 工具、最多 6 轮/每轮最多 4 次工具调用、最多 40 条证据、上下文取消、引用编号校验。模型不能自选其他用户的 dataset_id。

资源索引保存 Go 创建过的 Dataset/Document/Job 引用与名称，不复制 Python 状态机；列表和文档状态根据 GetJob 读取。旧 CLI 创建的数据集不会自动归属于新注册用户。Go 数据库与 Python RAG 数据库完全分离。

当前 Chat Model 请求使用 `stream:false` 收集单轮完整模型响应，SSE 发送检索事件、完整轮次文本 token 事件和 final/error；不是供应商逐 token 透传。有效问答成功后，将用户消息与助手消息在一个 MySQL 事务中保存。进程内防止同一会话同时运行，本版以单 API 实例为部署边界。

设置页可保存 Embedding Base URL、模型、API Key、超时和 Top-K，Go MySQL 加密存储凭据。创建知识库时经 RPC 将加密配置快照保存到 Python Dataset；Worker 与 Retrieve 均使用该快照。运行中的 Server/Worker 不再读取 EMBEDDING_MODEL_* 环境变量。当前 ES mapping 固定 1024 维；已绑定知识库不随个人设置改变模型，旧知识库首次使用时须匹配原模型/维度。更换模型需要创建新知识库并重新上传；当前快照接口也不支持原地轮换已有知识库的密钥。Rerank 配置可加密保存，但启用请求明确拒绝，界面显示未接入。这些限制与前端 Mock 演示区分。

## 数据与配置

`compose.product.yml` 是本地开发编排，默认数据库密码仅用于本地；MySQL 3307 和 API 8080 均绑定 127.0.0.1。部署到公网前必须配置 HTTPS、`PRODUCT_COOKIE_SECURE=true`、准确 `PRODUCT_ORIGIN` 和外部数据库/密钥；Python RPC 保持私网。

JWT 是 HttpOnly Cookie，有效期 24 小时，关闭标签页仍可恢复；写请求须同时提供 JWT 绑定的 `X-CSRF-Token`。登出撤销 jti。API Key 从不回传，仅有 hint；加密和 JWT 签名密钥在独立 `product-keys` 卷自动生成并持久保存，也可由 `PRODUCT_ENCRYPTION_KEY`（32 字节 base64）和 `PRODUCT_JWT_SECRET`（至少 32 字节）提供。备份数据库时必须同时保留加密密钥；不要删除密钥卷。

三种模型配置使用独立表和具体列（不是 JSON 配置包），各有专属参数。`resource_index` 合并承载 Dataset/Document/Job 的所有权引用；`schema_migrations` 记录初始版本。后续表结构变更需要新增 migration，不能仅修改初始建表语句。

跨服务创建/上传使用 `Idempotency-Key`，Go 加用户前缀传给 Python。RPC 已成功、资源索引登记失败时返回可重试错误，应以同一键重试恢复索引。当前没有后台跨服务对账任务，保留同一请求键至成功很重要。

模型 URL 默认仅允许 HTTPS，连接时复核 DNS 地址防止访问私网和重绑定；显式 `PRODUCT_ALLOW_LOCAL_MODELS=true` 可用于可信的本地模型部署。该选项会允许私网 HTTP，因此不在默认 Compose 中启用。

## 验证

```powershell
cd backend/go-api
go test ./...
go vet ./...
```

`TestLiveProductFlow` 使用真实独立 MySQL、Python gRPC、Worker、ES 与部署 Embedding，并使用 httptest 提供确定性的 Chat 模型响应。它验证注册/CSRF、配置脱敏、上传摄取、检索工具、引用和消息持久化，并删除自己创建的临时数据集。

```powershell
$env:PRODUCT_TEST_MYSQL_DSN='product:product-local-dev@tcp(127.0.0.1:3307)/rag_product?parseTime=true'
go test ./internal/httpapi -run TestLiveProductFlow -v -count=1
```

未配置上述变量时该集成测试跳过，不能将普通 `go test ./...` 的成功视为真实基础设施通过。2026-09-06 已显式运行并通过。用户保存配置并授权测试后，另设置 `PRODUCT_TEST_SAVED_CHAT=true`，已通过真实 DeepSeek Chat 与 Python RAG 的端到端测试。此模式要求数据库恰有一条 Chat 配置并可访问本地部署的加密密钥，会产生真实 API 用量；普通测试仍使用确定性 Chat。浏览器完整交互与后端 HTTP 集成验收应分别报告。

DeepSeek 的思考开关使用 `thinking.type`；开启时工具选择为 auto，工具轮次保留其返回的 reasoning_content。其他兼容模型仍使用可选的 enable_thinking 扩展。详情见 `docs/bug/2026-09-06-deepseek-thinking-tool-choice.md`。

前端 `pnpm --dir apps/web lint`、`typecheck`、`test --run`、`build`；Mock 仅在显式 `VITE_USE_MOCK=true` 时启动。HTTP/SSE 传输测试覆盖分段 UTF-8 与缺失 final 的断流。

Go protobuf 重新生成（在 backend/go-api 下）：

```text
protoc --proto_path=../../proto --go_out=. --go_opt=module=rag-mvp/backend/go-api --go_opt=Mrag/v1/rag_service.proto=rag-mvp/backend/go-api/internal/ragpb --go-grpc_out=. --go-grpc_opt=module=rag-mvp/backend/go-api --go-grpc_opt=Mrag/v1/rag_service.proto=rag-mvp/backend/go-api/internal/ragpb rag/v1/rag_service.proto
```

本次联调同时修复既有 Search Guard bulk 分片权限，见 `docs/bug/2026-09-06-search-guard-bulk-shard-permission.md`。权限仅对 `rag-chunks-v1*` 生效。

## 本次体验迭代

- 多文件及文件夹选择，单批最多 1000 个、单文件 32 MiB；逐文件提交，失败独立重试并复用幂等键。目录层级只用于选择列表展示，资料作为平面文档存放；不支持的格式/空文件跳过。
- 回答 Markdown 支持标题、列表、表格、代码块；禁用原始 HTML 并清洗结果，外部图片转为链接。引用仍显示完整原始 chunk。
- 历史会话放入右侧抽屉，按最近消息时间（空会话用创建时间）倒序；支持恢复、新会话和 Escape 关闭。
- 加密密钥是基础设施 Secret，并非模型配置。Go 文件 encryption.key 使用 0640；Python 只读挂载并加入组 10001，JWT 文件保持 0600。两份 Compose 均把共享密钥卷标记 external，避免 down -v 意外删密钥。外部密钥部署必须给 Go/Python 提供同一份 key，不能只设置 Go 环境变量。
- 本地 TUN Fake-IP 返回 198.18.0.0/15 时，通过固定 HTTPS Cloudflare DNS 地址 1.1.1.1 重新解析，仍只允许公网 IP，并固定实际连接 IP、保留原始 TLS SNI。普通内网/链路本地地址始终拒绝。该回退只发送模型域名，不发送凭据。
- 旧 `.env` 可用 `cmd/import-embedding --env-file ../../.env` 一次性迁移（在 backend/go-api 运行，并设置 PRODUCT_MYSQL_DSN）；仅恰有一个 Chat 配置时允许自动选用户，已有 Embedding 密钥则不覆盖。工具不会修改源文件，迁移后实际运行不再依赖它。真实 Python 模型测试仍可显式使用测试专用环境配置。
- 真实 MySQL 套件会 TRUNCATE 测试表，不能指向用户数据所在的 rag 库；本次使用独立 rag_acceptance_20260906 库。完整 Docker KILL/resilience 套件未在用户运行栈执行；离线韧性门禁已运行。
