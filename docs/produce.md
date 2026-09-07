# 公网生产部署差距分析（对照 RAGFlow）

---

## 一、核心判断

本项目的强项在**计算正确性**，不在**部署就绪度**：

- 已具备：状态机（Job/Task）、事务 Outbox、NATS ACK/NAK、generation fence、幂等去重、检索 RRF 融合与稳定排序、固定 fixture 的检索质量门禁。
- 尚缺：反代/TLS、前端生产产物、对象存储抽象、认证/租户、镜像发布与分支追踪 CI/CD。

RAGFlow 在这些“外壳”上已经完备，但它的复杂度（双语言后端、十余种检索引擎/对象存储、50+ 数据源、完整 Agent 平台）不适合 MVP 阶段照搬。下面按“必须补”和“可后补”拆开。

---

## 二、公网部署硬门槛（当前缺失，必须补）

### 1. 反向代理 + HTTPS 层（完全没有）

RAGFlow 有完整 nginx 层：`docker/nginx/nginx.conf`、`proxy.conf`、`ragflow.https.conf`，包含：

- TLS 终止（`ssl_certificate` + 80→443 重定向）。
- `client_max_body_size 1024M`（大文件上传必需，当前 `RAG_MAX_UPLOAD_BYTES` 仅 16MB，但默认 nginx 1M 限制会先截断）。
- `proxy_read_timeout 3600s` + `proxy_buffering off`（SSE 长连接必需，未来 Go SSE 接口会直接踩坑）。
- `X-Forwarded-*` 头、gzip、静态资源 `expires 10y` 缓存。

当前项目**没有任何反代配置**，这是公网部署的第一硬缺口。

### 2. 前端已容器化（✅ 已完成）

`apps/web` 已有多阶段 Dockerfile（Node 22 构建 + nginx 1.27 alpine 托管）和 `nginx.conf`（SPA fallback + 8 条 API 路径反代到 Go 后端，含 SSE 长连接支持）。`compose.product.yml` 的 `web` 服务将 nginx 容器映射到 `127.0.0.1:5173`，`make run` 一键启动全栈（含前端容器）。

### 3. 对象存储仍用本地卷

RAGFlow 用 MinIO（`minio` 服务，S3 协议），且支持 S3/OSS/Azure/GCS 多后端（见 `service_conf.yaml.template`）。本项目用 `object-data:/app/data/objects` **本地 Docker 卷**——单机能跑，但：

- 无备份、无跨节点共享、Worker 不能横向扩展。
- `CODEBUDDY.md` 已写明“跨节点共享本地对象文件”尚未完成。

公网生产至少要接一个 MinIO/S3（代码里已有 `ObjectStorage` port，替换成本低）。

### 4. CI/CD 发布流水线缺失

RAGFlow 有 `.github/workflows/release.yml`（tag 触发 → 构建多架构镜像 → 推 DockerHub → 发布 SDK/CLI 到 PyPI）、CodeQL 安全扫描、PR/Issue 模板。

本项目 **`.github` 目录不存在**，但 `Earthfile` 第 27 行写了 `COPY .github ./.github`——这要么构建时空目录，要么直接失败。没有镜像发布、没有分支追踪自动部署。

### 5. 镜像发布与版本化

RAGFlow 发布到 `infiniflow/ragflow:tag`，生产用 `image:` 拉取。本项目 `docker-compose.yml` 全是 `build: context: .`（现场构建），公网部署需要推送到 GHCR/DockerHub 或现场构建。

### 6. 认证/租户/权限体系（最大风险）

RAGFlow 有完整用户/团队、OAuth（OIDC/GitHub）、权限开关、API key 管理（见 `service_conf.yaml.template` 的 `oauth`/`permission` 段）。

本项目 Go 控制面是“未来”状态，`compose.product.yml` 里的 `api` 只是骨架。**没有认证就上公网等于裸奔**，任何能访问域名的人都能调 RAG。

