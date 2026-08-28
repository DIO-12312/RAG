# Search Guard Elasticsearch 安全加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将默认 Compose 检索集群升级为 Elasticsearch 8.19.19 + Search Guard FLX 4.1.2，并让所有 RAG 访问经私网 HTTPS、Basic 身份和最小权限完成。

**Architecture:** 使用一个校验插件校验和的 Elasticsearch 自定义镜像、一个仅在首次启动创建本地开发材料的 one-shot 服务，以及一个持有管理员客户端证书的 one-shot `sgctl` bootstrap 服务。ES 仅在 Compose 私有网络暴露；bootstrap 成功后，Server、Worker 和测试容器通过 CA 校验和 `rag_mvp` Basic 身份访问 ES。应用配置将认证信息封装为 secret profile，Search Engine adapter 保持不感知认证机制。

**Tech Stack:** Docker Compose、Elasticsearch 8.19.19、Search Guard FLX/sgctl 4.1.2、Python 3.12、Pydantic Settings、elasticsearch-py AsyncElasticsearch、pytest、Earthly。

**Spec:** `docs/superpowers/specs/2026-08-28-search-guard-elasticsearch-security-design.md`；同时遵守 `docs/SPEC.md` 第 3.4 节。

## Global Constraints

- Elasticsearch 固定为 `docker.elastic.co/elasticsearch/elasticsearch:8.19.19`；Search Guard FLX 固定为 `4.1.2`，插件 URL 与 SHA-256 `6fa46190b1fd62f6c54d6c11d17757f043110f8c0db016e16c62c59b953f3c91` 必须写入并被构建校验。
- 默认、CI 和生产 Compose 不得发布 `9200`；本地排障 override 只能使用 `127.0.0.1:9200:9200`，不得使用 `0.0.0.0`。MySQL、NATS client/monitoring 端口也不默认发布。
- 必须启用 Search Guard transport TLS 与 HTTP TLS；禁止 demo 证书/用户、`xpack.security.enabled: false`、明文 HTTP、`curl -k` 与 `verify_certs=false`。
- `sg_admin` 只存在于 bootstrap/恢复路径，不能注入 RAG 运行时；`rag_mvp` 只能访问 `rag-chunks-v1*`，不得具有 `SGS_ALL_ACCESS`、系统索引、节点或 Search Guard 管理权限。
- 密码、私钥、管理员证书、CA 私钥、`Authorization` 值、真实 PDF、数据、缓存和日志均不得提交、进入镜像层、Compose config 输出或测试 artifact；离线 `make ci` 不要求任何 Secret。
- 初始化/认证/TLS 任一步失败均 fail closed；不得以关闭 Search Guard、打开 9200 或回退 HTTP 恢复服务。不得用 `docker compose down -v` 清理或迁移数据卷。
- 所有新建或更名测试、marker、fixture 必须在同一工作包同步更新 `tests/TEST.md`；修改 Earthfile/Makefile 必须运行 `tests/contract/test_build_entrypoints.py`。

---

## File Structure

| 路径 | 职责 |
| --- | --- |
| `Dockerfile.elasticsearch` | 下载、校验并离线安装精确 Search Guard 插件的 ES 运行镜像。 |
| `Dockerfile.search-guard-bootstrap` | 下载、校验 sgctl 4.1.2，并提供一次性安全材料/配置初始化运行环境。 |
| `docker/search-guard/elasticsearch.yml` | 不含秘密的 Search Guard transport/HTTP TLS 与管理员 DN 配置。 |
| `docker/search-guard/sgconfig/*.yml` | 不含密码哈希的认证域、最小角色和 backend-role 映射模板。 |
| `scripts/search_guard/materials.py` | 只为 development/test 生成 CA、节点/管理员证书及随机 `rag_mvp` 密码，写入命名卷；production 缺失外部材料时失败。 |
| `scripts/search_guard/bootstrap.py` | 等待 TLS ES、用 sgctl 初始化或只读核验 Search Guard 配置，并验证 `rag_mvp`。 |
| `docker/search-guard/entrypoints/*.sh` | 将 Secret 文件路径传给 Python/sgctl，禁止 `set -x` 与秘密回显。 |
| `docker-compose.yml` | 安全材料、ES、bootstrap 与应用的依赖图、私网卷和 Secret 文件挂载。 |
| `docker-compose.debug.yml` | 仅供本机排障的 127.0.0.1 ES 端口 override。 |
| `src/rag_mvp/config.py` | `ElasticsearchProfile`、HTTPS/身份/Secret-file 配置校验。 |
| `src/rag_mvp/bootstrap/container.py` | 用 profile 构造经 Basic Auth 与 CA 验证的 `AsyncElasticsearch`。 |
| `tests/contract/test_search_guard_assets.py` | 静态安全资产、版本、checksum、端口及 TLS-bypass 不变量。 |
| `tests/integration/test_search_guard_security.py` | 真实 HTTPS 认证、拒绝与最小权限证据。 |
| `tests/integration/test_elasticsearch_adapter.py`、`tests/resilience/docker/conftest.py` | 复用受保护的 ES client，继续覆盖 adapter/恢复行为。 |
| `Earthfile`、`Makefile`、`scripts/check_secret_leaks.py` | 以不泄密的公共入口准备安全材料、构建/启动、传递测试连接参数并扫描两类 Secret。 |
| `.env.example`、`.gitignore`、`docs/setup-linux.md`、`docs/setup-windows.md`、`docs/testing-guide.md` | 本地初始化、生产材料、升级/回滚和安全测试说明。 |

