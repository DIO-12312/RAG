# Linux 从零搭建指南

本指南面向一台没有预装项目依赖的 Linux 主机，默认以 Ubuntu 22.04/24.04（x86_64）为例。项目的真实运行拓扑由 Docker Compose 提供：MySQL 保存元数据，Elasticsearch 保存 BM25 与向量索引，NATS JetStream 负责任务投递，Python gRPC 服务、Worker 和 Outbox 进程负责 RAG 计算链路。

> ARM64 主机可以沿用相同流程，但 Docker 镜像和 Earthly 二进制必须确认有对应架构；如果模型供应商只提供 x86 运行时，应使用 x86_64 主机或兼容运行环境。

## 1. 安装系统工具

先安装 Git、curl、证书和 GNU Make：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git make
```

确认工具可用：

```bash
git --version
make --version
curl --version
```

## 2. 安装 Docker Engine 和 Compose

推荐使用 Docker 官方 apt 源，以便同时安装 Compose v2 插件和 Buildx。以下命令适用于 Ubuntu：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

将当前用户加入 `docker` 组，重新打开终端（或执行 `newgrp docker`）后验证：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker run --rm hello-world
docker version
docker compose version
```

如果发行版不是 Ubuntu，请按照 [Docker Engine 安装文档](https://docs.docker.com/engine/install/)选择对应发行版，不要混装多个 Docker daemon。

## 3. 安装 Python 和 uv

Docker/Earthly 的公共入口会在构建容器内使用 Python 3.12.11；本机 Python 只用于 IDE、单个测试定位或脚本调试。使用 uv 安装并管理 Python：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL" -l
uv python install 3.12
uv --version
```

进入仓库后安装锁定依赖：

```bash
cd /path/to/RAG
uv sync --frozen --group dev
```

uv 的安装方式和 Python 管理方式见 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)与 [uv Python 指南](https://docs.astral.sh/uv/guides/install-python/)。

## 4. 安装 Earthly

Earthly 是本仓库的底层构建、测试和 Docker 编排入口，Makefile 只负责转发稳定命令。当前仓库按 Earthly `0.8.16` 验证：

```bash
sudo curl -fL \
  https://github.com/earthly/earthly/releases/download/v0.8.16/earthly-linux-amd64 \
  -o /usr/local/bin/earthly
sudo chmod +x /usr/local/bin/earthly
earthly --version
```

ARM64 主机把文件名替换为 `earthly-linux-arm64`，并在 [Earthly Releases](https://github.com/earthly/earthly/releases/tag/v0.8.16)核对资产名称和校验值。Earthly 运行时必须能够访问 Docker daemon。

## 5. 获取代码与配置模型

```bash
git clone https://github.com/DIO-12312/RAG.git
cd RAG
git config core.hooksPath .githooks
cp .env.example .env
```

编辑 `.env`，至少填写真实 Embedding 服务的四个字段：

```dotenv
EMBEDDING_MODEL_URL=https://<provider>/v1/embeddings
EMBEDDING_MODEL_NAME=<model-name>
EMBEDDING_MODEL_API_KEY=<secret>
EMBEDDING_MODEL_DIMENSION=<integer>
```

`docker-compose.yml`会拒绝缺少这些变量的启动请求。`.env`只供本机 Compose 插值，已用.gitignore忽略；不要把密钥写入 `.earthly.env`、README、日志或测试 fixture。

## 6. 先做离线质量检查

```bash
make proto
make lint
make test
```

`make test`不需要真实数据库或模型，会在 Earthly 构建容器中运行确定性测试和覆盖率门禁；`make ci`是一次性执行 `lint + test` 的完整无 Secret 门禁：

```bash
make ci
```

## 7. 启动真实 Docker 拓扑

```bash
make docker-up
```

该命令会校验 Compose、生成开发期 Search Guard 材料、构建镜像、执行 MySQL migration，并等待 MySQL、受 HTTPS/Basic 保护的 Elasticsearch、NATS、gRPC Server、Worker 和 Outbox 达到健康条件。默认只有 gRPC 对宿主机开放：

| 服务 | 主机端口 | 用途 |
| --- | ---: | --- |
| gRPC | 50051 | Python RAG 服务 |

Elasticsearch、MySQL 和 NATS 仅在 Compose 私有网络内可达。不要将 9200 发布到公网，也不要在默认环境用浏览器或未认证 curl 直连 Elasticsearch。必须本机排障时，显式叠加仅绑定回环地址的 override：

```bash
docker compose -f docker-compose.yml -f docker-compose.debug.yml ps elasticsearch
```

该 override 只开放 `127.0.0.1:9200:9200`，ES 仍要求 CA 校验和 `rag_mvp` Basic 身份；它不得用于 CI 或生产。

默认部署不向浏览器、`curl` 或 Kibana 暴露 Elasticsearch；Kibana 也不在本项目的 Compose 范围内。排障 override 不是常规启动入口：不要把它写入 shell profile、CI 或生产 Compose 文件，也不要用 `curl -k`、明文 HTTP 或关闭 Search Guard 来绕过 TLS。

## 8. 生产迁移 Search Guard

这是一次**全量重启**迁移，不可把已有 `elasticsearch-data` 卷原地改造成带插件的集群。`docker-compose.yml` 与 `make docker-up` 是 development/test 材料生成拓扑：其中 `rag-security-materials` 会生成本地开发材料，绝不可直接作为 production 部署。生产必须使用独立的 **production manifest/编排**，从密钥管理系统注入外部 CA、node、admin 和 client Secret；先在隔离维护网络中演练，并由能校验证书的集群内运维客户端执行管理 API，不要为了执行迁移临时开放 9200。

1. 建立维护窗口，暂停文档上传、摄取与检索流量；使用现有受控身份创建 Elasticsearch snapshot，并在独立环境实际恢复或读取后标记为“已验证”。记录旧镜像 digest、插件版本、索引清单和 snapshot 位置。
2. 在仍受保护的运维通道禁用 shard allocation，随后停止**所有** ES 节点和依赖 RAG 服务；确认没有节点继续写入。备份 Elasticsearch data volume，备份不替代已验证 snapshot。
3. 构建/拉取精确的 `elasticsearch:8.19.19` + Search Guard FLX `4.1.2` 镜像，核对插件 SHA-256、镜像 digest 与配置中的 TLS/node DN；任一校验不符即停止。
4. 在 production manifest 中从外部密钥管理系统预置 CA、节点证书/私钥、管理员客户端证书/私钥和 `rag_mvp` password。node/admin 材料挂入 `/node-secrets`，RAG client CA 与 password 挂入 `/client-secrets`；运行时服务只读挂入 `/run/secrets/ca.pem` 与 `/run/secrets/rag_mvp_password`。以 `--environment production` 运行材料校验器或等效 fail closed 检查；生产缺少、权限过宽或证书主体不匹配时必须停止，不得自签名补齐。
5. 仅在外部材料校验成功后，由 production manifest 启动 `elasticsearch` 和 `rag-search-guard-bootstrap`。bootstrap 只在 ES `service_started` 阶段使用管理员证书重试初始化；它成功后 Elasticsearch healthcheck 才以 CA + `rag_mvp` 请求 `/_searchguard/health` 并要求 `status=UP`。这是避免首次初始化前普通应用身份尚不存在的两阶段设计。
6. bootstrap/health 通过后、恢复业务与 shard allocation 前，创建新的受保护目标数据卷/集群，执行并验证已确认 snapshot restore。必须核对预期索引、文档计数/完整性与 RAG 可检索性；恢复、完整性或检索任一失败都保持停止，不能直接验证空新集群。
7. 仅在恢复验证完整通过后，以维护模式启动 `rag-migrate`、`rag-server`、`rag-worker`、`rag-outbox`，并用 `rag_mvp` 在受控网络复核 `/_searchguard/health`、`rag-chunks-v1*` 索引限制及一次 RAG 摄取/检索闭环；同时确认它不能访问其他索引或 Search Guard 管理 API。
8. 仅在上述验证完整通过后恢复 shard allocation 和业务流量，并持续观察集群与 RAG 审计日志。

证书错误、密码错误、插件校验失败或 bootstrap 不通过时，停在当前步骤并保留脱敏诊断；严禁关闭 Search Guard、禁用 TLS 或恢复公网端口。回滚仅能在维护窗口内使用**已验证 snapshot**和旧镜像，恢复后仍保持私网端口策略。若怀疑历史上 9200 曾暴露，应轮换全部密码和证书、检查索引/集群操作日志，并从可信来源重建受影响索引。

## 9. 运行真实验收

服务启动后，使用真实 MySQL、ES、NATS 和 Embedding provider 运行：

```bash
make docker-test SUITE=integration
make docker-test SUITE=resilience
make docker-test SUITE=eval
```

只需要一次完整验收时使用 `SUITE=all`。测试失败时先保留容器查看日志；确认问题后再停止：

```bash
make docker-down
```

`make docker-down`会先扫描日志中的模型密钥，再停止容器并保留命名卷。普通 `docker compose down` 同样保留命名卷，包括 Search Guard node/client 材料；安全 bootstrap、证书或密码故障不得以删卷“修复”，应保留材料并按 fail closed 诊断。

## 10. 本机 gRPC 调试

Python 服务没有 HTTP/FastAPI adapter。本机调试必须使用 generated gRPC client、`grpcurl`、`grpcui`或仓库 CLI。服务启动后可用：

```bash
uv run python -m rag_mvp.dev.cli --help
```

开发 Compose 默认开启 Server Reflection；生产环境应关闭 `RAG_GRPC_REFLECTION`，并通过 Go 控制面暴露认证后的公网 API。

## 11. 常见问题

### `permission denied` 访问 Docker socket

确认当前用户已加入 `docker` 组，重新登录或执行 `newgrp docker`，然后运行 `docker run --rm hello-world`。不要用 `sudo make ...`，否则生成文件和缓存可能归 root 所有。

### Earthly 找不到 Docker

Earthly 本身不是第二个容器运行时；确认 `docker version`在同一终端可用，并检查 Docker daemon 是否正在运行。

### Compose 因 Embedding 变量缺失退出

检查 `.env` 中 URL、模型名、API Key 和维度均为非空值；维度必须与供应商实际返回的向量长度一致。

### 端口已被占用

默认只检查 50051 的占用情况。ES/MySQL/NATS 没有宿主机端口；如需诊断 ES，使用上文 loopback-only override，不要直接修改应用内部连接地址或公开 9200。

### 想重新开始但保留镜像

运行 `make docker-down` 后再次 `make docker-up`。如确有数据销毁或环境重建需求，应仅在隔离演练环境执行经审批的卷回收流程；不得通过删除包括 Search Guard 材料在内的命名卷来“修复”安全故障。
