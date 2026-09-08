"""Initialize or verify Search Guard configuration without logging credentials."""

from __future__ import annotations

import argparse
import base64
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SGCTL = ("java", "-jar", "/opt/sgctl-4.1.2-shaded.jar")
# Search Guard must receive every required configuration type on its first bootstrap.
STATIC_CONFIGS = (
    "sg_action_groups.yml",
    "sg_authc.yml",
    "sg_roles.yml",
    "sg_roles_mapping.yml",
    "sg_tenants.yml",
)
# Search Guard rewrites config on download (version headers, YAML reflow), so the
# byte-for-byte comparison is unreliable. These markers are the security-critical
# invariants that must survive in the running cluster.
_REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "sg_roles.yml": ("rag_mvp_search", "rag-chunks-v1*"),
    "sg_roles_mapping.yml": ("rag_mvp_search", "rag_mvp_runtime"),
    "sg_authc.yml": ("basic/internal_users_db",),
    "sg_internal_users.yml": ("rag_mvp:", "rag_mvp_runtime"),
}


def _run(*arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*SGCTL, *arguments],
        check=False,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _connect(host: str, port: int, node_dir: Path) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            completed = _run(
                "connect",
                host,
                "--skip-connection-check",
                "--port",
                str(port),
                "--ca-cert",
                str(node_dir / "ca.pem"),
                "--cert",
                str(node_dir / "admin.pem"),
                "--key",
                str(node_dir / "admin-key.pem"),
            )
            if completed.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    raise RuntimeError("Search Guard admin connection did not become ready")


def _copy_static_config(config_dir: Path, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in STATIC_CONFIGS:
        shutil.copyfile(config_dir / name, work_dir / name)


def _initialize(config_dir: Path, client_dir: Path, work_dir: Path) -> None:
    _copy_static_config(config_dir, work_dir)
    password = (client_dir / "rag_mvp_password").read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("Search Guard runtime password is empty")
    added = _run(
        "add-user-local",
        "rag_mvp",
        "--backend-roles",
        "rag_mvp_runtime",
        "--password",
        "--output",
        str(work_dir / "sg_internal_users.yml"),
        input_text=f"{password}\n{password}\n",
    )
    if added.returncode != 0:
        raise RuntimeError("could not create Search Guard internal user")
    # Docker marks Elasticsearch as started before its HTTPS endpoint accepts SG config.
    # Both non-zero exit codes and Java process timeouts are transient during ES warm-up.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            uploaded = _run("update-config", str(work_dir), "--skip-connection-check")
            if uploaded.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    raise RuntimeError("could not initialize Search Guard configuration")


def _verify_existing(work_dir: Path) -> bool:
    try:
        downloaded = _run("get-config", "--output", str(work_dir))
    except subprocess.TimeoutExpired:
        return False
    if downloaded.returncode != 0:
        return False
    for name, markers in _REQUIRED_MARKERS.items():
        content = (work_dir / name).read_text(encoding="utf-8")
        if any(marker not in content for marker in markers):
            raise RuntimeError(f"existing Search Guard {name} differs from the declared baseline")
    return True


def _verify_runtime_user(host: str, port: int, client_dir: Path) -> None:
    password = (client_dir / "rag_mvp_password").read_text(encoding="utf-8").strip()
    basic = base64.b64encode(f"rag_mvp:{password}".encode()).decode()
    request = urllib.request.Request(
        f"https://{host}:{port}/_searchguard/health",
        headers={"Authorization": f"Basic {basic}"},
    )
    context = ssl.create_default_context(cafile=str(client_dir / "ca.pem"))
    with urllib.request.urlopen(request, context=context, timeout=10) as response:  # noqa: S310
        payload = response.read()
    if b'"status":"UP"' not in payload.replace(b" ", b""):
        raise RuntimeError("Search Guard health is not UP for the runtime identity")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="initialize Search Guard")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--node-dir", type=Path, required=True)
    parser.add_argument("--client-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/search-guard-config"))
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    _connect(arguments.host, arguments.port, arguments.node_dir)
    if not _verify_existing(arguments.work_dir):
        _initialize(arguments.config_dir, arguments.client_dir, arguments.work_dir)
    _verify_runtime_user(arguments.host, arguments.port, arguments.client_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, urllib.error.URLError) as error:
        print(f"Search Guard bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