### Task 1: 固定 Search Guard 镜像、非秘密配置与本地材料生成

**Files:**
- Create: `Dockerfile.elasticsearch`
- Create: `Dockerfile.search-guard-bootstrap`
- Create: `docker/search-guard/elasticsearch.yml`
- Create: `docker/search-guard/sgconfig/sg_authc.yml`
- Create: `docker/search-guard/sgconfig/sg_roles.yml`
- Create: `docker/search-guard/sgconfig/sg_roles_mapping.yml`
- Create: `docker/search-guard/entrypoints/prepare-materials.sh`
- Create: `scripts/search_guard/materials.py`
- Create: `tests/contract/test_search_guard_assets.py`
- Modify: `.gitignore`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces `python scripts/search_guard/materials.py --environment <development|test|production> --node-output <dir> --client-output <dir>`: development/test 创建 PEM 材料与 `rag_mvp_password`（0600）；production 在任何所需文件缺失时返回非零且不生成自签名材料。
- Produces node files `ca.pem`, `node.pem`, `node-key.pem`, `admin.pem`, `admin-key.pem`, `rag_mvp_password`，以及 client files `ca.pem`, `rag_mvp_password`；节点密码副本仅供 ES healthcheck 读取，客户端密码仅供 RAG runtime 读取。
- Produces Search Guard backend role `rag_mvp_runtime`，角色 `rag_mvp_search`，索引模式 `rag-chunks-v1*`。

- [ ] **Step 1: 写入失败的静态安全资产测试**

```python
def test_search_guard_assets_pin_versions_tls_and_least_privilege() -> None:
    dockerfile = _text("Dockerfile.elasticsearch")
    config = _text("docker/search-guard/elasticsearch.yml")
    roles = _text("docker/search-guard/sgconfig/sg_roles.yml")

    assert "elasticsearch:8.19.19" in dockerfile
    assert "search-guard-flx-elasticsearch-plugin-4.1.2-es-8.19.19.zip" in dockerfile
    assert "6fa46190b1fd62f6c54d6c11d17757f043110f8c0db016e16c62c59b953f3c91" in dockerfile
    assert "searchguard.ssl.transport.pemcert_filepath" in config
    assert "searchguard.ssl.http.enabled: true" in config
    assert "SGS_ALL_ACCESS" not in roles
    assert '"rag-chunks-v1*"' in roles
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/contract/test_search_guard_assets.py -q`

Expected: FAIL，因为安全镜像与配置文件尚不存在。

- [ ] **Step 3: 实现可复现镜像、配置与材料生成器**

`Dockerfile.elasticsearch` 必须先把插件下载到临时文件，执行以下精确校验后以 `file:///` 安装，并在同一层删除 zip：

```dockerfile
ARG SEARCH_GUARD_PLUGIN_SHA256=6fa46190b1fd62f6c54d6c11d17757f043110f8c0db016e16c62c59b953f3c91
RUN curl --fail --location --silent --show-error "$SEARCH_GUARD_PLUGIN_URL" -o /tmp/search-guard.zip \
 && echo "$SEARCH_GUARD_PLUGIN_SHA256  /tmp/search-guard.zip" | sha256sum --check --strict \
 && bin/elasticsearch-plugin install --batch file:///tmp/search-guard.zip \
&& rm /tmp/search-guard.zip
```

`Dockerfile.search-guard-bootstrap` 基于带 JRE 的最小镜像下载 `https://maven.search-guard.com/search-guard-flx-release/com/floragunn/sgctl/4.1.2/sgctl-4.1.2-shaded.jar`，并校验 SHA-256 `02d46e8166a87a9b524993d3842c59b13a7c7dc52d799e36c33f15e457947d29`；其入口只能以 `java -jar /opt/sgctl/sgctl-4.1.2-shaded.jar` 调用，不安装或启用 Search Guard REST 管理 API。

