"""Search Guard 构建资产与本地材料生成契约。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
    assert "searchguard.nodes_dn" in config
    assert "SGS_ALL_ACCESS" not in roles
    assert '"rag-chunks-v1*"' in roles
