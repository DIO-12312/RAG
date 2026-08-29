"""Search Guard 构建资产与本地材料生成契约。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.search_guard import bootstrap

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


def test_search_guard_operator_docs_preserve_private_tls_runbook() -> None:
    """运维文档必须避免默认 ES 暴露，并说明完整受保护验收入口。"""

    linux_setup = (ROOT / "docs" / "setup-linux.md").read_text(encoding="utf-8")
    windows_setup = (ROOT / "docs" / "setup-windows.md").read_text(encoding="utf-8")
    testing_guide = (ROOT / "docs" / "testing-guide.md").read_text(encoding="utf-8")

    for setup in (linux_setup, windows_setup):
        assert "localhost:9200" not in setup
        assert "Search Guard" in setup
        assert "127.0.0.1:9200:9200" in setup

    assert "make docker-test SUITE=all" in testing_guide
    assert "Search Guard" in testing_guide
    assert "TLS" in testing_guide


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
