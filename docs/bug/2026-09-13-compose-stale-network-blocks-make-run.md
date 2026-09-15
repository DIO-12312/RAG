# 本地产品栈遗留 Docker 网络导致 `make run` 失败

2026-09-13 本地执行 `make run` 时，RAG 核心服务能够启动，但产品栈的 `api` 和 `web` 未能正常拉起；Make 最终报告 `Makefile:69: run Error 1`。直接重试产品栈启动可稳定复现 Docker 错误：

```text
Error response from daemon: network <network-id> not found
```

根因是旧的 `rag-product-api-1` 容器仍引用已被删除的 `rag-product_default` 网络。Docker 无法把该旧容器重新接入不存在的网络，因此 `docker compose -f compose.product.yml up ...` 在启动 API 时失败。这不是生产栈占用开发栈端口导致的：生产 Caddy 使用宿主机 `80/443`，开发 Web 使用 `5173`，开发 API 仅绑定 `127.0.0.1:8080`。

## 解决方法

恢复时不要使用 `-v`，以保留产品 MySQL 的命名卷。按以下顺序操作。

1. 先确认问题属于产品栈，而不是默认 RAG 栈：

```bash
docker compose -f compose.product.yml ps -a
```

如果 `api` 为 `Exited`、`web` 为 `Created`，或启动时出现 `network <network-id> not found`，说明旧容器已不能复用。默认的 `docker compose ps` 只会显示 `docker-compose.yml` 管理的 RAG 栈，不能用它判断产品 API 或 Web 的状态。

2. 仅清理产品栈的容器和网络：

```bash
docker compose -f compose.product.yml down --remove-orphans
```

`-f compose.product.yml` 将操作范围限定在产品栈；`down` 会移除该栈的容器和默认网络，让 Docker 下次启动时新建网络。`--remove-orphans` 会同时移除不再属于当前 Compose 定义的旧容器。该命令没有 `-v`，因此不会删除 MySQL 的命名数据卷，已有产品数据会保留。不要改成 `down -v`，除非已明确决定删除本地产品数据库。

3. 重建镜像、网络和容器，并等待健康检查：

```bash
docker compose -f compose.product.yml up -d --build --wait --wait-timeout 240
```

`up` 会创建新的 `rag-product_default` 网络；`--build` 确保 API 和 Web 使用当前分支代码构建；`-d` 使服务在后台运行；`--wait` 直到 Compose 中声明的健康检查通过，最长等待 240 秒。成功时应显示 MySQL、API 和 Web 均为 `Healthy`。

4. 检查服务状态：

```bash
docker compose -f compose.product.yml ps
```

应看到 `product-mysql`、`api`、`web` 都处于 `Up` 或 `healthy` 状态。此后重新执行 `make run` 也应能通过；该目标会启动默认 RAG 栈以及产品栈。

## 访问开发环境

开发 Web 映射到宿主机 `5173`，生产 Caddy 映射到 `80/443`。因此远程浏览器应访问：

```text
http://<服务器 IP>:5173
```

开发 API 的 `8080` 只绑定在 `127.0.0.1`，这是预期的隔离设计；浏览器通过开发 Web 的反向代理访问 API，不应将 `8080` 直接暴露到公网。若远程仍无法访问 `5173`，再检查服务器防火墙或云安全组是否允许 TCP 5173。

验证结果：`rag-product-product-mysql-1`、`rag-product-api-1` 和 `rag-product-web-1` 均为 `Healthy`。从远程浏览器访问开发前端应使用 `http://<服务器 IP>:5173`；直接访问 `http://<服务器 IP>` 命中的是生产环境。

同一次排查中，`make docker-down` 还曾因 Compose 日志密钥扫描返回退出码 1 而报错。该扫描器已确认日志中包含一个已配置的 API Key 或 Elasticsearch 密码，但具体泄漏服务和日志位置尚未定位；不能将其归因于本次网络遗留问题。
