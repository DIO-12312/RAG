# 公网单机生产部署实施计划

## 目标与不变量

在 Ubuntu 单机上以 `compose.production.yml` 提供 Vue、Go 产品 API、Python RAG、MySQL、NATS 与受 Search Guard 保护的 Elasticsearch。公网只允许 Caddy 的 80/443；浏览器不能访问 gRPC、MySQL、NATS、Elasticsearch 或 Go 的内部端口。Python 继续只提供私网 gRPC，Go 是唯一产品控制面。

## 工作包

| 工作包 | 交付物 | 验收 |
| --- | --- | --- |
| P0-1 基线检查 | Ubuntu/Docker/Compose/资源/安全组/UFW 清单 | 仅 22、80、443 入站；DNS A/AAAA 指向主机；内存、磁盘满足镜像与数据容量预算。 |
| P0-2 生产拓扑 | `compose.production.yml`、`.env.production.example`、外部 Secret 布局 | 无基础设施 `ports`；私网网络；不含 `rag-security-materials`；生产材料校验 fail closed。 |
| P0-3 HTTPS 入口 | Caddyfile 与 web/api 路由 | 自动 HTTPS、HTTP→HTTPS、34 MiB 上传上限、SSE 不缓冲、SPA fallback、转发头正确。 |
| P0-4a 运维基线 | 部署、健康、备份、升级、回滚 runbook 与 ES snapshot 宿主机路径 | 静态契约覆盖裸 IP 安全停点、snapshot repository、证书失败停止及禁止删卷。 |
| P0-4b 主机演练 | 在真实主机执行启动、备份、恢复和回滚 | 可演练启动、恢复、证书失败停止及旧镜像/snapshot 回滚，并保存脱敏验收记录。 |
| P0-5 发布自动化 | 镜像构建、签名/扫描、服务器拉取与受控 rollout | 当前仓库尚未交付；不能把 Compose 模板当作 CI/CD 已完成。 |

P0-1 与 P0-4b 需要真实服务器和云账号权限，不能由仓库内静态检查替代。P0-5 是后续工作，且任何服务器自动化不得读取或提交真实 Secret。

## 2026-09-12 执行状态

- P0-1 主机侧验收已完成：Ubuntu 24.04 LTS、Docker 27.5.1、Compose 2.32.4、7.3 GiB 内存与 148 GiB 可用磁盘；UFW 仅允许 SSH、80、443。云安全组的 80/443 可达性由外部探测和用户确认覆盖，域名检查按本轮“仅公网 IP”范围暂缓。
- P0-2 已部署：生产材料检查、Search Guard bootstrap 与迁移均成功退出；MySQL、NATS、Elasticsearch、RAG、Go API、web 均在私网正常运行。旧 `rag-product` 栈已停止，宿主机不再监听 5173、8080、3307 等旧端口。
- P0-3 的仓库实现和离线门禁已完成，但按用户要求保持 `caddy=0`；宿主机当前只监听 SSH 22，真实 ACME/公网 HTTPS 验收暂缓。
- P0-4a 已完成并部署 snapshot 宿主机挂载。P0-4b 已创建 `rag_production` repository 和 `baseline-20260912-1438` 基线快照，16/16 shards 成功且无失败；异机备份和新受保护卷/集群 restore 演练仍待执行。
- 生产手工启动已收敛为 `make production-run` → Earthfile `+production-run`；它先构建并运行可信材料检查器，再以 `caddy=0` 启动和验证私网服务，移除同项目孤儿容器，只有显式请求且内部健康后才启用 Caddy。默认 `CADDY_SCALE=0` 保持裸 IP 私网预启动。

## 架构决策

选择方案 A：`Caddy → web(Nginx) → api`。现有前端产物和 Nginx 已共同维护 SPA fallback、上传限制及根路径 API 代理，保留它可避免让 Caddy 和前端各自维护两组路由规则。Caddy 只负责公网 TLS、HTTP→HTTPS、公共安全头与连接级超时；它也保留 `/api/*` 的直达 Go 路由（去除 `/api` 前缀），供外部集成使用。Vue 现有同源根路径请求仍经 web 转发，避免破坏已发布前端。

## 实施顺序

1. 在 Ubuntu 创建专用非 root 运维账号，安装受支持的 Docker Engine 与 Compose plugin；记录 `docker version`、`docker compose version`、`df -h`、`free -h`。确认没有其他进程绑定 80/443。
2. 在云安全组和 UFW 只允许 TCP 22、80、443。SSH 应限制可信源或使用密钥/MFA；Docker 不应把未声明端口自动公开。
3. 本期在主机从已审查的源码构建本地 production 镜像；填充主机私有 `.env.production` 的本地镜像名、域名、ACME 邮箱和绝对 Secret 目录。后续 registry/不可变 digest 发布是独立工作包，不影响本期启动链路。
4. 由 Secret Manager/受控运维过程创建 `deploy/production/secrets/README.md` 中列出的文件。生产不会生成任何 Search Guard 证书或密码；先单独运行 material check，失败即停止。
5. 用 `make production-run` 经 Earthfile 校验生产 env 和 Compose 配置、从当前源码构建无状态材料检查器并以禁止拉取的本地镜像校验外部材料，再以 `caddy=0` 构建、移除同项目孤儿容器并启动私网服务；不得将 `config` 渲染结果重定向到文件或日志，因为其中会包含敏感路径/配置。裸 IP 阶段保留默认 `CADDY_SCALE=0`。
6. 域名与 ACME 条件就绪后，在维护窗口执行 `make production-run CADDY_SCALE=1`，先验证 bootstrap、数据库、RAG 服务和 Go `/readyz`，最后验证 HTTPS。仅从浏览器访问域名；不映射或探测私网端口。

## 运行边界与未决事项

本期继续使用单机对象卷，故备份必须同时覆盖两套 MySQL、Elasticsearch snapshot/data、NATS、object-data、Caddy `/data` 与产品加密/JWT key。没有测试过的 key 轮换或恢复不能在生产直接执行。模型 API Key 由授权用户在设置页加密保存，不写入部署 env 或 Secret 模板。