`elasticsearch.yml` 使用相对 `search-guard/...` 路径，分别配置 transport 与 HTTP 的 cert/key/trusted CA，设置 `searchguard.nodes_dn` 为生成器节点 DN `CN=elasticsearch,OU=RAG,O=RAG,L=Local,C=CN`，并将 `searchguard.authcz.admin_dn` 固定为独立管理员证书的 `CN=sg_admin,OU=RAG,O=RAG,L=Local,C=CN`。在 Elastic 发行版上必须设置 `xpack.security.enabled: false`，避免 X-Pack Security 与 Search Guard 重复注册 transport 安全层；TLS、HTTP Basic 与 RBAC 由 Search Guard 继续强制执行。

`sg_authc.yml` 的完整认证域为：

```yaml
auth_domains:
  - type: basic/internal_users_db
```

`sg_roles.yml` 定义 `rag_mvp_search`：cluster actions 仅 `SGS_CLUSTER_COMPOSITE_OPS` 和 `cluster:monitor/health`；index actions 仅对 `rag-chunks-v1*` 授予 `indices:admin/exists`、`indices:admin/create`、`indices:admin/delete`、`indices:admin/mapping/put`、`indices:admin/mappings/get`、`indices:data/read/search`、`indices:data/read/msearch`、`indices:data/write/bulk`、`indices:data/write/index`、`indices:data/write/delete`、`indices:data/write/delete/byquery` 与 `indices:admin/refresh`。`sg_roles_mapping.yml` 仅把 backend role `rag_mvp_runtime` 映射到该角色。

`materials.py` 用 `cryptography.x509` 生成 RSA-3072 CA、包含 `DNS:elasticsearch`/`DNS:localhost` 的节点证书以及独立 admin client certificate；私钥和密码以 `chmod(0o600)` 写入。它只能在 `development`、`test` 生成；`production` 必须验证预置材料存在、权限不宽于 0600 且 SAN/DN 符合约定后返回成功。把生成目录 `data/search-guard/` 和 `tests/.search-guard/` 加入 `.gitignore`。

- [ ] **Step 4: 运行静态资产与材料生成测试**

Run: `uv run pytest tests/contract/test_search_guard_assets.py -q`

Expected: PASS；补充测试以 `tmp_path` 调用生成器，断言 test 生成的节点与 client 目录没有相同私钥、密码不出现在 stdout/stderr、production 缺文件失败。

- [ ] **Step 5: 更新测试目录说明并提交**

在 `tests/TEST.md` 的 contract 目录树与职责表登记 `test_search_guard_assets.py` 和其静态安全边界。

```bash
git add Dockerfile.elasticsearch Dockerfile.search-guard-bootstrap docker/search-guard scripts/search_guard \
  .gitignore tests/contract/test_search_guard_assets.py tests/TEST.md
git commit -m "build(search): 固定 Search Guard 安全镜像与材料"
```

### Task 2: 实现幂等 sgctl bootstrap 与私网 Compose 拓扑

**Files:**
- Create: `scripts/search_guard/bootstrap.py`
- Create: `docker/search-guard/entrypoints/bootstrap.sh`
- Create: `docker-compose.debug.yml`
- Modify: `docker-compose.yml`
- Modify: `tests/contract/test_container_artifacts.py`
- Modify: `tests/contract/test_search_guard_assets.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Consumes Task 1 的 node/client 目录、`sg_authc.yml`、`sg_roles.yml`、`sg_roles_mapping.yml`。
- Produces Compose service `rag-security-materials`（一次性）、`elasticsearch`、`rag-search-guard-bootstrap`（一次性）以及应用服务可依赖的 `service_completed_successfully` bootstrap 状态。
- Produces `scripts/search_guard/bootstrap.py --host elasticsearch --port 9200 --node-dir /node-secrets --client-dir /client-secrets --config-dir /config`：首次创建 `rag_mvp`，随后仅比较认证域/角色/映射与用户 backend role；不覆盖已存在密码或角色以外的运行中配置。

- [ ] **Step 1: 写入失败的 Compose 安全边界测试**

```python
def test_compose_keeps_infrastructure_private_and_orders_security_bootstrap() -> None:
    compose = _text("docker-compose.yml")
    es = _service_block(compose, "elasticsearch")
    bootstrap = _service_block(compose, "rag-search-guard-bootstrap")

    assert '"9200:9200"' not in compose
    assert '"3306:3306"' not in compose
    assert '"4222:4222"' not in compose
    assert "rag-security-materials:" in es
    assert "condition: service_completed_successfully" in es
    assert "condition: service_started" in bootstrap
    assert "RAG_ELASTICSEARCH_PASSWORD_FILE" not in _service_block(compose, "rag-outbox")


