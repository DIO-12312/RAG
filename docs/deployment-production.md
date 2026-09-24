# Ubuntu 公网部署与回滚手册

main 分支 GHCR 发布与应用镜像回退见 [自动发布手册](deployment-release.md)。
手工 `production-run` 为现场构建维护入口；自动发布独立使用 `production-deploy`。

这份手册对应单机 `compose.production.yml`，不替代高可用、Kubernetes 或托管数据库方案。只有公网 IP 时可以启动 Caddy 的 HTTP 入口并通过 `http://49.235.110.118` 验收页面，但不能完成浏览器认可的公网 HTTPS 验收。域名模式必须使用真实域名的 A/AAAA 记录，不得将公网 IP 填入域名 HTTPS 配置。

## 观测服务与数据保留

管理员观测权限由产品 MySQL 的 `users.role` 控制，注册用户默认 `user`。首次部署时先正常注册，记下该账号 `GET /api/me` 的 `id`，再由运维在私网 API 容器执行离线授权：

```bash
docker compose --env-file "$PRODUCTION_ENV_FILE" -f compose.production.yml exec -T api /usr/local/bin/product-admin-role --user-id USER_ID --role admin
```

撤权使用相同命令并将 `--role` 改为 `user`。命令只接受已存在的用户 ID，拒绝撤销最后一名管理员，成功时输出不含邮箱与凭据的 JSON 操作审计；应将该输出纳入受控运维记录。命令从容器内只读 Secret 文件读取产品 MySQL DSN，不需要在命令行暴露密码。旧 Cookie 的角色不会被信任，撤权后下一次管理员请求即被拒绝。

同一生产 Compose 在 `backend` 私网运行 OpenTelemetry Collector、Prometheus 和 Tempo；它们没有宿主端口，Caddy 也不代理原生查询接口。Go API 与三个 Python 进程只通过私网 `otel-collector:4318` 发送 OTLP。Collector 将脱敏后的指标供 Prometheus 抓取，将 Trace 发往 Tempo；保留原 JSON 日志和数据库业务状态。Collector、Prometheus 或 Tempo 不可用时，业务服务仍应正常运行，仪表盘显示后端不可用。

Prometheus 使用独立命名卷 `prometheus-data`，最长保留 15 天，并以 `OBS_PROMETHEUS_RETENTION_SIZE` 设置数据块容量上限（默认 `2GB`，实际部署须按磁盘预算调整）。Tempo 的 `tempo-data` 命名卷最长保留 7 天；`OBS_TEMPO_BUDGET_BYTES` 默认 3 GiB。独立的 `observability-retention` 进程每分钟计算 Tempo 卷占用和宿主剩余空间：80% 水位开始逐级缩短 Tempo 的运行时保留期，70% 以下逐级恢复，90% 或宿主剩余不足 10% 时拒绝新 Trace，并向 Prometheus 暴露容量/暂停状态。该进程只改写专用 `tempo-overrides` 卷中的配置，不直接删除数据文件。Tempo 的租户级覆盖会替换整组默认值，因此控制进程在写入保留期时同时固定摄取速率、突发大小、活跃 Trace 数和单条 Trace 大小限制；升级 Tempo 时须重新核对这些字段。Tempo 自身异步淘汰最旧块，因而预算不是严格硬配额；须为 WAL、压缩和回收延迟留余量。控制进程不可用时 Trace 可丢失，但业务不中断。生产长期运行宜将 Tempo 换为受支持的对象存储后端；当前本地卷部署需先完成真实负载容量验收。

备份和升级时将观测卷视为可重建的短期运营数据，按实际审计需求决定是否做一致性备份；不得运行 `docker compose down -v`。更新 Collector/Prometheus/Tempo 镜像前，检查固定版本的来源和 digest，在隔离环境验证配置兼容和旧卷读取，然后逐项升级。采集属性不得包含凭据、Prompt、Evidence、问题正文或模型输入输出。

## 主机预检

以有 sudo 权限的运维账号检查 Ubuntu、Docker、Compose、磁盘与内存：

```bash
uname -a
docker version
docker compose version
df -h
free -h
sudo ss -ltnp
```

