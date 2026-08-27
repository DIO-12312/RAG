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

该命令会校验 Compose、构建镜像、执行 MySQL migration，并等待 MySQL、Elasticsearch、NATS、gRPC Server、Worker 和 Outbox 达到健康条件。默认端口如下：

| 服务 | 主机端口 | 用途 |
| --- | ---: | --- |
| gRPC | 50051 | Python RAG 服务 |
| MySQL | 3306 | 元数据与任务状态 |
| Elasticsearch | 9200 | BM25/KNN 文档引擎 |
| NATS | 4222 | JetStream 客户端连接 |
| NATS monitoring | 8222 | 开发环境健康检查 |

## 8. 运行真实验收

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

`make docker-down`会先扫描日志中的模型密钥，再停止容器并保留命名卷。只有明确要清空数据时才执行 `docker compose down -v`；该命令会删除 MySQL、ES、NATS 和对象存储卷。

## 9. 本机 gRPC 调试

Python 服务没有 HTTP/FastAPI adapter。本机调试必须使用 generated gRPC client、`grpcurl`、`grpcui`或仓库 CLI。服务启动后可用：

```bash
uv run python -m rag_mvp.dev.cli --help
```

开发 Compose 默认开启 Server Reflection；生产环境应关闭 `RAG_GRPC_REFLECTION`，并通过 Go 控制面暴露认证后的公网 API。

## 10. 常见问题

### `permission denied` 访问 Docker socket

确认当前用户已加入 `docker` 组，重新登录或执行 `newgrp docker`，然后运行 `docker run --rm hello-world`。不要用 `sudo make ...`，否则生成文件和缓存可能归 root 所有。

### Earthly 找不到 Docker

Earthly 本身不是第二个容器运行时；确认 `docker version`在同一终端可用，并检查 Docker daemon 是否正在运行。

### Compose 因 Embedding 变量缺失退出

检查 `.env` 中 URL、模型名、API Key 和维度均为非空值；维度必须与供应商实际返回的向量长度一致。

### 端口已被占用

检查 3306、4222、8222、9200、50051 的占用情况，停止冲突服务或在本地 Compose 覆盖端口映射。不要直接修改应用内部连接地址。

### 想重新开始但保留镜像

运行 `make docker-down` 后再次 `make docker-up`。只有需要丢弃持久化数据时才使用带 `-v` 的 Compose down。