def test_debug_override_binds_es_to_loopback_only() -> None:
    assert '"127.0.0.1:9200:9200"' in _text("docker-compose.debug.yml")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/contract/test_container_artifacts.py tests/contract/test_search_guard_assets.py -q`

Expected: FAIL，因为现有 Compose 仍发布基础设施端口且没有 bootstrap 服务。

- [ ] **Step 3: 实现 bootstrap 和 Compose 依赖图**

`rag-security-materials` 挂载两个私有命名卷：`search-guard-node-secrets`（ES 节点/admin 材料）和 `search-guard-client-secrets`（CA、`rag_mvp_password`）。它在 development/test 调用 Task 1 生成器，已有同一套有效材料时不覆盖；production 只验证外部注入内容，缺失即退出非零。

`elasticsearch` 使用 `Dockerfile.elasticsearch`，只挂载 node volume 到 `/usr/share/elasticsearch/config/search-guard:ro` 与原有 data volume；不声明 `ports`。healthcheck 读取 client volume 中的 CA/密码并使用 `curl --cacert ... --user "rag_mvp:$(cat ...)" https://127.0.0.1:9200/_searchguard/health`，只在 JSON 的 `status` 为 `UP` 时成功，且命令禁止 `-k`。

bootstrap 不能等待 ES healthy：首次初始化前 `rag_mvp` 不存在。它只依赖 `elasticsearch: service_started`，先以 admin client certificate 加 CA 重试连接 `sgctl`；若尚未初始化，复制非秘密配置到临时目录、以管道把 client password 交给 `sgctl add-user-local rag_mvp --backend-roles rag_mvp_runtime --password`，再 `sgctl update-config`。若已初始化，执行 `sgctl get-config`，比较 `sg_authc.yml`、`sg_roles.yml`、`sg_roles_mapping.yml`，并仅验证 `rag_mvp` 的 backend role 存在；差异、认证失败和未知集群状态均退出非零。它最后用 `rag_mvp` + CA 请求 `/_searchguard/health`，使 ES healthcheck 转绿。

`rag-migrate`、`rag-server`、`rag-worker` 和 `rag-outbox` 都依赖 `rag-search-guard-bootstrap: service_completed_successfully`；只有 server、worker、rag-test 获得 client volume 和 `RAG_ELASTICSEARCH_PASSWORD_FILE=/run/secrets/rag_mvp_password`。所有访问 ES 的服务设置 `RAG_ELASTICSEARCH_URL=https://elasticsearch:9200`、username `rag_mvp`、CA 路径 `/run/secrets/search_guard_ca.pem`。`rag-outbox` 与 migration 不挂 ES 密码/CA。debug override 只增加 loopback 9200 端口，且不改变认证或 TLS。

- [ ] **Step 4: 运行 Compose 静态检查**

Run: `docker compose config --quiet && docker compose -f docker-compose.yml -f docker-compose.debug.yml config --quiet && uv run pytest tests/contract/test_container_artifacts.py tests/contract/test_search_guard_assets.py -q`

Expected: 两个 Compose 配置均可解析；默认拓扑没有主机 9200/3306/4222/8222 映射，所有新增静态测试 PASS。

- [ ] **Step 5: 更新测试目录说明并提交**

在 `tests/TEST.md` 更新 container artifact 测试职责，明确 bootstrap 顺序、Secret 最小暴露与私网端口不变量。

```bash
git add docker-compose.yml docker-compose.debug.yml scripts/search_guard/bootstrap.py \
  docker/search-guard/entrypoints/bootstrap.sh tests/contract/test_container_artifacts.py \
  tests/contract/test_search_guard_assets.py tests/TEST.md
git commit -m "feat(search): 编排 Search Guard 私网初始化"
```

### Task 3: 将 RAG 客户端改为 HTTPS、Basic 与 CA 校验

