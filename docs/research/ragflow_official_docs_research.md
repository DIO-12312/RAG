# RAGFlow 官方文档研究底稿（架构分析配套）

> 研究日期：2026-08-22（Asia/Shanghai）  
> 文档版本范围：RAGFlow `DEV` 文档与当前稳定版 `v0.27.0`。文档站的版本选择器当前列出 `DEV` 与 `0.27.0`；`v0.27.0` 发布于 2026-08-19。[User Guides 分类页](https://ragflow.io/docs/category/user-guides) · [Release notes](https://ragflow.io/docs/dev/release_notes#v0270)
> 源码核对基线：官方 `main` 提交 [`f796721ff25f0f86e4499c166f0c49228d5f6ad7`](https://github.com/infiniflow/ragflow/tree/f796721ff25f0f86e4499c166f0c49228d5f6ad7)。

## 1. 研究边界与使用方法

本底稿只使用两类第一方材料：RAGFlow 官方文档站和 `infiniflow/ragflow` 官方仓库中的文档/源码链接。官方文档自身也提示：二次开发和代码理解应以官方文档与官方源码为最新依据，DeepWiki 可能滞后。[Developer Guides](https://ragflow.io/docs/category/developer-guides) · [官方源码](https://github.com/infiniflow/ragflow)

官方文档最适合证明以下事实：部署拓扑、配置入口、用户可见的数据摄取/检索/聊天/Agent 流程、公开 HTTP/Python API 契约、版本演进与迁移要求。它不能代替源码级架构分析：尤其是 Go/Python 双运行时、包级调用关系、异步队列内部实现和测试分层，必须回到仓库源码、测试目录、构建脚本与 CI 工作流验证。[Launch Service from Source](https://ragflow.io/docs/dev/launch_ragflow_from_source) · [Contribution Guidelines](https://ragflow.io/docs/dev/contributing)

## 2. 官方文档导航所体现的产品边界

当前 User Guides 将产品能力分为 Dataset、Chat、Search、Agents、Memory、File Management、Team、Models、Data Sources、Chat Channels、Knowledge Compilation 等域。这说明 RAGFlow 已不是单一的“上传文件—问答”组件，而是围绕知识资产、检索应用、工作流、记忆、外部数据源和团队治理构建的平台。[User Guides](https://ragflow.io/docs/category/user-guides)

Developer Guides 当前包含 MCP、从源码启动、切换文档引擎、获取 API Key、构建 Docker 镜像和贡献指南。这个分类反映了官方支持的主要扩展面：协议集成、源码调试、存储后端替换、API 接入与容器构建。[Developer Guides](https://ragflow.io/docs/category/developer-guides)

References 提供 HTTP API、Python API 和术语表；公开 API 的资源边界覆盖 Dataset、Document、Chunk、Chat Assistant、Session、Agent、Memory 和 System Health。[References](https://ragflow.io/docs/dev/category/references) · [HTTP API](https://ragflow.io/docs/dev/http_api_reference) · [Python API](https://ragflow.io/docs/dev/python_api_reference)

### 2.1 官方源码给出的语言与目录基线

当前官方仓库根 `AGENTS.md` 明确把主技术栈描述为：Python 3.13+、Quart API、Peewee ORM、异步 Worker；前端为 React + TypeScript + Vite；同时有规模较大的 Go Module，负责 Server、Ingestion、Parser/Runtime、CLI 和支撑服务。常见运行依赖为 MySQL/PostgreSQL、Redis、MinIO、Elasticsearch/Infinity/OpenSearch。[官方源码 — Current stack](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/AGENTS.md#current-stack)

同一文件给出的顶层代码职责基线是：`api/` 为 Python API/Blueprint/Service/DB，`rag/` 为 Ingestion/Retrieval/LLM/GraphRAG，`deepdoc/` 为解析/OCR，`agent/` 为工作流 Canvas/组件/工具/模板；`cmd/` 是 Go 服务与 CLI 入口，`internal/` 是 Go 主应用；`web/`、`docker/`、`sdk/`、`test/` 分别承载前端、部署、SDK 和自动化测试。[官方源码 — Code layout](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/AGENTS.md#code-layout-to-expect)

官方还把 `internal/ingestion`、`internal/parser`、`internal/deepdoc` 标为 Go 的活跃重构区，并要求收敛重复路径。这是“Go 正在接管摄取/解析运行时”的强迁移信号，但不是“Python 路径已经退役”的证据。[官方源码 — Go-specific rules](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/AGENTS.md#go-specific-rules)

## 3. 部署架构与运行时服务

### 3.1 Docker 入口与资源要求

Quickstart 把 RAGFlow 定义为基于深度文档理解的开源 RAG 引擎，目标是处理复杂格式数据并给出带依据引用的问答；官方入门主链路为“启动服务 → 创建 Dataset → 检查/干预解析结果 → 建立 AI Chat”。[Quickstart](https://ragflow.io/docs/dev/)

官方 Docker 快速部署的最低条件为 x86 CPU 4 核、16 GB RAM、50 GB 磁盘、Docker 24.0.0+、Docker Compose v2.26.1+；当前文档还要求 Python 3.13+。官方支持 x86 CPU 与 Nvidia GPU；ARM64 会测试但不提供官方 ARM 镜像，需要自行构建。[Quickstart](https://ragflow.io/docs/dev/#prerequisites) · [FAQ](https://ragflow.io/docs/dev/faq#which-architectures-or-devices-does-ragflow-support)

Quickstart 要求 `vm.max_map_count >= 262144`，原因是 Elasticsearch 的多路召回需要足够的内存映射；默认外部访问由 Nginx 暴露在 HTTP 80，RAGFlow API 服务在容器内/宿主暴露配置中使用 9380。[Quickstart](https://ragflow.io/docs/dev/#start-up-the-server) · [Configuration](https://ragflow.io/docs/dev/configurations#ragflow)

当前稳定镜像是 `infiniflow/ragflow:v0.27.0`，压缩下载体积约 2 GB，解压运行后约 7 GB；`nightly` 是不稳定的最新构建。[Quickstart](https://ragflow.io/docs/dev/#start-up-the-server)

### 3.2 Compose 服务拓扑

官方把 Compose 文件分成两层：`docker-compose.yml` 启动 RAGFlow 及依赖，`docker-compose-base.yml` 只启动 Elasticsearch/Infinity、MySQL、MinIO、Redis 等基础服务。[Configuration](https://ragflow.io/docs/dev/configurations#docker-compose)

各依赖在官方文档中的职责可以整理为：

| 服务 | 文档明确职责或可观察边界 | 官方证据 |
|---|---|---|
| Elasticsearch / Infinity | 文档引擎，保存全文和向量并支持混合检索；默认 Elasticsearch，可切换 Infinity | [Switch Document Engine](https://ragflow.io/docs/dev/switch_doc_engine) |
| MySQL | RAGFlow 关系数据库；`service_conf` 可配置数据库名、连接数和超时 | [Configuration — mysql](https://ragflow.io/docs/dev/configurations#mysql) |
| MinIO | 对象存储，保存和管理上传文件 | [Configuration — MinIO](https://ragflow.io/docs/dev/configurations#minio) |
| Redis | 运行时依赖，配置主机、DB、ACL 用户和密码；FAQ 的任务队列与同义词故障排查也通过 Redis 观察 | [Configuration — redis](https://ragflow.io/docs/dev/configurations#redis) · [FAQ — task queue](https://ragflow.io/docs/dev/faq#xxx-tasks-are-ahead-in-the-queue) |
| RAGFlow API | 内部默认监听 `0.0.0.0:9380`，外部 API 端口由 `SVR_HTTP_PORT` 控制 | [Configuration — RAGFlow](https://ragflow.io/docs/dev/configurations#ragflow-1) |
| Nginx | Docker 默认 HTTP 入口；源码调试文档要求注释掉 entrypoint 中的 Nginx，再单独启动前后端 | [Quickstart](https://ragflow.io/docs/dev/#start-up-the-server) · [Launch Service from Source](https://ragflow.io/docs/dev/launch_ragflow_from_source#launch-the-ragflow-backend-service) |
| TEI（可选） | 独立 Embedding 服务，默认可选 `Qwen/Qwen3-Embedding-0.6B`，外部端口默认 6380 | [Configuration — Embedding Service](https://ragflow.io/docs/dev/configurations#embedding-service) |

FAQ 说明 RAGFlow 选择全文检索能力较强的文档引擎：它认为 Elasticsearch 与 Infinity 满足混合检索、短语搜索和高级排序要求，而多数纯向量数据库的全文能力不足。[FAQ — document engine](https://ragflow.io/docs/dev/faq#why-not-use-other-open-source-vector-databases-as-the-document-engine)

但当前 Release notes 已声明 `v0.27.0` 新增 SereneDB 文档存储引擎支持，而 FAQ 与“切换文档引擎”页面仍只描述 Elasticsearch/Infinity。这是明确的文档时差/口径不一致：架构报告中应把 SereneDB 标记为“最新发布说明宣称支持，具体成熟度与接线方式需源码验证”。[Release notes v0.27.0](https://ragflow.io/docs/dev/release_notes#infrastructure) · [Switch Document Engine](https://ragflow.io/docs/dev/switch_doc_engine)

FAQ 还记录了 v0.26.0 前后的 Redis Stream 名称变化：旧队列为 `rag_flow_svr_queue` / `rag_flow_svr_task_broker`，新队列为 `te.0.common_queue` / `te.0.common_task_broker`。这是一条明确的 Task Executor 队列基础设施重构信号；具体生产/消费协议仍需源码验证。[FAQ — task queue](https://ragflow.io/docs/dev/faq#xxx-tasks-are-ahead-in-the-queue)

### 3.3 从源码启动揭示的经典运行时

当前官方“从源码启动”步骤先用 Compose 启动 MinIO、Elasticsearch、Redis、MySQL，然后分别启动两个 Python 进程：`python rag/svr/task_executor.py -i 1`（任务执行器）与 `python api/ragflow_server.py`（API 服务）；前端进入 `web/` 后用 `npm install`、`npm run dev` 启动，并代理到 `127.0.0.1:9380`。[Launch Service from Source](https://ragflow.io/docs/dev/launch_ragflow_from_source#launch-third-party-services) · [后端](https://ragflow.io/docs/dev/launch_ragflow_from_source#launch-the-ragflow-backend-service) · [前端](https://ragflow.io/docs/dev/launch_ragflow_from_source#launch-the-ragflow-frontend-service)

因此，官方开发文档仍把 Python API Server + Python Task Executor 描述为源码调试主路径。它没有解释仓库中新增 Go 服务的入口和迁移边界；Go/Python 的实际职责不能仅凭该文档下结论，必须结合 `cmd/`、`internal/`、`api/`、`rag/` 等源码分析。[Launch Service from Source](https://ragflow.io/docs/dev/launch_ragflow_from_source) · [官方源码](https://github.com/infiniflow/ragflow)

## 4. 配置系统设计

### 4.1 三个核心配置入口

Docker 部署需要同时理解三个文件：`.env` 保存 Compose 环境变量；`service_conf.yaml.template` 描述 API Server 与 Task Executor 使用的后端系统配置，容器启动时将环境变量替换进去并生成最终 YAML；`docker-compose.yml` 定义 RAGFlow 服务和容器编排。修改后需重启容器生效。[Configuration — Guidelines](https://ragflow.io/docs/dev/configurations#guidelines)

这构成一条两阶段配置链：

```text
.env / 宿主环境变量
        │
        ▼
service_conf.yaml.template ──容器启动时展开──> service_conf.yaml
        │                                      │
        └──────── docker-compose.yml ──────────┘
                         │
                         ▼
                  API Server / Task Executor / dependencies
```

该链路由官方 Configuration 对三个文件的定义直接支持；具体模板替换函数与加载优先级仍应以源码为准。[Configuration — Guidelines](https://ragflow.io/docs/dev/configurations#guidelines)

### 4.2 关键环境变量与端口

| 域 | 关键配置 | 当前官方默认/含义 | 官方证据 |
|---|---|---|---|
| Elasticsearch | `STACK_VERSION`, `ES_PORT`, `ELASTIC_PASSWORD` | ES 默认 8.11.3；宿主暴露端口默认 1200 | [Configuration — Elasticsearch](https://ragflow.io/docs/dev/configurations#elasticsearch) |
| 资源限制 | `MEM_LIMIT` | 单容器内存上限默认 8,073,741,824 bytes | [Configuration — Resource Management](https://ragflow.io/docs/dev/configurations#resource-management) |
| MySQL | `MYSQL_PORT`, `EXPOSE_MYSQL_PORT`, `MYSQL_PASSWORD` | 容器连接端口默认 3306，宿主暴露默认 5455 | [Configuration — MySQL](https://ragflow.io/docs/dev/configurations#mysql) |
| MinIO | `MINIO_PORT`, `MINIO_CONSOLE_PORT`, 用户与密码 | API 默认 9000，控制台默认 9001 | [Configuration — MinIO](https://ragflow.io/docs/dev/configurations#minio) |
| Redis | `REDIS_PORT`, `REDIS_USERNAME`, `REDIS_PASSWORD` | 默认 6379，支持 Redis 6+ ACL 用户 | [Configuration — Redis](https://ragflow.io/docs/dev/configurations#redis) |
| RAGFlow | `SVR_HTTP_PORT`, `RAGFLOW_IMAGE` | API 外部端口默认 9380；镜像默认 v0.27.0 | [Configuration — RAGFlow](https://ragflow.io/docs/dev/configurations#ragflow) |
| 运行环境 | `TZ`, `HF_ENDPOINT`, `REGISTER_ENABLED` | 时区默认 Asia/Shanghai；可配置 Hugging Face 镜像；用户注册默认开启 | [Configuration — Timezone](https://ragflow.io/docs/dev/configurations#timezone) · [HF mirror](https://ragflow.io/docs/dev/configurations#hugging-face-mirror-site) · [Registration](https://ragflow.io/docs/dev/configurations#user-registration) |

`service_conf.yaml.template` 还配置 `ragflow`、`mysql`、`minio`/S3、`redis`、OAuth 与新用户默认 LLM。S3/Tigris 等外部对象存储可替代 MinIO；使用外部存储后可从基础 Compose 中移除 MinIO 服务。[Configuration — Service Configuration](https://ragflow.io/docs/dev/configurations#service-configuration) · [S3](https://ragflow.io/docs/dev/configurations#s3-tigris)

### 4.3 文档引擎切换与数据风险

官方默认用 Elasticsearch 保存全文与向量；切换 Infinity 的操作是停止容器、把 `.env` 中 `DOC_ENGINE` 改为 `infinity`、再启动。官方示例的停止命令带 `-v`，会删除 Docker volumes 并清空现有数据，因此该操作实质上不是无损热切换。[Switch Document Engine](https://ragflow.io/docs/dev/switch_doc_engine)

升级文档反复强调：普通升级本身不删除数据，但 `docker compose ... down -v` 会删除卷；升级必须同步更新 Git 代码与 Docker 镜像，不能只换其中一项。[Upgrading](https://ragflow.io/docs/dev/upgrade_ragflow)

## 5. 数据摄取、解析、切块和索引

### 5.1 领域对象与主流程

官方将 Dataset 定义为承载知识源与检索内容的工作空间；用户在 Dataset 中导入文件、解析文件、切分 Chunk、维护元数据并验证召回，Chat、Search 和 Agent 再消费这些内容。[Dataset Overview（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/dataset_overview.md)

Quickstart 的标准摄取链是：创建 Dataset → 选择 Embedding 模型和 Chunk 方法 → 上传文件 → 显式启动解析 → 查看 Chunk → 手工修订关键词/问题 → Retrieval Testing。已用于解析的 Dataset 必须保持相同 Embedding 空间，官方 UI 不允许随意更换 Embedding 模型。[Quickstart — Create your first dataset](https://ragflow.io/docs/dev/#create-your-first-dataset) · [Intervene with file parsing](https://ragflow.io/docs/dev/#intervene-with-file-parsing)

公开 HTTP API 展示了更细的状态机。文档上传后可处于 `UNSTART`；文档列表支持 `UNSTART / RUNNING / CANCEL / DONE / FAIL`；解析进度示例依次记录“任务接收 → 页解析 → 生成 Chunk → Embedding → Indexing → 完成”。[Upload documents](https://ragflow.io/docs/dev/http_api_reference#upload-documents) · [List documents](https://ragflow.io/docs/dev/http_api_reference#list-documents) · [Update document](https://ragflow.io/docs/dev/http_api_reference#update-document)

```text
Document created (UNSTART)
        │ POST parse / ingest
        ▼
Task received (RUNNING)
        ▼
Parse pages / extract structure
        ▼
Generate chunks
        ▼
Embed chunks
        ▼
Index full text + vectors
        ▼
DONE  ──或──> CANCEL / FAIL
```

### 5.2 内置解析与自定义 Ingestion Pipeline

Dataset 有两类解析入口：`Built-in` 使用内置解析/切块规则；`Pipeline` 选择在 Agent 页面预先创建的自定义 Ingestion Pipeline。自定义 Pipeline 适合文档处理逻辑或清洗流程需要编排的场景。[Dataset Configuration（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/configuration.md)

官方把 Ingestion Pipeline 拆成四个核心组件：Parser 读取 PDF、图像、邮件等并抽取文本与结构；Transformer 用 AI 添加摘要、关键词或问题；Chunker 将长文本切成适合检索的片段；Indexer 把结果写入文档引擎，支持全文与向量混合检索。[Core Ingestion Pipeline Components](https://ragflow.io/docs/dev/understand_core_ingestion_pipeline_components)

公开 API 也保持两条入口的区分：内置 Chunk Pipeline 使用 `POST /api/v1/datasets/{dataset_id}/chunks`；配置了自定义 Pipeline 的文档使用 `POST /api/v1/documents/ingest`，后者支持启动、取消、重跑，并可在重跑前删除现有任务和 Chunk。[Parse documents](https://ragflow.io/docs/dev/http_api_reference#parse-documents) · [Ingest documents](https://ragflow.io/docs/dev/http_api_reference#ingest-documents)

### 5.3 内置解析策略

当前 Dataset 配置文档列出的内置策略包括 General、Q&A、Manual、Table、Paper、Book、Laws、Presentation、One、Tag。它们不是简单 UI 预设，而是针对普通文档、问答对、手册、表格、论文、书籍、法规、幻灯片、整文和标签集的不同结构假设。[Dataset Configuration（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/configuration.md)

PDF Parser 可选择 DeepDoc、Naive、Docling、TCADPParser 或符合条件的 VLM。DeepDoc 执行 OCR、表格结构识别和版面理解，适合扫描件/复杂排版/图表但更慢；Naive 适合纯文本 PDF，跳过这些视觉处理以减少开销。[Dataset Configuration（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/configuration.md)

FAQ 从工程代价角度解释了同一现象：RAGFlow 比轻量框架解析更慢、资源占用更大，是因为预处理包含版面分析、表格结构识别、OCR 和内置文档结构模型。[FAQ — parsing time](https://ragflow.io/docs/dev/faq#why-does-it-take-longer-for-ragflow-to-parse-a-document-than-langchain) · [FAQ — resources](https://ragflow.io/docs/dev/faq#why-does-ragflow-require-more-resources-than-other-projects)

### 5.4 Chunk 设计与可干预性

Chunk 是检索和问答的基本知识单元。官方管理界面允许查看来源位置、正文、关键词、问题和启用状态，也允许搜索、编辑、新增、禁用和删除；禁用会保留 Chunk 但不参与检索，删除则从解析结果移除。[Chunk Management（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/chunk_parsing_results_and_knowledge_fragment_management.md)

核心切块参数包括推荐 Chunk 大小、文本分隔符、相邻 Chunk overlap、是否使用更细的 child chunk 参与检索；图像/表格还可配置上下文窗口。小 Chunk 提高粒度但可能丢上下文，大 Chunk 保留上下文但召回粒度变粗，过高 overlap 会制造重复内容。[Dataset Configuration（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/configuration.md)

内容增强包括 Auto Metadata、Auto Keyword 和 Auto Question。自动元数据配置只影响后续新解析的文档，既有文档需要重新解析；表格列还能被设为 Indexing、Metadata 或 Both，以控制哪些字段进入全文/向量检索、哪些字段只做过滤。[Dataset Configuration（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/configuration.md)

## 6. 检索设计

### 6.1 混合检索和公开参数

AI Search 明确使用“加权关键词相似度 + 加权向量相似度”的混合策略，并用系统默认 Chat 模型生成单轮回答；结果 Chunk 按相似度展示。AI Search 不使用知识图谱、Auto Keyword、Auto Question 等高级策略。[AI Search](https://ragflow.io/docs/dev/ai_search)

公开检索 API `POST /api/v1/retrieval` 支持：指定 Dataset/Document、分页、相似度阈值、向量权重、候选 `top_k`、Rerank 模型、关键词匹配、高亮、跨语言、元数据条件、知识图谱和目录增强。默认相似度阈值为 0.2，向量权重为 0.3，向量计算候选 `top_k` 为 1024。[Retrieve chunks](https://ragflow.io/docs/dev/http_api_reference#retrieve-chunks)

没有 Rerank 时，综合分数由关键词/全文与向量余弦相似度组成；启用 Rerank 后会增加模型调用与延迟。跨多个 Dataset 检索时，所选内容必须处于兼容的 Embedding 空间。[Retrieval Testing](https://ragflow.io/docs/dev/run_retrieval_test) · [Retrieval Component](https://ragflow.io/docs/dev/basic_component#retrieval-component) · [Retrieve chunks](https://ragflow.io/docs/dev/http_api_reference#retrieve-chunks)

检索 API 返回的不只是正文，还包括 Chunk ID、Document ID、Dataset ID、来源文档聚合、位置、总相似度、term similarity 与 vector similarity，这为引用溯源和检索诊断提供了公开的数据契约。[Retrieve chunks — Response](https://ragflow.io/docs/dev/http_api_reference#retrieve-chunks)

### 6.2 Retrieval Testing 是“检索验收”，不是代码单测

官方建议在 Chat、Search、Agent 之前，用真实典型问题检查目标 Chunk 是否召回、内容是否完整、排序是否合理。若 Chunk 已正确召回而最终回答仍差，应检查模型、Prompt 或应用配置；若未召回，应按“解析 → Chunk → Metadata → 检索参数”定位。[Retrieval Testing（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/retrieval_testing.md)

Retrieval Testing 可调相似度阈值、向量权重、Rerank、跨语言、Metadata 和 Top，但这些参数只影响当前测试，不会自动同步到 Chat Assistant 或 Agent；确定参数后必须在实际应用或 Retrieval 组件中再配置。[Retrieval Testing（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/retrieval_testing.md)

因此，Retrieval Testing 应被架构报告归类为“面向知识库质量的人工/交互式验收工具”，不能与仓库中的 unit/integration/e2e 自动化测试混为一谈。[Retrieval Testing](https://ragflow.io/docs/dev/run_retrieval_test) · [Contribution Guidelines](https://ragflow.io/docs/dev/contributing)

## 7. Chat、Search 与 Agent

### 7.1 Chat

Chat 基于一个或多个 Dataset，通常要求先完成文件解析和 Retrieval Testing。Chat Assistant 可以配置 Dataset、Chat 模型、Prompt、检索阈值、向量权重、Top N、Multi-turn Optimization、Rerank、知识图谱等。[Start AI Chat](https://ragflow.io/docs/dev/start_chat)

Quickstart 特别揭示了无召回时的产品策略：若配置 Empty response，回答会被约束在 Dataset 范围；若留空，模型可自由补答，但会增加幻觉风险。[Quickstart — Set up an AI chat](https://ragflow.io/docs/dev/#set-up-an-ai-chat)

HTTP `POST /api/v1/chat/completions` 同时支持三种模式：不传 `chat_id` 时直接用租户默认模型；传 `chat_id` 不传 `session_id` 时使用 Assistant 配置并自动建 Session；两者都传时继续已有 Session。流式输出默认开启，服务端保存的历史与客户端提交完整历史可由 `pass_all_history_messages` 控制。[Converse with chat assistant](https://ragflow.io/docs/dev/http_api_reference#converse-with-chat-assistant)

FAQ 确认 Chat Assistant 和 Agent 的 UI 默认流式输出，若要关闭必须通过 Python 或 REST API；多轮查询改写由 Multi-turn Optimization 提供。[FAQ — stream output](https://ragflow.io/docs/dev/faq#do-you-support-stream-output) · [FAQ — multi-turn](https://ragflow.io/docs/dev/faq#do-you-support-multiple-rounds-of-dialogues-referencing-previous-dialogues-as-context-for-the-current-query)

### 7.2 Search 与 Chat 的边界

AI Search 是单轮、固定混合检索策略、使用系统默认模型，并显示召回 Chunk；AI Chat 是多轮、检索策略和模型可配置，可启用知识图谱、Auto Keyword、Auto Question 等高级 RAG 能力，但默认回答界面不把召回 Chunk 列在答案下方。[AI Search — FAQ](https://ragflow.io/docs/dev/ai_search#frequently-asked-questions) · [FAQ — Search vs Chat](https://ragflow.io/docs/dev/faq#key-differences-between-ai-search-and-chat)

官方建议用 AI Search 作为 Chat Assistant 调试参照，核对模型与检索策略。这意味着产品本身提供“隔离检索/生成问题”的诊断路径。[AI Search](https://ragflow.io/docs/dev/ai_search)

### 7.3 Agent

Agent 是 RAGFlow 的业务工作流编排能力：用户在无代码 Canvas 上连接组件，支持顺序执行、条件分支、分类路由与循环。典型能力包括知识库问答、意图路由、HTTP/数据库/MCP/自定义代码调用、长文本批处理与 Session Memory。[Agent Overview](https://ragflow.io/docs/dev/agent_overview)

Chat 更适合知识库问答和多轮对话；Agent 适合条件分支、工具调用、数据处理和多步编排。Retrieval 既可作为普通流程节点，也可注册为 Agent 的工具，让 LLM 自主决定何时检索。[Agent Overview — Relationship](https://ragflow.io/docs/dev/agent_overview#relationship-between-agent-and-knowledge-base-qa)

Agent 组件可调用 Retrieval、SQL、HTTP、MCP 和子 Agent；支持重试、错误延迟、反思轮次、引用和 JSON Schema 结构化输出。工具、子 Agent、更多反思轮次和更大的消息窗口都会增加延迟。[Basic Component — Agent](https://ragflow.io/docs/dev/basic_component#agent-component)

## 8. API 与 SDK 设计

HTTP API 使用 Bearer API Key；Python SDK 可通过 `pip install ragflow-sdk` 安装，并同样需要 RAGFlow API Key。[HTTP API](https://ragflow.io/docs/dev/http_api_reference) · [Python API](https://ragflow.io/docs/dev/python_api_reference) · [Acquire API key](https://ragflow.io/docs/dev/acquire_ragflow_api_key)

公开 API 的资源边界与典型动作如下：

| 资源 | 典型能力 | 官方证据 |
|---|---|---|
| Dataset | 创建、删除、更新、列表、GraphRAG、RAPTOR | [HTTP API — Dataset Management](https://ragflow.io/docs/dev/http_api_reference#dataset-management) |
| Document | 上传、下载、更新、列表、删除、启动/停止解析、自定义 Pipeline 摄取 | [HTTP API — File Management](https://ragflow.io/docs/dev/http_api_reference#file-management-within-dataset) |
| Chunk | 新增、查询、更新、启停、删除、Metadata 管理、跨 Dataset 检索 | [HTTP API — Chunk Management](https://ragflow.io/docs/dev/http_api_reference#chunk-management-within-dataset) |
| Chat Assistant | 创建、查询、更新、删除，绑定 Dataset 与模型配置 | [HTTP API — Chat Assistant](https://ragflow.io/docs/dev/http_api_reference#chat-assistant-management) |
| Session | Chat/Agent Session 生命周期、消息反馈、对话、TTS/STT、相关问题 | [HTTP API — Session](https://ragflow.io/docs/dev/http_api_reference#session-management) |
| Agent | 创建、列表、更新、删除、流式对话 | [HTTP API — Agent Management](https://ragflow.io/docs/dev/http_api_reference#agent-management) |
| Memory | Memory CRUD、消息添加/遗忘/状态更新/搜索/最近消息 | [HTTP API — Memory](https://ragflow.io/docs/dev/http_api_reference#memory-management) |
| System | 健康检查 | [HTTP API — System](https://ragflow.io/docs/dev/http_api_reference#system) |

HTTP API 还提供 OpenAI-compatible Chat Completion 与 Agent Completion；架构上这是一层兼容外观，底层仍可路由到租户默认模型、Chat Assistant 或 Agent 会话。[HTTP API — OpenAI-Compatible](https://ragflow.io/docs/dev/http_api_reference#openai-compatible-api)

API 版本演进不是完全无破坏：当前 Chat Completion 文档已弃用旧的 `/api/v1/chats/{chat_id}/completions`，统一到 `/api/v1/chat/completions`；`legacy` 参数用于恢复 v0.23.0 的累计流式输出与 `<think>` 兼容行为。[Converse with chat assistant](https://ragflow.io/docs/dev/http_api_reference#converse-with-chat-assistant)

## 9. 测试、验收与可观测性

### 9.1 官方文档明确说明的自动化测试要求

源码调试文档提供测试依赖安装命令：`uv sync --python 3.13 --group test --frozen && uv pip install sdk/python --group test`。它证明 Python 服务测试依赖被单独分组，但没有定义具体测试目录、标签或覆盖率阈值。[Launch Service from Source — Python Dependencies](https://ragflow.io/docs/dev/launch_ragflow_from_source#install-python-dependencies)

贡献指南要求新功能补充测试用例，并要求 PR 合并前通过全部 CI 测试；它还建议大 PR 拆成小而独立的 PR、破坏性/API 变化提供设计细节。[Contribution Guidelines](https://ragflow.io/docs/dev/contributing)

官方文档站没有给出完整的 Unit / Integration / E2E / Manual 分类，也没有解释 Go build tags、Python pytest markers、前端测试或 Golden Test。测试架构的细节必须从仓库的 `test/`、Go `*_test.go`、`web/` 测试、`build.sh`、`pyproject.toml` 与 `.github/workflows/` 读取。[Contribution Guidelines](https://ragflow.io/docs/dev/contributing) · [官方源码](https://github.com/infiniflow/ragflow)

### 9.2 官方源码规定的 Go 测试分层

当前官方根 `AGENTS.md` 用 build tag 定义了四级测试，并把 CGO 作为正交维度：[Go Test Tiers](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/AGENTS.md#go-test-tiers)

| 层级 | Build tag | 默认运行 | 官方定义的依赖/目标 |
|---|---|---:|---|
| Unit | 无 | 是 | 不得连接外部服务；使用内存 SQLite、miniredis、`httptest` 等；通过 `build.sh --test` 接入原生静态库 |
| Integration | `integration` | 否 | 一个真实 MySQL/MinIO/Elasticsearch/Infinity/LLM 服务，单组件、相对快速 |
| E2E | `e2e` | 否 | 真实服务上的完整 `ingest → index → retrieve` 跨组件链路，较重较慢 |
| Manual | `manual` | 否 | DeepDoc render/parity/snapshot/benchmark 等极慢或昂贵测试，只允许本地 opt-in，禁止进入 CI |
| Native（正交） | `cgo` / `!cgo` | 取决于构建 | `office_oxide`、`pdfium`、`pdf_oxide` 等原生静态库，可与上述 tag 组合 |

官方要求：任何真实外部服务测试必须显式标记为 Integration/E2E/Manual，不得仅靠 `t.Skip` 或环境变量逃逸默认单测；Manual 永不进入 CI。默认 Unit 即使无外部服务，编译阶段仍可能需要原生 CGO 静态库，因此官方推荐 `build.sh --test`，而不是裸 `go test`。[Go Test Tiers](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/AGENTS.md#go-test-tiers) · [build.sh](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/build.sh)

官方命令契约为：Python `uv run pytest`；Web `npm run test` 并配合 lint/type-check；Go 使用 `build.sh --test`、`--test-integration`、`--test-e2e`、`--test-manual`、`--test-all`，其中 `--test-all` 只包含 Integration + E2E，不包含 Manual。[官方源码 — Commands](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/AGENTS.md#commands) · [build.sh test options](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/build.sh#L503-L536)

### 9.3 产品级验收与调试工具

Retrieval Testing 是最重要的产品级知识质量验收：它检验目标 Chunk、完整性、排序、来源、无关召回，并允许反复调整阈值、权重、Rerank、语言、Metadata 和 Top。[Retrieval Testing（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/retrieval_testing.md)

Chunk 页面提供“解析结果对照原文”的人工验收入口；用户可修正文档解析生成的正文、关键词、问题、标签并控制 Chunk 是否参与检索。[Chunk Management（官方源码文档）](https://github.com/infiniflow/ragflow/blob/f796721ff25f0f86e4499c166f0c49228d5f6ad7/docs/guides/dataset/chunk_parsing_results_and_knowledge_fragment_management.md)

RAGFlow 0.18.0+ 集成 Langfuse。官方 tracing 文档称每个请求可观察整体 trace，以及 retrieval、ranking、generation spans，并记录 Prompt、召回文档和 LLM Response；这适合做线上回归定位和性能分析，不替代确定性的代码测试。[Tracing](https://ragflow.io/docs/dev/tracing)

Ingestion Pipeline Canvas 还支持上传样本并逐节点观察 Parser、Chunker、Transformer、Indexer 输出；它是流程级调试/验收入口，与自动化 E2E 的作用互补。[Pipeline Test Run](https://ragflow.io/docs/dev/test_run)

### 9.4 Release notes 可转化为回归测试清单

`v0.27.0` 修复项集中暴露了高风险边界：大文件解析/上传导致服务挂起、Dataset/Document 删除阻塞其他 API、非法查询串破坏 Search、Chat 切换导致 SSE 中断或旧消息串会话、分隔符被转义、中文文件名 PDF 预览、DOCX/CSV/TAB 表格解析缺失等。这些应成为并发、状态隔离、取消、字符编码、结构解析和流式协议回归测试的重点。[Release notes v0.27.0 — Bug fixes](https://ragflow.io/docs/dev/release_notes#bug-fixes)

`v0.26.3` 引入批量上传“部分成功”语义，避免一个失败文件使整批丢弃；同时公开自定义 Pipeline 的 Ingest Documents API。这两点应形成 API 契约测试：逐文件结果不可因单项失败整体回滚，自定义 Pipeline 入口必须与 Built-in Parse 入口区分。[Release notes v0.26.3](https://ragflow.io/docs/dev/release_notes#v0263) · [Ingest documents](https://ragflow.io/docs/dev/http_api_reference#ingest-documents)

## 10. 当前版本与迁移信号

### 10.1 v0.27.0 当前能力

当前 Release notes 的最新稳定版是 `v0.27.0`（2026-08-19）。主要新增包括文档级/Dataset 级 Knowledge Compilation（Wiki、Graph、Tree、Page Index、Mind Map、Timeline、To Skills）、四档思考强度的 Agentic RAG、重做 Model Provider 系统；基础设施增加 GaussDB 适配器、SereneDB 文档存储、Tenki Sandbox，并升级 Infinity 到 0.7.3。[Release notes v0.27.0](https://ragflow.io/docs/dev/release_notes#v0270)

### 10.2 关键演进节点

| 版本 | 架构/接口信号 | 官方证据 |
|---|---|---|
| v0.21.0 | 引入可编排 Ingestion Pipeline；GraphRAG/RAPTOR 从自动增量构建改为手工批量构建；引入 Admin CLI | [Release notes v0.21.0](https://ragflow.io/docs/dev/release_notes#v0210) |
| v0.22.0 | Docker 只发布 slim 版且移除 `-slim` 后缀；增加 Admin Web UI；Pipeline 支持 Docling | [Release notes v0.22.0](https://ragflow.io/docs/dev/release_notes#v0220) |
| v0.23.0 | Agent 底层架构重构；支持结构化输出、多 Retrieval；Dataset 加入父子 Chunk、自动元数据和图表上下文窗口 | [Release notes v0.23.0](https://ragflow.io/docs/dev/release_notes#v0230) |
| v0.24.0 | Memory HTTP/Python API；多 Sandbox；OceanBase 可替代 MySQL | [Release notes v0.24.0](https://ragflow.io/docs/dev/release_notes#v0240) |
| v0.25.0 | 七个 Ingestion Pipeline 模板、可发布 Agent、Sandbox Code Execution；ES 升级 9.x，MinIO 镜像替换，加入数据库迁移脚本 | [Release notes v0.25.0](https://ragflow.io/docs/dev/release_notes#v0250) |
| v0.26.0 | Model Provider 支持同供应商多 API Key；增加企业数据源；GraphRAG 高成本阶段支持 checkpoint/resume | [Release notes v0.26.0](https://ragflow.io/docs/dev/release_notes#v0260) |
| v0.26.3 | 公开自定义 Pipeline Ingest API；批量上传支持部分成功 | [Release notes v0.26.3](https://ragflow.io/docs/dev/release_notes#v0263) |
| v0.27.0 | Knowledge Compilation、Agentic RAG、Model Provider 重做、GaussDB/SereneDB/Tenki | [Release notes v0.27.0](https://ragflow.io/docs/dev/release_notes#v0270) |

这些版本信号说明 RAGFlow 正从“固定文档解析 + Chat”演进为“可编排摄取 + 多类知识编译产物 + Agentic Runtime + 多基础设施适配器”。这是基于官方 Release notes 的架构趋势推断，不等同于源码模块已经全部完成统一或迁移。[Release notes](https://ragflow.io/docs/dev/release_notes)

### 10.3 数据库和部署迁移

升级必须让源码 tag 与 Docker image 版本一致；`nightly` 和指定 release 都要求先停服务、更新代码、更新 `RAGFLOW_IMAGE`、拉取镜像再启动。[Upgrading](https://ragflow.io/docs/dev/upgrade_ragflow)

RAGFlow 默认在启动时自动同步 Schema 和执行迁移；大规模 Kubernetes 环境可能因数据量大而超过 10 分钟、触发容器超时，因此官方提供 `mysql_migration.py` 和 `db_schema_sync.py` 在服务启动前手工迁移/对齐。[Database Schema and Migration](https://ragflow.io/docs/dev/database_schema_and_migration)

`mysql_migration.py` 用于把旧模型数据从统一表迁到 Provider / Instance / Model 多表结构，v0.25+ 尤其重要；`db_schema_sync.py` 比较 `api/db/db_models.py` 与数据库、生成迁移并支持 diff/执行，删除列需显式 `--drop`。[Database Schema and Migration](https://ragflow.io/docs/dev/database_schema_and_migration#mysql_migrationpy) · [Db_schema_sync](https://ragflow.io/docs/dev/database_schema_and_migration#db_schema_syncpy)

默认持久数据位于 Docker volumes，包括关系数据库、上传文件、搜索索引和 Redis 数据；官方迁移脚本对这些卷整体备份/恢复。备份与恢复前必须停服务，不能使用会删除卷的 `down -v`。[Backup & Migration](https://ragflow.io/docs/dev/migration#data-migration)

## 11. FAQ 对设计取舍的补充

RAGFlow 官方认为自己的核心差异化是细粒度文档解析（含图像和表格、允许人工干预）与可追踪引用、降低幻觉。这与 Quickstart 的 Chunk 预览/修改和带引用问答形成一致证据链。[FAQ — differentiation](https://ragflow.io/docs/dev/faq#what-sets-ragflow-apart-from-other-rag-products) · [Quickstart](https://ragflow.io/docs/dev/)

本地开源服务可以由 Python Client 或 REST API 调用；官方云站展示的是企业能力且不支持同样的本地开源 API 调用方式。架构分析必须区分 OSS 仓库与 cloud.ragflow.io 企业服务，不能把云端权限特性直接归到开源源码。[FAQ — cloud vs local](https://ragflow.io/docs/dev/faq#differences-between-cloudragflowio-and-a-locally-deployed-open-source-ragflow-service)

Hugging Face 不可访问会使 OCR 模型资源下载失败并导致 PDF 解析失败；官方支持通过 `HF_ENDPOINT` 切换镜像或手工挂载资源。这说明文档解析运行时依赖模型资源的可用性和缓存，部署/测试环境必须固定资源来源。[FAQ — Hugging Face](https://ragflow.io/docs/dev/faq#cannot-access-httpshuggingfaceco) · [Configuration — HF endpoint](https://ragflow.io/docs/dev/configurations#hugging-face-mirror-site)

## 12. 对最终源码级架构报告的直接建议

1. **部署图以 Configuration 为外层事实，以 Compose/源码为内层事实。** 文档可确定 ES/Infinity、MySQL、MinIO、Redis、API、Nginx 的公开拓扑；具体容器、健康检查、网络、队列实现需读取 Compose 与代码。[Configuration](https://ragflow.io/docs/dev/configurations)
2. **调用流程至少拆成 Built-in Parse 与 Custom Ingest 两条。** 两条公开 API 不同，且 Ingestion Pipeline 有 Parser → Transformer → Chunker → Indexer 四段。[Parse documents](https://ragflow.io/docs/dev/http_api_reference#parse-documents) · [Ingest documents](https://ragflow.io/docs/dev/http_api_reference#ingest-documents) · [Pipeline Components](https://ragflow.io/docs/dev/understand_core_ingestion_pipeline_components)
3. **检索流程以公开参数反推，但排序实现必须读源码。** 文档能证明混合检索、阈值、向量权重、Rerank、Metadata、Cross-language、KG/TOC；具体召回合并、分页和分数公式必须结合检索代码。[Retrieve chunks](https://ragflow.io/docs/dev/http_api_reference#retrieve-chunks)
4. **Chat、Search、Agent 分开画。** Search 是固定单轮检索；Chat 是有 Session 的可配置问答；Agent 是组件图执行器，可把 Retrieval 作为节点或工具。[AI Search](https://ragflow.io/docs/dev/ai_search) · [Start Chat](https://ragflow.io/docs/dev/start_chat) · [Agent Overview](https://ragflow.io/docs/dev/agent_overview)
5. **测试章节区分四类证据。** 源码自动化测试、CI 流程、产品 Retrieval Testing、Langfuse 线上 tracing 是不同层次，不能用交互式 Retrieval Testing 代替单元/E2E 测试覆盖。[Contribution Guidelines](https://ragflow.io/docs/dev/contributing) · [Retrieval Testing](https://ragflow.io/docs/dev/run_retrieval_test) · [Tracing](https://ragflow.io/docs/dev/tracing)
6. **显式标注文档时差。** `v0.27.0` 已声称支持 SereneDB，但 Switch/FAQ 仍按 ES/Infinity 描述；源码启动文档也仍只列 Python API/Task Executor，不能据此忽略 Go 子系统。[Release notes v0.27.0](https://ragflow.io/docs/dev/release_notes#v0270) · [Switch Document Engine](https://ragflow.io/docs/dev/switch_doc_engine) · [Launch Service from Source](https://ragflow.io/docs/dev/launch_ragflow_from_source)

## 13. 用户指定的官方入口

- [Quickstart / 快速入门](https://ragflow.io/docs/dev/)
- [Configuration / 配置](https://ragflow.io/docs/dev/configurations)
- [Release notes / 发布说明](https://ragflow.io/docs/dev/release_notes)
- [User Guides / 用户指南](https://ragflow.io/docs/category/user-guides)
- [Developer Guides / 开发者指南](https://ragflow.io/docs/category/developer-guides)
- [References / 参考](https://ragflow.io/docs/dev/category/references)
- [FAQs / 常见问题](https://ragflow.io/docs/dev/faq)
- [RAGFlow 官方 GitHub 源码](https://github.com/infiniflow/ragflow)