> * [ ]  注：`backend/go-api` 已实现 Argon2id 密码哈希、JWT、CSRF、所有权校验（见 `docs/development/live-product-plane.md`），这是好的起点，但尚未构成完整的多租户/权限闭环，也未接入公网部署链路。

### 7. 配置与 Secret 的隐患

- `.env.example` 设计正确（`_FILE` 模式读 secret），但实际 `.env` 中存在硬编码的真实 Embedding API key，需立即轮换，公网部署改用 Docker secrets 或外部 secret manager 注入。
- RAGFlow 用 `.env`（模板）+ `service_conf.yaml.template`（`${VAR}` 渲染）+ Docker secrets/挂载分离，值得对照。

---

## 三、生产加固差距（公网必看）


| 项                | 现状                          | 公网要求                                                                                    |
| ------------------- | ------------------------------- | --------------------------------------------------------------------------------------------- |
| gRPC Reflection   | `RAG_GRPC_REFLECTION=true`    | 必须`false`                                                                                 |
| gRPC 端口         | `50051` 绑 `0.0.0.0`          | 绑内网，只给 Go API 用                                                                      |
| Cookie            | `PRODUCT_COOKIE_SECURE=false` | 必须`true`，`PRODUCT_ORIGIN` 指向公网域名                                                   |
| 可观测性          | 仅 healthcheck + JSON 日志    | RAGFlow 有 OTEL + Jaeger + ClickHouse 追踪、日志轮转                                        |
| 备份/恢复         | 持久卷已声明但无备份          | RAGFlow MySQL 开 binlog、ES snapshot；本项目还需保留加密密钥卷（见`live-product-plane.md`） |
| 依赖/容器安全扫描 | 有 secret leak 扫描脚本       | RAGFlow 有 CodeQL、`.trivyignore`、SECURITY.md                                              |

---

## 四、规模化差距（RAGFlow 有，MVP 可暂缓）

- **Kubernetes/Helm**：RAGFlow 有 `helm/` chart 支持多副本 HA；本项目明确单机。
- **多检索引擎**：RAGFlow 支持 ES/OpenSearch/Infinity/SereneDB；本项目固定 ES（符合约束，无需改）。
- **Redis 缓存**：RAGFlow 有；本项目无（MVP 规模不需要）。
- **GPU/自托管 Embedding**：RAGFlow 支持 GPU profile + TEI；本项目走 OpenAI 兼容外部 API（够用）。
- **Admin 后台/团队协作**：RAGFlow 有；本项目暂无。

---

## 五、最小补齐路径

按优先级，要“真正可公网部署”最少补 5 件事：

```
① 前端生产化        ✅ apps/web Dockerfile + nginx 托管 dist（已完成）
② 反向代理 + TLS     入口 Caddy/nginx（参考 RAGFlow 的 proxy.conf 关键头）
③ 对象存储           MinIO/S3 替换本地卷
④ 认证 + Secret      至少一个登录门槛 + 轮换泄漏的 key + secret 注入
⑤ CI/CD             .github/workflows 追踪分支 + 推镜像 + 服务器自动部署
```

其中 ①②⑤ 是部署链路，③④ 是数据安全与访问控制。

⑥（K8s/多租户/GPU/Redis/可观测性）等有真实流量和规模需求后再上，不建议 MVP 阶段照搬 RAGFlow 的复杂度。

---

## 六、参考证据索引

- 本地对照：`references/ragflow/docker/nginx/`（反代 + TLS）、`references/ragflow/docker/docker-compose.yml`、`references/ragflow/docker/service_conf.yaml.template`、`references/ragflow/helm/values.yaml`、`references/ragflow/.github/workflows/release.yml`、`references/ragflow/SECURITY.md`。
- 本项目现状：`docker-compose.yml`、`compose.product.yml`、`Earthfile`、`.env.example`、`docs/development/live-product-plane.md`。