**Files:**
- Modify: `src/rag_mvp/config.py`
- Modify: `src/rag_mvp/bootstrap/container.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_container_roles.py`
- Modify: `tests/fakes/container.py`
- Modify: `.env.example`
- Modify: `docs/SPEC.md`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces immutable `ElasticsearchProfile(endpoint: str, username: str, password: SecretStr, ca_cert: Path)`.
- Produces `Settings.require_elasticsearch_profile() -> ElasticsearchProfile`; it accepts exactly one of `RAG_ELASTICSEARCH_PASSWORD` and `RAG_ELASTICSEARCH_PASSWORD_FILE` and rejects non-HTTPS endpoint, blank username/password, absent CA path value, or both secret sources.
- `container._search_resource(settings)` consumes that profile and creates `AsyncElasticsearch(endpoint, basic_auth=(username, password), ca_certs=str(ca_cert), verify_certs=True)`.

- [ ] **Step 1: 写入失败的 Settings 与 client 组装测试**

```python
def test_elasticsearch_profile_reads_file_secret_without_repr_leak(tmp_path: Path) -> None:
    password_file = tmp_path / "rag_mvp_password"
    password_file.write_text("test-es-password\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        elasticsearch_url="https://elasticsearch:9200",
        elasticsearch_username="rag_mvp",
        elasticsearch_password_file=password_file,
        elasticsearch_ca_cert=tmp_path / "ca.pem",
    )

    profile = settings.require_elasticsearch_profile()
    assert profile.password.get_secret_value() == "test-es-password"
    assert "test-es-password" not in repr(settings)
    assert "test-es-password" not in repr(profile)


@pytest.mark.parametrize("url", ["http://elasticsearch:9200", "https://"])
def test_elasticsearch_profile_rejects_insecure_or_invalid_endpoint(url: str) -> None:
    with pytest.raises(ValueError, match="Elasticsearch"):
        Settings(_env_file=None, elasticsearch_url=url).require_elasticsearch_profile()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_container_roles.py -q`

Expected: FAIL，因为 profile、secret-file 字段和认证 client 尚不存在。

- [ ] **Step 3: 实现 profile、fail-closed 校验与 client 构造**

在 `Settings` 默认值中将 URL 改为 `https://elasticsearch:9200`，增加 `elasticsearch_username: str = "rag_mvp"`、`elasticsearch_password: SecretStr | None`、`elasticsearch_password_file: Path | None`、`elasticsearch_ca_cert: Path | None`。`require_elasticsearch_profile()` 在运行时读取 password file、去掉末尾换行，不把路径内容写入错误；文件不存在或空值一律以不包含秘密的 `ValueError` 失败。不得在 import 阶段读取文件。

将 `_search_resource` 只替换为如下受保护构造，保持 `ElasticsearchSearchEngine` 的公开接口不变：

```python
profile = settings.require_elasticsearch_profile()
client = AsyncElasticsearch(
    profile.endpoint,
    basic_auth=(profile.username, profile.password.get_secret_value()),
    ca_certs=str(profile.ca_cert),
    verify_certs=True,
)
```

所有 unit fake settings 使用 `tmp_path` 写入的 test password file 和一个路径值为 CA，避免把密码硬编码进测试对象或断言输出。`.env.example` 写入 URL、username、`RAG_ELASTICSEARCH_PASSWORD_FILE=data/search-guard/client/rag_mvp_password`、`RAG_ELASTICSEARCH_CA_CERT=data/search-guard/client/ca.pem`，并注释明文 password 仅适合外部 Secret 注入，不应写在文件中。同步 SPEC 中该 file-secret 兼容方式，不放宽 HTTPS/最小权限约束。

- [ ] **Step 4: 运行 unit 与类型检查**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_container_roles.py -q && uv run mypy src/rag_mvp/config.py src/rag_mvp/bootstrap/container.py`

Expected: PASS；测试覆盖 HTTPS 拒绝、双 secret source 拒绝、空/缺文件拒绝及密码不出现在 repr。

- [ ] **Step 5: 更新测试说明并提交**

在 `tests/TEST.md` 登记新增 Settings 测试职责与 fake container secret-file 前置条件。

```bash
git add src/rag_mvp/config.py src/rag_mvp/bootstrap/container.py tests/unit/test_config.py \
  tests/unit/test_container_roles.py tests/fakes/container.py .env.example docs/SPEC.md tests/TEST.md
