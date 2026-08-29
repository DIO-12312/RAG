# Bug 记录：`rag-search-guard-bootstrap` 生成失败（Search Guard SG11 初始化墙）

- **日期**：2026-08-29
- **影响组件**：`rag-search-guard-bootstrap`（one-shot sgctl bootstrap）、`elasticsearch`（Search Guard FLX 4.1.2 / ES 8.19.19）
- **触发方式**：`make docker-up`
- **状态**：已修复并在 WSL/Docker Desktop 的真实 Compose 环境验收

## 现象

`make docker-up` 时，`rag-search-guard-bootstrap` 容器以状态 1 退出：

```
Container rag-mvp-rag-search-guard-bootstrap-1
Error service "rag-search-guard-bootstrap" didn't complete successfully: exit 1
```

由于 compose 中 `rag-server`/`rag-worker`/`rag-outbox` 依赖 bootstrap `service_completed_successfully`，失败会连带阻断整个 RAG 链路启动（fail closed 预期行为）。

## 影响

- Search Guard 无法完成首次初始化，`.searchguard` 配置索引永远不会被创建。
- ES 拒绝所有请求（含 `_cluster/health`），返回 `Search Guard not initialized (SG11)`。
- 所有下游服务无法启动；只能在空集群/受保护数据卷上恢复。

## 排查过程

从 `docker/search-guard/entrypoints/bootstrap.sh` 追入 `scripts/search_guard/bootstrap.py`，流程为：

1. `_connect` → `sgctl connect --skip-connection-check`
2. `_verify_existing` → `sgctl get-config`（未初始化时失败，返回 False）
3. `_initialize` → `sgctl add-user-local` 生成 `sg_internal_users.yml` → `sgctl update-config`
4. `_verify_runtime_user` → 以 `rag_mvp` Basic 身份请求 `/_searchguard/health`

### 现场证据

`sgctl update-config` 返回：

```
HTTP/1.1 503 Service Unavailable
Search Guard not initialized (SG11). See https://docs.search-guard.com/latest/sgctl
```

ES 日志：

```
ERROR com.floragunn.searchguard.authc.rest.AuthenticatingRestFilter
  Not yet initialized (you may need to run sgctl)
```

即使 `connect` 携带管理员证书也失败：`Server is unavailable: Service Unavailable`。

### 已排除的假设

| 假设 | 结论 |
|---|---|
| 数据卷陈旧导致半初始化 | 排除：全新数据卷可稳定复现 |
| sgconfig 文件缺失/路径错 | 排除：镜像内路径与 compose 挂载均正确 |
| sgctl 与插件版本不匹配 | 排除：同为 4.1.2 |

## 根因

首次问题由 DN 与配置类型缺陷触发；修复后，真实启动继续暴露三个独立缺陷：初始化时序、开发 TLS 材料和 Python 客户端的最小权限。

### 缺陷 1（主因）：`elasticsearch.yml` 的 `admin_dn`/`nodes_dn` 顺序错误

Search Guard 的 `DefaultPrincipalExtractor` 会把证书 DN 的 RDN 顺序**反转**后作为 principal：

```
C=CN,L=Local,O=RAG,OU=RAG,CN=sg_admin
```

而 `AdminDNs.isAdminDN()` 使用 `javax.naming.ldap.LdapName.equals()` 做**顺序敏感**比较。

`docker/search-guard/elasticsearch.yml` 中配置为正序：

```yaml
searchguard.nodes_dn:
  - CN=elasticsearch,OU=RAG,O=RAG,L=Local,C=CN
searchguard.authcz.admin_dn:
  - CN=sg_admin,OU=RAG,O=RAG,L=Local,C=CN
```

正序与反转序在 `LdapName` 相等性判断上**永不相等**，导致：

- 管理员证书不被识别为 admin；
- `AuthenticatingRestFilter` 把所有 sgctl 请求挡在初始化之外，抛 SG11。

> 证书本身（DN 内容）是对的，仅 `elasticsearch.yml` 里的书写顺序与 Search Guard 生成 principal 的顺序不一致。

### 缺陷 2（次因）：sgconfig 缺少必需配置类型，卡在 `waiting for 5`

Search Guard 首次初始化需要 **5 个必需配置文档**：

- `internalusers`（sg_internal_users.yml）
- `actiongroups`（sg_action_groups.yml）
- `roles`（sg_roles.yml）
- `rolesmapping`（sg_roles_mapping.yml）
- `tenants`（sg_tenants.yml）

仓库 `docker/search-guard/sgconfig/` 只有 3 个文件（sg_authc.yml、sg_roles.yml、sg_roles_mapping.yml），`bootstrap.py` 再经 `add-user-local` 生成 1 个 `sg_internal_users.yml`，合计 **4 个文档**，缺少 `sg_action_groups.yml` 与 `sg_tenants.yml`。

ES 日志反复出现：

```
Got 4 documents, waiting for 5 in total, we just try again ...
```

配置刷新永不完成，`/_searchguard/health` 一直为 `DOWN`，最终 `_verify_runtime_user` 抛错导致容器 exit 1。

### 缺陷 3：`service_started` 不等于 `update-config` 已可用

Compose 只保证 Elasticsearch 进程已创建；`_connect(... --skip-connection-check)` 也不验证 HTTPS/SG 配置入口已经能接受请求。真实时间线中 bootstrap 在 ES 启动后约 0.2 秒执行，并在约 5 秒后因首个 `update-config` 失败退出。

修复：`_initialize` 只生成一次内部用户文件，并在 120 秒窗口内每 2 秒重试 `update-config`；超时仍 fail closed。

