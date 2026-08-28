# Search Guard Elasticsearch 安全加固设计

**日期：** 2026-08-28  
**状态：** 已确认，待实施  
**范围：** 将本项目的 Elasticsearch 从无认证、宿主机全网卡暴露，迁移为 Search Guard FLX 管理的 HTTPS + HTTP Basic 服务。

## 背景与目标

当前 Compose 使用 Elasticsearch `8.19.3`、显式关闭安全功能，并将 `9200` 发布到所有宿主机网卡。任意可达该端口的客户端可以读取、修改或删除索引。目标是保留 Python RAG 服务对 Elasticsearch 的内部依赖，同时以网络隔离、TLS 和最小权限认证消除未鉴权暴露。

本设计采用 Elasticsearch `8.19.19` 与 Search Guard FLX `4.1.2`。二者必须精确匹配；不允许继续使用已 EOL 的 `8.19.3 + 3.1.2` 组合。

不在本次范围内：Kibana、LDAP/AD、OIDC、Search Guard Enterprise REST 管理 GUI、细粒度租户 DLS/FLS，以及公网 Elasticsearch API。未来引入 Kibana 时必须单独设计，且不得发布 `5601` 到公网。

## 方案与边界

部署单个自定义 Elasticsearch 镜像：基于 `docker.elastic.co/elasticsearch/elasticsearch:8.19.19`，安装精确版本的 Search Guard 插件。插件工件 URL 为：

```text
https://maven.search-guard.com/search-guard-flx-release/com/floragunn/search-guard-flx-elasticsearch-plugin/4.1.2-es-8.19.19/search-guard-flx-elasticsearch-plugin-4.1.2-es-8.19.19.zip
```

构建时必须校验该工件 SHA-256：

```text
6fa46190b1fd62f6c54d6c11d17757f043110f8c0db016e16c62c59b953f3c91
```

`elasticsearch` 不再声明 `9200:9200`。RAG Server、Worker、Outbox、迁移容器和测试容器仅通过 Compose 私有网络的 `https://elasticsearch:9200` 访问它。若本地排障确有需要，单独的开发 Compose override 才可发布 `127.0.0.1:9200:9200`；该 override 不得进入生产部署或 CI。

```text
rag-server / rag-worker / rag-test
          │ HTTPS + Basic (rag_mvp)
          ▼
Search Guard FLX on Elasticsearch 8.19.19
          │ TLS
          ▼
rag-chunks-v1* only
```

## TLS、初始化与秘密

Search Guard 首次安装要求全量重启并要求 transport TLS；本设计同时启用 HTTP TLS。证书分为 CA、节点证书和仅用于 `sgctl` 初始化的管理员客户端证书。生产中节点与管理员证书必须不同；私钥、管理员证书、用户密码、CA 私钥及其派生文件均不得进入 Git、镜像层、日志或测试 artifact。

提供受版本控制的非秘密配置模板和显式 bootstrap 命令。bootstrap 只接受通过环境变量或 Docker/Kubernetes Secret 注入的材料，在受限目录生成或挂载证书，并以管理员客户端证书运行 `sgctl` 完成初始化。它必须可重复执行：已初始化的配置只做一致性校验，不覆盖运行中用户或角色。丢失或错误的秘密必须使启动失败，不能回退到 HTTP、匿名访问或 demo 用户。

Search Guard 初始化配置使用 `basic/internal_users_db`。内部用户数据库只保存 BCrypt 密码哈希；明文密码只以一次性 Secret 形式提供给 bootstrap 和 RAG 进程。Search Guard 自身配置、角色及用户更新优先经 `sgctl` 完成，不启用 Enterprise REST 管理 API。

## 身份与授权

定义两个身份：

| 身份 | 用途 | 权限 |
|---|---|---|
| `sg_admin` | 仅 bootstrap/灾难恢复，用管理员客户端证书调用 `sgctl` | Search Guard 配置管理；不注入 RAG 运行时容器 |
| `rag_mvp` | RAG Server、Worker、Outbox 和真实测试的 HTTP Basic 身份 | 仅 `rag-chunks-v1*` 的读、写、bulk、mapping、创建/删除索引、delete-by-query 与所需 cluster health/复合操作 |