git commit -m "feat(search): 强制 RAG Elasticsearch TLS 认证"
```

### Task 4: 让真实 adapter、恢复与安全测试使用受保护客户端

**Files:**
- Create: `tests/integration/test_search_guard_security.py`
- Modify: `tests/integration/test_elasticsearch_adapter.py`
- Modify: `tests/resilience/docker/conftest.py`
- Modify: `tests/resilience/docker/docker-compose.resilience.yml`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces `tests.integration.test_elasticsearch_adapter._client_from_environment(prefix: str) -> AsyncElasticsearch`：读取 `<prefix>_ELASTICSEARCH_URL`、`_USERNAME`、`_PASSWORD_FILE`、`_CA_CERT`，以 Basic + CA + `verify_certs=True` 创建 client。
- Docker test 环境向 integration/resilience 提供 `RAG_TEST_ELASTICSEARCH_URL=https://elasticsearch:9200`、`RAG_TEST_ELASTICSEARCH_USERNAME=rag_mvp`、`RAG_TEST_ELASTICSEARCH_PASSWORD_FILE=/run/secrets/rag_mvp_password`、`RAG_TEST_ELASTICSEARCH_CA_CERT=/run/secrets/search_guard_ca.pem`。

- [ ] **Step 1: 写入失败的真实 Search Guard 安全测试**

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_guard_rejects_anonymous_http_and_bad_credentials() -> None:
    secure = _client_from_environment("RAG_TEST")
    anonymous = AsyncElasticsearch(os.environ["RAG_TEST_ELASTICSEARCH_URL"], ca_certs=os.environ["RAG_TEST_ELASTICSEARCH_CA_CERT"])
    bad = AsyncElasticsearch(
        os.environ["RAG_TEST_ELASTICSEARCH_URL"],
        basic_auth=("rag_mvp", "wrong-password"),
        ca_certs=os.environ["RAG_TEST_ELASTICSEARCH_CA_CERT"],
    )
    with pytest.raises(ApiError) as anonymous_error:
        await anonymous.info()
    assert anonymous_error.value.status_code == 401
    with pytest.raises(ApiError) as bad_error:
        await bad.info()
    assert bad_error.value.status_code == 401
    with pytest.raises(ConnectionError):
        await AsyncElasticsearch("http://elasticsearch:9200").info()
    await secure.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rag_identity_cannot_manage_search_guard_or_other_indices() -> None:
    client = _client_from_environment("RAG_TEST")
    with pytest.raises(ApiError) as other_index:
        await client.indices.create(index="forbidden-index")
    assert other_index.value.status_code == 403
    with pytest.raises(ApiError) as management:
        await client.perform_request("GET", "/_searchguard/api/roles")
    assert management.value.status_code == 403
```

- [ ] **Step 2: 运行安全测试并确认失败**

Run: `uv run pytest tests/integration/test_search_guard_security.py -m integration -q`

Expected: 由于尚未启动受保护 Docker 服务而失败；记录为需要 Docker 的 integration 测试，不能把它加入离线 suite。

- [ ] **Step 3: 改造真实 fixture 并补齐能力/拒绝覆盖**

把 ES adapter fixture、resilience `es_client` 和需要计数/refresh 的测试全部改为上述 TLS helper；不得保留 `http://127.0.0.1:9200` 默认值。安全测试必须再断言 `rag_mvp` 能 `ensure_index`、bulk upsert、KNN、BM25、document/dataset delete-by-query、refresh/count；并以另一临时 CA 建立 client，断言 TLS 验证失败且异常文本不包含 password。所有 client 在 `finally` 中关闭。

resilience override 只传递 `RAG_TEST_*` 的非秘密路径/用户名，复用 base Compose client Secret volume；不添加 docker socket 之外的权限，不暴露 ES 端口，也不改现有持久卷保护。

- [ ] **Step 4: 启动隔离 Docker 拓扑并运行真实测试**

Run: `make docker-test SUITE=integration`

Expected: adapter、E2E 与 `test_search_guard_security.py` 通过；若模型配置缺失，则在真实 Docker 前置校验失败而不是回退 Fake。随后运行 `make docker-test SUITE=resilience`，确认 worker/relay 重启后仍以 CA/Basic 访问 ES。

- [ ] **Step 5: 更新测试说明并提交**

在 `tests/TEST.md` 目录树和 Integration/Docker Resilience 责任表中登记安全测试、客户端环境变量和 Docker-only 边界。

```bash
git add tests/integration/test_search_guard_security.py tests/integration/test_elasticsearch_adapter.py \
  tests/resilience/docker/conftest.py tests/resilience/docker/docker-compose.resilience.yml tests/TEST.md
git commit -m "test(search): 覆盖 Search Guard 认证与最小权限"
```

### Task 5: 更新 Earthly 真实入口、秘密扫描与构建契约