### 缺陷 4：开发 TLS 证书缺少 SKI/AKI，且旧卷被错误复用

Python 运行时访问 `/_searchguard/health` 时校验证书链，实际错误为 `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier`。材料生成器没有为 CA 写入 SKI、也没有为叶子证书写入 AKI；旧逻辑又仅凭文件存在就复用命名卷中的材料。

修复：CA 写入 SKI，节点/admin 叶子证书写入 SKI 与指向 CA SKI 的 AKI；开发/test 复用前会解析证书、验证 DN、issuer、CA 内容及 AKI 链。不合格旧卷在下次启动时重新生成材料。

### 缺陷 5：RAG 运行时角色缺少 `indices:admin/get`

Python Elasticsearch 客户端的 `indices.exists()` 在 Search Guard 中被授权为 `indices:admin/get`，并非仅 `indices:admin/exists`。ES 权限日志明确记录该动作对 `rag_mvp_search` 为 `MISSING`，导致 server/worker 的 `ensure_index()` 收到 403。

修复：只为 `rag-chunks-v1*` 增加 `indices:admin/get`，不授予 `SGS_ALL_ACCESS`。对于已存在的命名卷，已使用管理员证书显式上传更新后的 `sg_roles.yml`；这是配置迁移，不删除任何数据卷。

## 验证证据（决定性实验）

全部在临时容器与临时卷上完成，未触碰真实数据卷。

| 配置 | 结果 |
|---|---|
| 正序 admin_dn + 原证书 | `connect`/`update-config` 均 SG11 拒绝 |
| 反转 admin_dn + 带 SKI/AKI 证书 | `connect` 成功：`Successfully connected ... as user C=CN,L=Local,O=RAG,OU=RAG,CN=sg_admin`；`update-config` 创建 `.searchguard` 索引，但卡在 `waiting for 5 in total` |
| 反转 admin_dn + nodes_dn + 补齐 5 个配置文档 | `update-config` 返回 `Configuration has been updated`；ES 日志 `Search Guard configuration has been successfully initialized`；`/_searchguard/health` = `{"status":"UP"}`；`rag_mvp` Basic 身份可检索集群（green） |

## 已应用修复

1. `docker/search-guard/elasticsearch.yml` — 将 `admin_dn`、`nodes_dn` 改为反转序：
   ```yaml
   searchguard.nodes_dn:
     - C=CN,L=Local,O=RAG,OU=RAG,CN=elasticsearch
   searchguard.authcz.admin_dn:
     - C=CN,L=Local,O=RAG,OU=RAG,CN=sg_admin
   ```
2. 新增 `docker/search-guard/sgconfig/sg_action_groups.yml`、`docker/search-guard/sgconfig/sg_tenants.yml`。
3. `scripts/search_guard/bootstrap.py` — 将上述两个文件纳入初始化上传，并重试暂时不可用的首次上传。
4. `scripts/search_guard/materials.py` — 生成并校验 SKI/AKI 证书链，拒绝复用畸形开发材料。
5. `docker/search-guard/sgconfig/sg_roles.yml` — 补充 `indices:admin/get` 最小权限。

## 预防与回归

- 在 `tests/contract/test_search_guard_assets.py` 增加对 `elasticsearch.yml` 中 `admin_dn`/`nodes_dn` 顺序的契约断言（反转序）。
- 增加对 sgconfig 必需配置类型的断言（至少包含 internalusers/actiongroups/roles/rolesmapping/tenants 对应的文件）。
- 首次初始化验证命令：`sgctl connect`（不带 `--skip-connection-check`）+ `sgctl update-config` 后检查 `/_searchguard/health` 为 `UP`。
- 对启动时序、证书 key identifiers、旧材料校验和 `indices.exists()` 所需权限增加契约回归。

## 最终验收（2026-08-29）

执行 WSL 中的 `make docker-up` 后：

- `rag-security-materials`、`rag-search-guard-bootstrap` 与 `rag-migrate` 均 `Exited (0)`；
- `elasticsearch`、`rag-server`、`rag-worker`、`rag-outbox` 均为 `healthy`；
- gRPC 服务已监听宿主机 `0.0.0.0:50051`；
- `tests/contract/test_search_guard_assets.py` 共 11 项通过。

## 参考资料

- Search Guard sgctl 文档（connect + update-config 流程）：https://docs.search-guard.com/latest/sgctl
- Search Guard 安装文档（admin_dn 与首次 sgctl 初始化）：https://docs.search-guard.com/latest/search-guard-installation
- Search Guard 论坛：AuthenticatingRestFilter `Not yet initialized` 与 admin_dn/clientauth 修复：https://forum.search-guard.com/t/c-f-s-a-r-authenticatingrestfilter-node1-not-yet-initialized-you-may-need-to-run-sgctl/2433/4
- 相关源码：
  - `DefaultPrincipalExtractor`（反转 RDN 顺序）：`ssl/src/main/java/com/floragunn/searchguard/ssl/transport/DefaultPrincipalExtractor.java`
  - `AdminDNs`（LdapName 顺序敏感比较）：`security/src/main/java/com/floragunn/searchguard/configuration/AdminDNs.java`
  - `AuthenticatingRestFilter`（未初始化时仅放行 admin DN）：`security/src/main/java/com/floragunn/searchguard/authc/rest/AuthenticatingRestFilter.java`
  - `ConfigurationRepository`（等待必需文档数）：`security/src/main/java/com/floragunn/searchguard/configuration/ConfigurationRepository.java`
