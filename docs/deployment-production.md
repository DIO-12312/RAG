# Ubuntu 公网部署与回滚手册

这份手册对应单机 `compose.production.yml`，不替代高可用、Kubernetes 或托管数据库方案。只有公网 IP 时可以启动 Caddy 的 HTTP 入口并通过 `http://49.235.110.118` 验收页面，但不能完成浏览器认可的公网 HTTPS 验收。域名模式必须使用真实域名的 A/AAAA 记录，不得将公网 IP 填入域名 HTTPS 配置。

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

回滚仅使用上一个不可变镜像与已验证 snapshot/数据库备份，并继续保持 Caddy 为唯一公网入口。禁止执行 `docker compose down -v`；它会破坏可恢复数据。若怀疑任何私网服务曾暴露公网，立即关闭入口、轮换数据库/ES/产品密钥与证书、审查日志，并从可信备份重建。