安全组和 UFW 只保留 TCP 22、80、443；先保证现有 SSH 会话可用，再修改防火墙。禁止为排障开放 50051、3306、3307、4222、9200、5173 或 8080。Docker Compose 生产文件不会发布这些端口，仍应以 `ss -ltnp` 在启动后复核。生产 `edge` 网桥固定使用 `172.19.0.0/16`，供公网回程策略路由使用；若该网段与主机其他网络冲突，必须先调整 Compose、systemd 回程规则和防火墙策略后再启动。

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status numbered
```

云安全组也必须做相同限制；UFW 不是云防火墙的替代品。

### Mihomo TUN 与公网回程

生产 `edge` 固定为 `172.19.0.0/16`。启用 TUN 的主机仅应让该网段中源端口为 TCP 80/443 的回复优先查主路由：

```bash
ip rule add pref 8988 from 172.19.0.0/16 ipproto tcp sport 80 lookup main
ip rule add pref 8989 from 172.19.0.0/16 ipproto tcp sport 443 lookup main
```

当前主机由 `/etc/systemd/system/rag-public-return-route.service` 持久化这两条规则。不得使用整个 `edge` 网段无条件直连的规则：Go API 也连接此网络，模型域名若解析为 Mihomo Fake-IP，主动模型请求必须继续经 TUN。上述范围仅覆盖当前 TCP 公网入口，不覆盖未来 UDP/HTTP3。

首次从自动分配改为固定 IPAM 时，Compose 可能重建网络，使已有 API/Web 容器引用旧网络 ID。确认报错后可在维护窗口重建这些无状态容器，再运行 `make production-run`；不得删除数据库或持久卷。

## Secret 与配置

将仓库的 `.env.production.example` 复制为 `/etc/rag-mvp/.env.production`，填写本地镜像名、`/etc/rag-mvp/secrets` 和 `/var/backups/rag-mvp`。IP 模式默认使用 `http://49.235.110.118`，不填写域名变量，明文 HTTP 仅适合临时演示或受控网络，不能承载正式生产登录流量；域名模式才填写 `RAG_PUBLIC_ORIGIN=https://...`、`RAG_PUBLIC_SITE_ADDRESS=...`、ACME 邮箱，并设置 `PRODUCT_COOKIE_SECURE=true`，入口会强制检查该值。配置文件和目录均不可提交。本期 Compose 在主机从当前源码构建 production 镜像；后续可独立改为 registry 的不可变 digest。按照 [Secret 布局](../deploy/production/secrets/README.md) 从 Secret Manager 写入所需文件；生产材料验证器会拒绝缺失、权限过宽或主体不匹配的 Search Guard 材料，绝不会自签名补齐。这里的 Compose `secrets:` 是只读 bind mount，不是 Swarm 的加密 Secret，文件来源的 `uid/gid/mode` 字段会被 Compose 忽略。宿主机必须按 Secret 布局中的 UID/权限矩阵落盘，否则非 root 的 Elasticsearch、Python 或 Go 进程无法读取；宿主机 root 与 Docker 管理组仍可读取全部 Secret。

ES snapshot 目录必须在启动前创建，所有备份随后还要复制到另一台主机或对象存储；同机目录不能抵御整机损坏：

```bash
sudo install -d -o 1000 -g 1000 -m 0700 /var/backups/rag-mvp/elasticsearch
```

`product/encryption.key` 与 `product/jwt.key` 必须在恢复时与产品 MySQL 一起恢复。遗失 encryption key 会使已保存的模型配置无法解密；不要以删除 key 或数据库来“修复”。

## 裸 IP 阶段：公网 HTTP 启动

在仓库根目录执行生产入口；它会经 Earthfile 依次校验参数和生产配置、从当前源码构建无状态材料检查器并校验外部材料，随后构建镜像、移除同项目孤儿容器、启动私网服务并检查 API/web 健康状态。任一检查失败都会停止，持久卷不会被删除：

```bash
make production-run
```

默认 `PUBLIC_MODE=ip`，页面地址为 `http://49.235.110.118`。IP 模式不申请 ACME 证书，浏览器应使用 HTTP 访问；不得临时发布 8080、50051 或其他内部端口。若生产 env 不在默认路径，可显式传入 `PRODUCTION_ENV_FILE=/absolute/path`。

## 域名就绪后：公网启动与健康检查

确认 A/AAAA 解析正确、填写真实域名 Origin 和 ACME 邮箱，并设置 `PRODUCT_COOKIE_SECURE=true` 后，在仓库根目录执行：

```bash
make production-run PUBLIC_MODE=domain
curl --fail --show-error --location "https://YOUR_DOMAIN/healthz"
```

先检查 `production-material-check` 和 `rag-search-guard-bootstrap` 为成功退出，再要求所有长期服务 healthy/running。Caddy 自动将 HTTP 重定向到 HTTPS，并传递 `X-Forwarded-*`；Caddy 的 `/api/*` 会移除前缀后转发 Go API，现有 Vue 根路径 API 继续由 web 容器转发。验证一次注册、上传、Job 成功和有 evidence 的问答，确认 SSE 在代理后能完成。