**Files:**
- Modify: `Earthfile`
- Modify: `Makefile`
- Modify: `Dockerfile`
- Modify: `scripts/check_secret_leaks.py`
- Modify: `tests/contract/test_build_entrypoints.py`
- Modify: `tests/contract/test_container_artifacts.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- `make docker-up` 与 `make docker-test SUITE=...` 继续是唯一公共真实入口；`DOCKER_START` 必须构建 `rag-security-materials`、自定义 ES 和 bootstrap，并等待 bootstrap 后的 ES health。
- `check_secret_leaks.py` 同时扫描 embedding API key 与 `Settings.require_elasticsearch_profile().password`，命中时只输出 `secret leak detected`。

- [ ] **Step 1: 写入失败的入口与扫描器测试**

```python
def test_docker_entrypoint_builds_security_services_without_rendering_secrets() -> None:
    earthfile = _text("Earthfile")
    assert "Dockerfile.elasticsearch" in earthfile
    assert "rag-security-materials" in earthfile
    assert "rag-search-guard-bootstrap" in earthfile
    assert "RAG_TEST_ELASTICSEARCH_PASSWORD_FILE" in earthfile
    assert "docker compose config --quiet" in earthfile
    assert "config >" not in earthfile


def test_secret_scanner_detects_elasticsearch_password_without_echoing_it(tmp_path: Path) -> None:
    password_file = tmp_path / "rag_mvp_password"
    password_file.write_text("es-secret-sentinel\n", encoding="utf-8")
    completed = _run_secret_scanner(password_file, "Authorization: Basic es-secret-sentinel")
    assert completed.returncode == 1
    assert completed.stdout.strip() == "secret leak detected"
    assert "es-secret-sentinel" not in completed.stdout + completed.stderr
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q`

Expected: FAIL，因为 Docker 构建清单、测试连接参数和 ES Secret 扫描尚未更新。

- [ ] **Step 3: 实现安全真实入口和扫描器**

在 `Dockerfile` runtime/test 目标复制 `scripts/search_guard`，但绝不 COPY `data/search-guard`、`.env` 或生成证书；保持非 root `rag` 用户。`Earthfile` 的 `DOCKER_START` 先执行 `docker compose config --quiet`，再 build `rag-security-materials elasticsearch rag-search-guard-bootstrap rag-migrate rag-server rag-worker rag-outbox rag-test`，最后 `up -d --wait` 应用服务。不要新增绕过 Make 的公开 shell 入口。

在 `docker-test` integration 函数传递四个 `RAG_TEST_ELASTICSEARCH_*` 值，其中 password 是 `/run/secrets/rag_mvp_password` 路径；resilience 同理依赖 base Compose mount。`docker-down` 的 log scanner 运行在包含 client Secret volume 的 rag-test 容器中，并保证即使扫描失败仍执行 `docker compose down --remove-orphans`，绝不加入 `-v`。

扫描器逐个收集已配置的非空 secret 值（embedding 和 ES profile），使用内存精确匹配，任何一个命中均只打印固定文本。若没有可扫描的 Secret，保留现有非零配置错误，不打印配置内容。

- [ ] **Step 4: 运行构建契约和离线门禁**

Run: `uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q && make ci`

Expected: PASS；离线门禁不要求生成证书或模型/API Secret，契约确认没有公开 9200、明文 URL、TLS bypass 或 Compose Secret 展开。

- [ ] **Step 5: 更新测试说明并提交**

在 `tests/TEST.md` 更新 build-entrypoint 与 secret scanner 责任；Makefile 如新增说明仅添加注释和 help 文案，不新增直接执行 Docker/Python 的 recipe。

```bash
git add Earthfile Makefile Dockerfile scripts/check_secret_leaks.py \
  tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py tests/TEST.md
