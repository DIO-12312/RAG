"""Search Guard 构建资产与本地材料生成契约。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cryptography import x509

from scripts.search_guard import bootstrap, materials

ROOT = Path(__file__).resolve().parents[2]
MATERIALS_SCRIPT = ROOT / "scripts" / "search_guard" / "materials.py"


def test_development_material_generator_creates_separate_node_and_client_secrets(
    tmp_path: Path,
) -> None:
    """开发材料必须为节点和应用客户端分离私钥，并且不在输出中回显密码。"""

    node_output = tmp_path / "node"
    client_output = tmp_path / "client"

    completed = subprocess.run(
        [
            sys.executable,
            str(MATERIALS_SCRIPT),
            "--environment",
            "development",
            "--node-output",
            str(node_output),
            "--client-output",
            str(client_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (node_output / "node-key.pem").read_bytes() != (
        node_output / "admin-key.pem"
    ).read_bytes()
    password = (client_output / "rag_mvp_password").read_text(encoding="utf-8").strip()
    assert password
    assert (node_output / "rag_mvp_password").read_text(encoding="utf-8").strip() == password
    assert password not in completed.stdout
    assert password not in completed.stderr


def test_development_material_generator_writes_certificate_key_identifiers(
    tmp_path: Path,
) -> None:
    """生成的 CA/节点证书必须具备 Python TLS 校验所需的 SKI/AKI。"""

    node_output = tmp_path / "node"
    client_output = tmp_path / "client"

    materials.generate(node_output, client_output)

    ca_certificate = x509.load_pem_x509_certificate((node_output / "ca.pem").read_bytes())
    node_certificate = x509.load_pem_x509_certificate((node_output / "node.pem").read_bytes())
    ca_ski = ca_certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    node_aki = node_certificate.extensions.get_extension_for_class(
        x509.AuthorityKeyIdentifier
    ).value

    assert node_aki.key_identifier == ca_ski.digest


def test_development_material_validator_rejects_malformed_existing_files(
    tmp_path: Path,
) -> None:
    """旧卷中的畸形材料不能只因文件齐全就被当作可复用。"""

    node_output = tmp_path / "node"
    client_output = tmp_path / "client"
    node_output.mkdir()
    client_output.mkdir()
    for name in materials.NODE_FILES:
        (node_output / name).write_text("not a certificate", encoding="utf-8")
    for name in materials.CLIENT_FILES:
        (client_output / name).write_text("not a certificate", encoding="utf-8")

    assert not materials._validate_existing(node_output, client_output)


def test_production_material_generator_refuses_to_self_sign_missing_material(
    tmp_path: Path,
) -> None:
    """生产缺少外部材料时必须失败，而不是创建可误用的自签名证书。"""

    completed = subprocess.run(
        [
            sys.executable,
            str(MATERIALS_SCRIPT),
            "--environment",
            "production",
            "--node-output",
            str(tmp_path / "node"),
            "--client-output",
            str(tmp_path / "client"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "missing required Search Guard material" in completed.stderr


def test_search_guard_assets_pin_tls_and_least_privilege() -> None:
    """错误版本、缺 TLS 或全权限角色必须使安全构建契约失败。"""

    dockerfile = (ROOT / "Dockerfile.elasticsearch").read_text(encoding="utf-8")
    config = (ROOT / "docker" / "search-guard" / "elasticsearch.yml").read_text(encoding="utf-8")
    roles = (ROOT / "docker" / "search-guard" / "sgconfig" / "sg_roles.yml").read_text(
        encoding="utf-8"
    )

    assert "elasticsearch:8.19.19" in dockerfile
    assert "search-guard-flx-elasticsearch-plugin-4.1.2-es-8.19.19.zip" in dockerfile
    assert "6fa46190b1fd62f6c54d6c11d17757f043110f8c0db016e16c62c59b953f3c91" in dockerfile
    assert "searchguard.ssl.transport.pemcert_filepath" in config
    assert "searchguard.ssl.http.enabled: true" in config
    assert "xpack.security.enabled: false" in config
    assert "http.host: 0.0.0.0" in config
    assert "searchguard.nodes_dn" in config
    assert "SGS_ALL_ACCESS" not in roles
    assert '"rag-chunks-v1*"' in roles
    assert "indices:admin/get" in roles


def test_first_bootstrap_declares_search_guard_principals_in_extractor_order() -> None:
    """首次 SG11 初始化时，admin/node DN 必须匹配提取器的逆序 principal。"""

    config = (ROOT / "docker" / "search-guard" / "elasticsearch.yml").read_text(encoding="utf-8")

    assert "- C=CN,L=Local,O=RAG,OU=RAG,CN=elasticsearch" in config
    assert "- C=CN,L=Local,O=RAG,OU=RAG,CN=sg_admin" in config


def test_first_bootstrap_uploads_all_required_search_guard_config_types() -> None:
    """首次初始化必须提供 Search Guard 所需的五类配置文档。"""

    config_dir = ROOT / "docker" / "search-guard" / "sgconfig"

    assert set(bootstrap.STATIC_CONFIGS) == {
        "sg_action_groups.yml",
        "sg_authc.yml",
        "sg_roles.yml",
        "sg_roles_mapping.yml",
        "sg_tenants.yml",
    }
    for name in bootstrap.STATIC_CONFIGS:
        assert (config_dir / name).is_file()


def test_search_guard_operator_docs_preserve_private_tls_runbook() -> None:
    """规格、设计和运维文档必须隔离开发材料与尚待平台化的生产编排。"""

    linux_setup = (ROOT / "docs" / "setup" / "setup-linux.md").read_text(encoding="utf-8")
    windows_setup = (ROOT / "docs" / "setup" / "setup-windows.md").read_text(encoding="utf-8")
    testing_guide = (ROOT / "docs" / "test" / "testing-guide.md").read_text(encoding="utf-8")
    security_spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    earthfile = (ROOT / "Earthfile").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    security_design = (
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-08-28-search-guard-elasticsearch-security-design.md"
    ).read_text(encoding="utf-8")

    for setup in (linux_setup, windows_setup):
        assert "localhost:9200" not in setup
        assert "Search Guard" in setup
        assert "127.0.0.1:9200:9200" in setup
        assert "外部" in setup
        assert "production manifest" in setup
        assert "--environment production" in setup
        assert "snapshot" in setup
        assert "shard allocation" in setup
        assert "回滚" in setup
        assert "fail closed" in setup
        assert "启动 `rag-security-materials`" not in setup
        assert "docker compose down -v" not in setup
        assert "新的受保护目标数据卷/集群" in setup
        assert "已确认 snapshot restore" in setup
        assert "预期索引、文档计数/完整性与 RAG 可检索性" in setup
        assert "空新集群" in setup

    for document in (security_spec, security_design):
        assert "development/test" in document
        assert "rag-security-materials → elasticsearch → rag-search-guard-bootstrap" in document
        assert "独立、尚待平台化的 deployment manifest/编排" in document
        assert "只读挂载外部 CA/node/admin/client Secret" in document
        assert "禁止定义或启动 `rag-security-materials`" in document
        assert "--environment production" in document
        assert "fail closed" in document
        assert "阻断 ES、bootstrap 与下游服务启动" in document
        assert "后续工作" in document
        assert "新的受保护目标数据卷/集群" in document
        assert "已确认 snapshot restore" in document
        assert "预期索引、文档计数/完整性与一次 RAG 可检索性" in document
        assert "失败必须保持停止" in document

    assert "COPY SPEC.md PLAN.md AGENTS.md ./" in earthfile
    assert "docs/SPEC.md" not in agents
    assert "`SPEC.md`" in agents

    assert "make docker-test SUITE=all" in testing_guide
    assert "make docker-test SUITE=integration" in testing_guide
    assert "make docker-test SUITE=eval" in testing_guide
    assert "Search Guard" in testing_guide
    assert "TLS" in testing_guide
    assert "历史未受保护 ES 证据" in testing_guide
    assert "不可作为 Search Guard 验收" in testing_guide
    assert "仅描述 Search Guard 加固前的未受保护环境" in testing_guide
    assert "bootstrap 失败" in testing_guide
    assert "后续 suite 未运行" in testing_guide
    assert "docker compose --profile test run" not in testing_guide


def test_bootstrap_connect_allows_first_time_search_guard_initialization(monkeypatch) -> None:
    """首次安装的 SG11 状态必须允许 bootstrap 继续上传配置。"""

    calls: list[tuple[str, ...]] = []

    def successful_run(
        *arguments: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", successful_run)

    bootstrap._connect("elasticsearch", 9200, Path("/node-secrets"))

    assert "--skip-connection-check" in calls[0]


def test_bootstrap_upload_skips_connection_check_for_first_initialization(
    monkeypatch, tmp_path: Path
) -> None:
    """首次上传配置同样不能因尚无认证域而被 sgctl 拒绝。"""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in bootstrap.STATIC_CONFIGS:
        (config_dir / name).write_text("_readonly: {type: map}\n", encoding="utf-8")
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "rag_mvp_password").write_text("not-logged\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def successful_run(
        *arguments: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", successful_run)

    bootstrap._initialize(config_dir, client_dir, tmp_path / "work")

    assert calls[-1][-1] == "--skip-connection-check"


def test_bootstrap_retries_config_upload_until_elasticsearch_is_ready(
    monkeypatch, tmp_path: Path
) -> None:
    """ES 刚启动时的首次上传失败必须重试，而不是使依赖服务永久阻断。"""

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in bootstrap.STATIC_CONFIGS:
        (config_dir / name).write_text("{}\n", encoding="utf-8")
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "rag_mvp_password").write_text("not-logged\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    update_attempts = 0

    def eventually_ready(
        *arguments: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        nonlocal update_attempts
        calls.append(arguments)
        if arguments[0] == "update-config":
            update_attempts += 1
            if update_attempts == 1:
                return subprocess.CompletedProcess(arguments, 1, "", "SG11")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", eventually_ready)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    bootstrap._initialize(config_dir, client_dir, tmp_path / "work")

    assert update_attempts == 2
    assert sum(call[0] == "add-user-local" for call in calls) == 1