若材料校验失败，任何有状态服务都不得启动；若后续 bootstrap 或内部健康检查失败，必须保持 Caddy 停止，私网服务可保留用于检查不含 Secret 的容器状态/日志。不得通过关闭 TLS、Search Guard 或新增私网端口映射来继续。

## 备份、恢复、升级与回滚

`compose.production.yml` 将宿主机 `${PRODUCTION_BACKUP_DIR}/elasticsearch` 挂到 ES 的 `/mnt/snapshots`，并设置 `path.repo`。使用受控的 Search Guard 管理员客户端在私网注册文件系统仓库；不要授予运行时 `rag_mvp` snapshot 管理权限。注册请求的固定 payload 与 API 路径如下，实际调用必须使用管理员证书并在私网发起：

```http
PUT /_snapshot/rag_production
Content-Type: application/json

{"type": "fs", "settings": {"location": "/mnt/snapshots", "compress": true}}
```

每次备份创建唯一 snapshot 后，必须检查 `GET /_snapshot/rag_production/SNAPSHOT_NAME` 返回成功且没有 failed shard，再将宿主机 snapshot 目录同步到异机或对象存储。两套 MySQL 采用一致性 dump；暂停 API、worker 和 outbox 写入后，再备份 NATS、object-data、Caddy `/data` 与产品 key。备份产物必须加密、校验 checksum，并记录对应镜像 digest。

恢复演练必须在新的受保护数据卷或隔离主机执行：先恢复同批次的产品 key 和两套 MySQL，再启动 ES 与 Search Guard bootstrap、注册 `rag_production` repository、执行 snapshot restore；核对索引清单、文档数与一次有 evidence 的 RAG 查询后，才启动 worker、outbox、API 和 Caddy。任一步失败都保持公网入口停止，不得在原生产卷上反复试错。

升级前：创建并验证 ES snapshot，记录当前镜像 digest，暂停写入、禁用 shard allocation、停止服务并备份数据卷。用新镜像和外部材料启动一个新的受保护 ES 目标卷或集群，完成 Search Guard bootstrap 后 restore 已确认 snapshot；核对预期索引、文档计数和一次 RAG 检索，才恢复 allocation 和全部业务服务。

### 主机重启后恢复

生产 Compose 的 `edge`/`egress`/`backend` 三个网络都必须固定子网（172.19/172.20/172.21）。
只给 `edge` 固定时，先创建的 `egress`/`backend` 会被自动分配到 `172.19.0.0/16`，随后 `edge`
因网段重叠创建失败，`boot-start.sh`（容器全停后的唯一恢复入口）会停在「4/5 产品 API 与前端」。
实测处置：删除残留网络后预建 `rag-production_edge --subnet 172.19.0.0/16`（带
`com.docker.compose.project=rag-production`、`com.docker.compose.network=edge` 标签）再恢复；
根治办法是三个网络都固定子网（已由 contract 测试固定）。

非计划重启（OOM、内核升级、云厂商维护）后容器不会自动回到运行态，且 `make production-run` 会重新构建镜像、`make production-deploy` 需要 GitHub 触发。恢复入口是 `deploy/production/boot-start.sh`：它读取 `/var/lib/rag-deploy/active.json` 指向的已渲染 Compose 配置，按「基础设施 → 一次性初始化 → 摄取检索 → API/前端 → Caddy」顺序拉起服务，不构建、不迁移、不删除任何卷，也不修改发布记录。

本仓库提供 `deploy/production/rag-production.service`（`Type=oneshot`、`RemainAfterExit=yes`）在开机时调用同一脚本：

```bash
install -m 0644 deploy/production/rag-production.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now rag-production.service
systemctl status rag-production.service   # ExecMainStatus=0 表示恢复成功
```

注意两点：恢复使用的是**发布记录里的镜像 digest**，因此如果线上此前是手工构建的镜像（未走 `release.py publish`），恢复会把服务带回记录版本；出现这种情况应重新执行一次正式发布，而不是继续手工重建。另外前端侧栏的版本徽标由 `VITE_GIT_COMMIT` 构建参数决定，缺失时会显示 `unknown`；手工 `make production-run` 已在 Earthfile 中注入当前提交，正式发布由 `release.py publish` 注入。

回滚仅使用上一个不可变镜像与已验证 snapshot/数据库备份，并继续保持 Caddy 为唯一公网入口。禁止执行 `docker compose down -v`；它会破坏可恢复数据。若怀疑任何私网服务曾暴露公网，立即关闭入口、轮换数据库/ES/产品密钥与证书、审查日志，并从可信备份重建。