git commit -m "build(search): 保护真实测试的 Elasticsearch 密钥"
```

### Task 6: 完成运维 runbook、安装文档与全量验收

**Files:**
- Modify: `docs/SPEC.md`
- Modify: `docs/superpowers/specs/2026-08-28-search-guard-elasticsearch-security-design.md`
- Modify: `docs/setup-linux.md`
- Modify: `docs/setup-windows.md`
- Modify: `docs/testing-guide.md`
- Modify: `.env.example`
- Modify: `tests/TEST.md`

**Interfaces:**
- 文档输出一条开发启动路径：复制 `.env.example`、填写模型配置、执行 `make docker-up`；安全材料服务在 development/test 自动初始化但不会回显任何秘密。
- 文档输出一条生产迁移路径：备份/快照、维护窗口、停止节点、预置外部证书/密码、全量重启、bootstrap、健康/权限验证、恢复 shard allocation；回滚仅使用验证快照与旧镜像，且不恢复公网端口。

- [ ] **Step 1: 写入失败的文档安全断言**

```python
def test_setup_documents_private_es_and_recovery_runbook() -> None:
    linux = _text("docs/setup-linux.md")
    windows = _text("docs/setup-windows.md")
    guide = _text("docs/testing-guide.md")
    for text in (linux, windows):
        assert "localhost:9200" not in text
        assert "Search Guard" in text
        assert "127.0.0.1:9200:9200" in text
    assert "make docker-test SUITE=all" in guide
    assert "Search Guard" in guide and "TLS" in guide
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/contract/test_search_guard_assets.py -q`

Expected: FAIL，因为现有搭建文档仍把 ES/MySQL/NATS 作为默认主机端口服务说明。

- [ ] **Step 3: 更新使用、迁移、排障与回滚说明**

Linux/Windows 文档删除默认 ES/MySQL/NATS host port 表项，只保留 gRPC 的 loopback 调试说明；增加 `docker compose -f docker-compose.yml -f docker-compose.debug.yml ...` 仅排障使用、只绑定 127.0.0.1 的说明。明确不能用浏览器、curl 或 Kibana 直连默认 ES。

将完整迁移 runbook 写入设计与 setup 文档：创建并验证 ES snapshot；在维护窗口禁用 shard allocation、停止所有节点；保存 data volume 备份；构建精确镜像并校验插件；预置 production CA/node/admin/client 秘密；启动并让 bootstrap 完成；以 `rag_mvp` 验证 `/_searchguard/health`、索引限制和 RAG 检索；再恢复 allocation。加入“错误证书、密码或 bootstrap 不可通过时停止，不得关闭安全”的故障处置；若怀疑历史 9200 暴露，轮换密码/证书、检查操作日志、重建可信索引。

在 `testing-guide.md` 增加真实 suite 的安全覆盖、材料卷不应删除及 `make docker-down` 后保留数据卷的说明；不复制 Earthfile 内部完整命令。同步 SPEC/设计中的最终 service 名、file-secret 路径、healthcheck 两阶段原因和实际测试证据位置。

- [ ] **Step 4: 执行完整验证并记录真实结果**

Run: `git -c core.whitespace=cr-at-eol diff --check && make ci && make docker-test SUITE=all && make docker-down`

Expected: whitespace 检查、离线门禁、integration、Docker resilience、real eval 与安全停止均成功；若模型配置、Docker/Earthly、磁盘或网络使真实 suite 不能运行，保留容器诊断后执行 `make docker-down`，在提交/交接中逐项记录未运行原因，绝不声称通过。

- [ ] **Step 5: 最终检查并提交文档工作包**

```bash
git status --short
git add docs/SPEC.md docs/superpowers/specs/2026-08-28-search-guard-elasticsearch-security-design.md \
  docs/setup-linux.md docs/setup-windows.md docs/testing-guide.md .env.example tests/TEST.md
git commit -m "docs(search): 补充 Search Guard 运维与迁移手册"
```

提交前确认暂存内容只属于本任务；不提交 `data/search-guard/`、`tests/.search-guard/`、`.env`、真实 PDF 或 `tests/**/log/`。

## Self-Review

### Spec coverage

| 规格要求 | 覆盖任务 |
| --- | --- |
| 精确 ES/FLX 版本、插件 URL/checksum | Task 1 |
| transport/HTTP TLS、管理员证书、无 demo 材料 | Task 1、2 |
| Basic/internal user、backend-role 最小权限与无 REST 管理 API | Task 1、2、4 |
| 私网 9200、debug-only loopback override、MySQL/NATS 不公开 | Task 2、5、6 |
| HTTPS Basic 应用 client、SecretStr/file、无 TLS bypass | Task 3、5 |
| ES → bootstrap → migrate/application 顺序与 fail closed | Task 2、5 |
| 认证/错误 CA/权限拒绝与 RAG 能力真实验证 | Task 4 |
| integration/resilience/eval、日志秘密扫描、无 Secret 离线门禁 | Task 4、5、6 |
| 升级、快照、恢复、事件处置与不删卷 | Task 6 |

### Placeholder scan

已逐段检查：没有未决占位项或泛化的测试/错误处理描述；每个实现任务均给出路径、接口、失败测试、命令、实现内容和独立提交。

### Type consistency

`Settings.require_elasticsearch_profile()`、`ElasticsearchProfile` 与 `_search_resource()` 在 Task 3 定义，Task 4/5 使用的 `RAG_TEST_ELASTICSEARCH_*` 只服务测试 client，不改变应用 Settings 前缀。Task 1 的材料文件名固定为 Task 2/3/4/5 使用的挂载路径，避免名称漂移。
