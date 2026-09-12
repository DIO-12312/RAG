# Ubuntu 公网部署与回滚手册

这份手册对应单机 `compose.production.yml`，不替代高可用、Kubernetes 或托管数据库方案。只有公网 IP 时可以完成主机预检、镜像构建、材料校验和不启动 Caddy 的私网预启动，但不能完成浏览器登录或公网 HTTPS 验收。不得将公网 IP 填入 `RAG_PUBLIC_DOMAIN` 冒充域名；启用 Caddy 前必须确认域名的 A/AAAA 记录已经指向服务器。

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

安全组和 UFW 只保留 TCP 22、80、443；先保证现有 SSH 会话可用，再修改防火墙。禁止为排障开放 50051、3306、3307、4222、9200、5173 或 8080。Docker Compose 生产文件不会发布这些端口，仍应以 `ss -ltnp` 在启动后复核。

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status numbered
```

云安全组也必须做相同限制；UFW 不是云防火墙的替代品。

## Secret 与配置

将仓库的 `.env.production.example` 复制为 `/etc/rag-mvp/.env.production`，填写域名、ACME 邮箱、本地镜像名、`/etc/rag-mvp/secrets` 和 `/var/backups/rag-mvp`。裸 IP 预启动阶段仍填写一个明确的待启用域名占位值，但必须保持 Caddy 副本数为 0。配置文件和目录均不可提交。本期 Compose 在主机从当前源码构建 production 镜像；后续可独立改为 registry 的不可变 digest。按照 [Secret 布局](../deploy/production/secrets/README.md) 从 Secret Manager 写入所需文件；生产材料验证器会拒绝缺失、权限过宽或主体不匹配的 Search Guard 材料，绝不会自签名补齐。这里的 Compose `secrets:` 是只读 bind mount，不是 Swarm 的加密 Secret，文件来源的 `uid/gid/mode` 字段会被 Compose 忽略。宿主机必须按 Secret 布局中的 UID/权限矩阵落盘，否则非 root 的 Elasticsearch、Python 或 Go 进程无法读取；宿主机 root 与 Docker 管理组仍可读取全部 Secret。

ES snapshot 目录必须在启动前创建，所有备份随后还要复制到另一台主机或对象存储；同机目录不能抵御整机损坏：

```bash
sudo install -d -o 1000 -g 1000 -m 0700 /var/backups/rag-mvp/elasticsearch
```

`product/encryption.key` 与 `product/jwt.key` 必须在恢复时与产品 MySQL 一起恢复。遗失 encryption key 会使已保存的模型配置无法解密；不要以删除 key 或数据库来“修复”。

## 裸 IP 阶段：私网预启动

先解析配置并单独执行生产材料检查；该命令失败时不得继续：

```bash
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml config --quiet
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml run --rm --no-deps production-material-check
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml up -d --scale caddy=0
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml ps
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml exec -T api wget -q -O - http://127.0.0.1:8080/readyz
```

此时所有业务服务仍只在 Docker 网络内。公网 IP 上没有可用产品页面是预期行为；不得临时发布 8080、50051 或其他内部端口绕过 HTTPS。

## 域名就绪后：公网启动与健康检查

确认 A/AAAA 解析正确并填写真实域名和 ACME 邮箱后，在仓库根目录执行：

```bash
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml config --quiet
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml up -d --scale caddy=1
docker compose --env-file /etc/rag-mvp/.env.production -f compose.production.yml ps
curl --fail --show-error --location "https://YOUR_DOMAIN/healthz"
```

先检查 `production-material-check` 和 `rag-search-guard-bootstrap` 为成功退出，再要求所有长期服务 healthy/running。Caddy 自动将 HTTP 重定向到 HTTPS，并传递 `X-Forwarded-*`；Caddy 的 `/api/*` 会移除前缀后转发 Go API，现有 Vue 根路径 API 继续由 web 容器转发。验证一次注册、上传、Job 成功和有 evidence 的问答，确认 SSE 在代理后能完成。

若材料校验、bootstrap 或健康检查失败，保持服务停止并检查不含 Secret 的容器状态/日志。不得通过关闭 TLS、Search Guard 或新增私网端口映射来继续。

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
