# Windows 从零搭建指南（Docker Desktop + WSL2）

Windows 版本使用 Docker Desktop 提供 Docker Engine/Compose，使用 Ubuntu WSL2 提供 Git、Make、uv 和 Earthly 等 Linux 工具链。项目命令统一在 Ubuntu WSL2 终端执行；不要在 Windows PowerShell 和 WSL2 之间混用同一套 Earthly/Make 构建状态。

## 1. 安装 Windows 基础工具

安装并重启后确认：

- Windows 10 22H2（内部版本 19045）或 Windows 11。
- BIOS/UEFI 已开启 CPU 虚拟化。
- [Git for Windows](https://git-scm.com/download/win)（可选：仅在 PowerShell 使用 Git 时需要；WSL 内也会单独安装 Git）。
- Docker Desktop for Windows。

本项目同时运行 MySQL、Elasticsearch、NATS 和多个 RAG 容器，建议至少 8 GB 可分配内存、4 个 CPU 和 20 GB 可用磁盘；这只是单机开发建议，不是应用硬性限制。

## 2. 安装并初始化 WSL2 Ubuntu

以管理员身份打开 PowerShell，执行：

```powershell
wsl --install -d Ubuntu
wsl --update
wsl --set-default Ubuntu
```

按提示重启电脑，并在首次打开 Ubuntu 时创建 Linux 用户名和密码。微软的完整说明见 [WSL 安装文档](https://learn.microsoft.com/en-us/windows/wsl/install)。验证发行版和 WSL 版本：

```powershell
wsl --list --verbose
```

Ubuntu 的 `VERSION` 应为 `2`。若不是：

```powershell
wsl --set-version Ubuntu 2
```

## 3. 配置 Docker Desktop 的 WSL 集成

安装 Docker Desktop 后：

1. 打开 **Settings → General**，勾选 **Use the WSL 2 based engine**。
2. 打开 **Settings → Resources → WSL Integration**，启用默认 WSL 发行版，并打开 Ubuntu 的集成开关。
3. 点击 **Apply & Restart**。

在 Ubuntu 终端验证 Docker CLI 来自 Docker Desktop：

```bash
docker version
docker compose version
docker run --rm hello-world
```

如果 Ubuntu 中提示 `docker: command not found`，优先修复 Docker Desktop 的 WSL Integration；不要再在 Ubuntu 内安装第二个 Docker Engine。Docker 官方说明见 [WSL integration 文档](https://docs.docker.com/desktop/features/wsl/)。

## 4. 在 Ubuntu WSL2 安装开发工具

打开 **Ubuntu**（不是 PowerShell），安装 Git、Make 和证书：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git make
```

安装 uv 和 Python 3.12：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL" -l
uv python install 3.12
```

安装当前仓库验证过的 Earthly 0.8.16：

```bash
sudo curl -fL \
  https://github.com/earthly/earthly/releases/download/v0.8.16/earthly-linux-amd64 \
  -o /usr/local/bin/earthly
sudo chmod +x /usr/local/bin/earthly
```

验证全部工具：

```bash
git --version
make --version
uv --version
earthly --version
docker version
docker compose version
```

## 5. 获取项目并创建运行时配置

推荐把高频构建代码放在 WSL2 的 Linux 文件系统中；若需要与 Windows 编辑器共享，可先使用 `/mnt/c` 路径。下面示例对应 Windows 用户目录下的仓库：

```bash
cd /mnt/c/Users/<WindowsUser>/Documents
git clone https://github.com/DIO-12312/RAG.git
cd RAG
git config core.hooksPath .githooks
cp .env.example .env
```

编辑 `.env`，填写真实 Embedding provider：

```dotenv
EMBEDDING_MODEL_URL=https://<provider>/v1/embeddings
EMBEDDING_MODEL_NAME=<model-name>
EMBEDDING_MODEL_API_KEY=<secret>
EMBEDDING_MODEL_DIMENSION=<integer>
```

可用 VS Code 在 Windows 侧编辑，但启动和测试仍从 Ubuntu 终端执行。`.env`、密钥、真实数据和 `data/` 不得提交到 Git。

## 6. 运行离线检查

所有命令在 Ubuntu WSL2 中执行：

```bash
uv sync --frozen --group dev
make proto
make lint
make test
```

`make ci` 会聚合完整离线质量门禁：

```bash
make ci
```

## 7. 启动 Docker 服务并验收

```bash
make docker-up
docker compose ps
make docker-test SUITE=integration
make docker-test SUITE=resilience
make docker-test SUITE=eval
make docker-down
```

`make docker-up` 会通过 Earthly 构建并启动 MySQL、Elasticsearch、NATS、gRPC Server、Worker 和 Outbox。gRPC 服务从 Windows 主机访问时使用 `localhost:50051`；ES 开发检查地址为 `http://localhost:9200`，NATS 客户端地址为 `nats://localhost:4222`。

## 8. Windows 与 WSL2 的边界

| 操作 | 推荐终端 | 说明 |
| --- | --- | --- |
| 安装/更新 WSL | 管理员 PowerShell | 执行 `wsl --install`、`wsl --update` |
| Docker Desktop 设置 | Windows GUI | 启用 WSL2 engine 和 Ubuntu integration |
| `make`、`earthly`、`uv` | Ubuntu WSL2 | 使用仓库约定的 Linux 命令和路径 |
| `docker compose` | Ubuntu WSL2 | CLI 连接 Docker Desktop daemon |
| 编辑 `.env` | 任一编辑器 | 不要提交该文件或密钥 |
| gRPC/端口调试 | Windows 或 WSL2 | 服务映射到 `localhost` |

不要从 `C:\...` 路径直接在 PowerShell 调用 Linux Earthly，也不要同时让 WSL2 和 PowerShell 使用不同的工作副本；这样会导致路径转换、缓存和 `LOCALLY` 阶段行为不一致。

## 9. 常见问题

### WSL 中 `docker version` 无法连接 daemon

确认 Docker Desktop 正在运行，**Use the WSL 2 based engine** 已启用，并在 **Resources → WSL Integration** 打开 Ubuntu。修改后执行：

```powershell
wsl --shutdown
```

重新打开 Docker Desktop 和 Ubuntu，再运行 `docker version`。

### Earthly 报 `mkdir /C::` 或路径转换错误

这是从原生 Windows shell 启动 Linux Earthly 的典型现象。关闭 PowerShell 中的 Earthly 进程，进入 Ubuntu，使用 Linux 路径重新执行 `make docker-up`。

### Compose 找不到 `.env`

确认当前目录是仓库根目录并已执行 `cp .env.example .env`。PowerShell 的 `Copy-Item` 只在 Windows 侧复制文件；WSL 命令需要确认 `/mnt/c/.../RAG/.env` 实际存在。

### Elasticsearch 启动后反复不健康

为 Docker Desktop 分配更多内存，检查 9200 端口没有冲突，并查看：

```bash
docker compose logs --tail=200 elasticsearch
```

### 想清空本地卷

普通停止使用 `make docker-down`，它保留命名卷。只有明确要删除 MySQL、ES、NATS 和对象数据时才执行 `docker compose down -v`。