`rag_mvp` 不得拥有 `SGS_ALL_ACCESS`，不得访问 Search Guard 系统索引、任意其他业务索引、用户/角色管理 API 或节点管理 API。Search Guard 的角色映射必须通过 `rag_mvp` 的 backend role 完成，避免将用户名直接散布在权限定义中。

## 应用与运维契约

新增以下运行时配置，全部允许从环境变量或密钥挂载读取：

```text
RAG_ELASTICSEARCH_URL=https://elasticsearch:9200
RAG_ELASTICSEARCH_USERNAME=rag_mvp
RAG_ELASTICSEARCH_PASSWORD=<secret>
RAG_ELASTICSEARCH_CA_CERT=/run/secrets/search_guard_ca.pem
```

`Settings` 将密码建模为 secret 类型；container 仅将其传递给 Elasticsearch async client 的 Basic Auth 和 CA 校验配置。日志、异常、trace、pytest 快照、Earthly 输出和 Compose config 不得包含 password 或 `Authorization` 值。客户端不得以 `verify_certs=false`、`curl -k` 或明文 HTTP 绕过 TLS。

Elasticsearch healthcheck 改为使用 CA、`rag_mvp` Basic 身份和 `/_searchguard/health`，要求 `status=UP`。启动顺序为：Search Guard Elasticsearch 健康 → 安全 bootstrap 成功 → `rag-migrate` → RAG Server/Worker/Outbox。任何一步失败均阻止下游服务启动。

已有 `elasticsearch-data` 卷不可原地升级为带插件的集群；迁移 runbook 必须要求维护窗口、备份/快照、停止所有节点、安装插件、挂载证书、初始化、验证，再恢复 shard allocation。测试环境使用独立卷，不得以删除生产卷作为迁移手段。

## 测试与验收

离线门禁继续不需要任何 Secret。Docker integration、resilience 和 eval 必须使用受保护的 HTTPS Elasticsearch；测试 bootstrap 通过专用 test CA 和 test-only Secret 建立身份，且这些文件被 `.gitignore` 排除。

必须覆盖：

1. 未带凭据的 HTTPS 请求返回认证拒绝，明文 HTTP 连接失败；宿主非 loopback 地址没有 9200 listener。
2. 使用错误密码、错误 CA 或缺失凭据时，RAG 进程/测试容器失败且日志不泄露秘密。
3. `rag_mvp` 能完成 `ensure_index`、bulk upsert、KNN/BM25、delete-by-query 和 dataset cleanup，但不能读取非 `rag-chunks-v1*` 索引或调用 Search Guard 管理 API。
4. `/_searchguard/health` 为 `UP` 后，完整 integration、resilience、real eval 均可通过；真实 eval 的 Dataset 删除仍能清理 MySQL、ES 和对象存储。
5. 构建入口测试固定 ES/插件精确版本、插件 checksum、禁止 `xpack.security.enabled: false`、禁止默认 `9200:9200` 和 TLS bypass。

## 失败处理与回滚

插件、证书、配置初始化或认证失败都应 fail closed：容器不健康、依赖服务不启动、真实 suite 失败。不得为了恢复可用性临时关闭 Search Guard 或恢复公网端口。

回滚仅限于在维护窗口中，从已验证的快照和原镜像恢复；恢复后的 ES 仍必须保持私网访问限制。若怀疑历史暴露，视为数据泄露与篡改事件：保留证据、轮换全部 Search Guard 密码和证书、检查索引/集群操作日志，并重新建立可信索引。

## 参考

- [Search Guard 版本矩阵](https://docs.search-guard.com/latest/search-guard-versions)
- [Search Guard 安装与 TLS 初始化](https://docs.search-guard.com/latest/search-guard-installation)
- [Search Guard HTTP Basic 与内部用户库](https://docs.search-guard.com/latest/http-basic-authorization)
- [Search Guard 角色权限](https://docs.search-guard.com/latest/roles-permissions)
