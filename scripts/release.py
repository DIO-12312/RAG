"""GHCR release construction and serialized, application-only Compose deployment.

Uses only the standard library so the production host needs Python, Docker and Earthly.
Rendered Compose and credentials are host-local; release.json contains no secrets.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

APPS = ("rag-server", "rag-worker", "rag-outbox", "api", "web")
_PROJECT_NAME = "rag-production"
INFRA = ("rag-mysql", "product-mysql", "elasticsearch", "nats")
IMAGES = {
    "rag": ("Dockerfile", ".", "runtime"),
    "api": ("backend/go-api/Dockerfile", "backend/go-api", None),
    "web": ("apps/web/Dockerfile", "apps/web", None),
    "search-guard": ("Dockerfile.search-guard-bootstrap", ".", None),
    "elasticsearch": ("Dockerfile.elasticsearch", ".", None),
}
APP_IMAGE = {
    "rag-server": "rag",
    "rag-worker": "rag",
    "rag-outbox": "rag",
    "api": "api",
    "web": "web",
}
# Only persistent schema and production infrastructure changes require a
# maintenance release. Application images are replaced as one unit.
MAINTENANCE_PATHS = (
    "compose.production.yml",
    "deploy/production/Caddyfile",
    "migrations",
    "alembic.ini",
    "backend/go-api/internal/storage",
    "docker/search-guard",
    "scripts/search_guard",
    "Dockerfile.elasticsearch",
    "Dockerfile.search-guard-bootstrap",
)
MAX_COMPATIBLE_BASES = 128


class ReleaseError(RuntimeError):
    pass


def run(
    args: list[str], *, cwd: Path | None = None, timeout: int = 600, visible: bool = False
) -> str:
    """Do not echo commands or captured output: Compose may contain private settings."""
    result = subprocess.run(args, cwd=cwd, capture_output=not visible, text=True, timeout=timeout)
    if result.returncode:
        raise ReleaseError(f"{args[0]} operation failed (exit {result.returncode})")
    return (result.stdout or "").strip()


def write_json(path: Path, value: Any) -> None:
    """Atomic state replacement, including crash recovery records."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        os.chmod(stream.name, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
        temp = stream.name
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ReleaseError("invalid JSON object")
    return value


# 只有持久 schema 与生产基础设施变化才需要维护窗口。Service 的
# environment/env_file/build/labels 只是「重建容器即替换」的启动参数：
# 改一个 env 默认值不应该要求维护窗口，否则每次调整配置都会被发布门禁拦住。
# volumes/ports/secrets/command/entrypoint/healthcheck/depends_on 等仍然参与摘要。
RESTART_ONLY_KEYS = frozenset({"build", "environment", "env_file", "labels"})
_KEY = re.compile(r"^[ \t]*(?:-[ \t]*)?([A-Za-z0-9_.\-]+)[ \t]*:")
_ALIAS = re.compile(r"^\*([A-Za-z0-9_.\-]+)$")


# 前导空白宽度：用字符串运算而不是正则，避免可选匹配带来的类型分支。
def _indent_width(line: str) -> int:
    """Return the width of the leading whitespace of a Compose line."""

    return len(line) - len(line.lstrip(" \t"))


# 统计锚点被引用的位置：出现在重启即替换的块里的锚点不参与摘要。
def _anchor_usage(text: str) -> tuple[set[str], set[str]]:
    restart_only: set[str] = set()
    elsewhere: set[str] = set()
    stack: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent_width(line)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        key_match = _KEY.match(line)
        if key_match is None or ":" not in line:
            continue
        key = key_match.group(1)
        value = line.split(":", 1)[1].strip()
        alias = _ALIAS.match(value)
        if alias is not None:
            # `environment: *x` 与 `<<: *x` 由自身/父键决定；
            # 更深层的 `FOO: *x` 则由它所在的块决定。
            owner = parent if key == "<<" else key
            in_restart_block = any(item[1] in RESTART_ONLY_KEYS for item in stack)
            target = restart_only if owner in RESTART_ONLY_KEYS or in_restart_block else elsewhere
            target.add(alias.group(1))
        if not value:
            stack.append((indent, key))
    return restart_only, elsewhere


def strip_restart_only_blocks(text: str) -> str:
    """Drop restart-only service blocks before hashing a rendered Compose input."""

    restart_only, elsewhere = _anchor_usage(text)
    dropped_anchors = restart_only - elsewhere
    kept: list[str] = []
    skip_indent: int | None = None
    drop_definition = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        indent = _indent_width(line) if stripped else 0
        if skip_indent is not None:
            if not stripped:
                continue
            if indent > skip_indent or (indent == skip_indent and stripped.startswith("-")):
                continue
            skip_indent = None
        if drop_definition:
            if not stripped:
                continue
            if indent > 0:
                continue
            drop_definition = False
        if not stripped:
            kept.append(line)
            continue
        key_match = _KEY.match(line)
        key = key_match.group(1) if key_match is not None else ""
        if key in RESTART_ONLY_KEYS:
            skip_indent = indent
            continue
        if indent == 0 and key.startswith("x-") and "&" in line:
            anchor = line.split("&", 1)[1].strip()
            if anchor in dropped_anchors:
                drop_definition = True
                continue
        kept.append(line)
    return "\n".join(kept) + "\n"


# 只有 Compose 需要投影：其余维护敏感输入（schema、Caddyfile、migrations）整体参与摘要。
PROJECTED_INPUTS = {"compose.production.yml": strip_restart_only_blocks}


def compatibility(root: Path) -> str:
    digest = hashlib.sha256()
    for name in MAINTENANCE_PATHS:
        path = root / name
        if not path.exists():
            raise ReleaseError(f"missing compatibility input: {name}")
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in files:
            if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
                digest.update(str(item.relative_to(root)).encode() + b"\0")
                payload = item.read_bytes()
                projector = PROJECTED_INPUTS.get(name)
                if projector is not None:
                    payload = projector(payload.decode("utf-8")).encode("utf-8")
                digest.update(payload + b"\0")
    return digest.hexdigest()


def compatible_base_shas(root: Path, sha: str) -> list[str]:
    """Return recent ancestors whose maintenance-sensitive trees match this release."""

    history = run(
        ["git", "rev-list", f"--max-count={MAX_COMPATIBLE_BASES}", sha],
        cwd=root,
    ).splitlines()
    compatible: list[str] = []
    for candidate in history:
        changed = run(
            ["git", "diff", "--name-only", candidate, sha, "--", *MAINTENANCE_PATHS],
            cwd=root,
        )
        if not changed:
            compatible.append(candidate)
    return compatible


def validate_release(value: dict[str, Any], sha: str, root: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or value.get("sha") != sha:
        raise ReleaseError("release SHA mismatch")
    if value.get("compatibility") != compatibility(root):
        raise ReleaseError("release compatibility fingerprint mismatch")
    bases = value.get("compatible_base_shas")
    if (
        not isinstance(bases, list)
        or sha not in bases
        or any(
            not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{40}", item) for item in bases
        )
    ):
        raise ReleaseError("release compatible base list is invalid")
    images = value.get("images", {})
    if set(images) != set(IMAGES):
        raise ReleaseError("release image set mismatch")
    for name, ref in images.items():
        if not isinstance(ref, str) or not re.fullmatch(
            rf"ghcr\.io/dio-12312/rag-{name}@sha256:[0-9a-f]{{64}}", ref
        ):
            raise ReleaseError("release requires approved GHCR digest references")


def publish(root: Path, sha: str, output: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReleaseError("expected full commit SHA")
    if run(["git", "rev-parse", "HEAD"], cwd=root) != sha:
        raise ReleaseError("checkout does not match release SHA")
    images = {}
    with tempfile.TemporaryDirectory() as temp:
        for name, (dockerfile, context, target) in IMAGES.items():
            print(f"Building and publishing {name}", flush=True)
            tag = f"ghcr.io/dio-12312/rag-{name}:{sha}"
            metadata = Path(temp) / f"{name}.json"
            command = [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                dockerfile,
                "--tag",
                tag,
                "--push",
                "--label",
                f"org.opencontainers.image.revision={sha}",
                "--label",
                "org.opencontainers.image.source=https://github.com/DIO-12312/RAG",
                "--metadata-file",
                str(metadata),
            ]
            if target:
                command += ["--target", target]
            if name == "web":
                # 前端把 commit 编入产物，页面才能确认线上镜像对应的推送版本。
                command += ["--build-arg", f"VITE_GIT_COMMIT={sha}"]
            run([*command, context], cwd=root, timeout=2400, visible=True)
            images[name] = (
                tag.rsplit(":", 1)[0] + "@" + read_json(metadata)["containerimage.digest"]
            )
    value = {
        "sha": sha,
        "compatibility": compatibility(root),
        "compatible_base_shas": compatible_base_shas(root, sha),
        "images": images,
    }
    validate_release(value, sha, root)
    write_json(output, value)


def compose(config: Path, *args: str) -> list[str]:
    return ["docker", "compose", "--project-name", _PROJECT_NAME, "-f", str(config), *args]


def inspect_service(config: Path, service: str) -> dict[str, Any]:
    ids = run(compose(config, "ps", "-a", "-q", service)).splitlines()
    if len(ids) != 1:
        raise ReleaseError(f"expected one existing production container: {service}")
    value: dict[str, Any] = json.loads(run(["docker", "inspect", ids[0]]))[0]
    return value


# 网络定义变更必须在停任何容器之前被发现：`up --force-recreate` 会为定义变化的
# 网络触发重建，而此时 MySQL/Elasticsearch/NATS 仍挂在网络上，重建必然失败——
# 应用已经被停掉，回滚又会撞上同一堵墙，生产就这样变成 502。
def _declared_network_subnets(config: Mapping[str, Any]) -> dict[str, list[str]]:
    """Map docker network name to the subnets the target config pins."""

    declared: dict[str, list[str]] = {}
    for name, definition in (config.get("networks") or {}).items():
        subnets = [
            str(item["subnet"])
            for item in (((definition or {}).get("ipam") or {}).get("config") or [])
            if item.get("subnet")
        ]
        if not subnets:
            continue
        explicit = (definition or {}).get("name")
        docker_name = str(explicit) if explicit else f"{_PROJECT_NAME}_{name}"
        declared[docker_name] = subnets
    return declared


# 读取运行中网络的子网；网络不存在或 docker 不可用时返回 None，交由后续步骤报错。
def _live_network_subnets(docker_name: str) -> list[str] | None:
    try:
        output = run(
            [
                "docker",
                "network",
                "inspect",
                docker_name,
                "--format",
                "{{range .IPAM.Config}}{{.Subnet}} {{end}}",
            ]
        )
    except ReleaseError:
        return None
    return output.split()


# 目标配置固定了子网、且运行中网络已有不同子网时拒绝发布，避免不可回滚的停机。
def check_network_compatibility(config: Mapping[str, Any]) -> None:
    """Refuse a deploy whose pinned network subnets differ from the live ones."""

    for docker_name, declared in _declared_network_subnets(config).items():
        live = _live_network_subnets(docker_name)
        if not live or live == declared:
            continue
        raise ReleaseError(
            f"network {docker_name} subnet drift: live={'/'.join(live)} "
            f"declared={'/'.join(declared)}; recreate networks in a maintenance window "
            "before deploying"
        )


def health(config: Path) -> None:
    for service in (*INFRA, *APPS, "caddy"):
        state = inspect_service(config, service)["State"]
        if (
            not state.get("Running")
            or state.get("Health", {}).get("Status", "healthy") != "healthy"
        ):
            raise ReleaseError(f"production service not healthy: {service}")
    # Actual HTTP responses, not just container liveness / SPA status 200.
    for url in ("http://127.0.0.1:8080/readyz", "http://web/readyz", "http://web/healthz"):
        body = run(compose(config, "exec", "-T", "api", "wget", "-q", "-O", "-", url))
        if json.loads(body).get("status") != "ok":
            raise ReleaseError("API/web probe did not return healthy JSON")
    body = run(compose(config, "exec", "-T", "api", "wget", "-q", "-O", "-", "http://web/"))
    if "<html" not in body.lower():
        raise ReleaseError("web page probe failed")
    public = read_json(config)["services"]["caddy"]["environment"]["RAG_PUBLIC_SITE_ADDRESS"]
    host = public.removeprefix("http://").removeprefix("https://").rstrip("/")
    body = run(
        compose(
            config,
            "exec",
            "-T",
            "api",
            "wget",
            "-q",
            "-O",
            "-",
            "--header",
            f"Host: {host}",
            "http://caddy/healthz",
        )
    )
    if json.loads(body).get("status") != "ok":
        raise ReleaseError("Caddy public route probe failed")


def render(root: Path, env_file: Path) -> dict[str, Any]:
    # Render only on the host; this result must never become a CI artifact.
    raw = run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "--project-name",
            "rag-production",
            "-f",
            str(root / "compose.production.yml"),
            "config",
            "--format",
            "json",
        ]
    )
    config: dict[str, Any] = json.loads(raw)
    for service in config["services"].values():
        service.pop("build", None)
        service.pop("pull_policy", None)
    return config


def capture(root: Path, env_file: Path, state: Path, sha: str) -> None:
    if (state / "active.json").exists():
        raise ReleaseError("baseline already exists; use maintenance procedure to replace it")
    if run(["git", "rev-parse", "HEAD"], cwd=root) != sha:
        raise ReleaseError("baseline must identify the deployed checkout HEAD")
    config = render(root, env_file)
    path = state / "baseline.compose.json"
    write_json(path, config)
    health(path)
    for name in config["services"]:
        container = inspect_service(path, name)
        image = container["Image"]
        tag = f"rag-rollback/{name}:baseline-{sha}"
        run(["docker", "tag", image, tag])
        config["services"][name]["image"] = tag
    write_json(path, config)
    write_json(
        state / "active.json",
        {"sha": sha, "compatibility": compatibility(root), "config": str(path), "sequence": 0},
    )


def activate(config: Path) -> None:
    # Stop all app writers before replacement. No migrations or infrastructure starts.
    run(compose(config, "stop", "--timeout", "60", *APPS))
    for services in (("rag-server", "rag-worker", "rag-outbox"), ("api",), ("web",)):
        run(
            compose(
                config,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "180",
                *services,
            )
        )
    # Web nginx resolves api at startup; Caddy must also discard pooled upstreams.
    run(compose(config, "start", "caddy"))
    run(
        compose(
            config,
            "exec",
            "-T",
            "caddy",
            "caddy",
            "reload",
            "--force",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        )
    )
    health(config)
    time.sleep(3)
    health(config)


def recover(state: Path) -> None:
    pending = state / "pending.json"
    if pending.exists():
        old = read_json(pending)["previous"]
        print("Recovering interrupted/failed release", flush=True)
        activate(Path(old["config"]))
        write_json(state / "active.json", old)
        pending.unlink()


def deploy(root: Path, manifest: Path, state: Path, sha: str, sequence: int) -> None:
    recover(state)
    release = read_json(manifest)
    validate_release(release, sha, root)
    active = read_json(state / "active.json")
    if sequence <= active["sequence"]:
        raise ReleaseError("stale release sequence; refusing to replace a newer deployment")
    if (
        active["compatibility"] != release["compatibility"]
        and active["sha"] not in release["compatible_base_shas"]
    ):
        raise ReleaseError(
            "persistent schema/infrastructure changed: maintenance deployment required"
        )
    old_path = Path(active["config"])
    config = read_json(old_path)
    # 目标配置的网络定义必须与运行中的网络一致，否则拒绝在停机之前。
    check_network_compatibility(config)
    health(old_path)
    # Detect manual image changes since baseline/last release before touching services.
    for service in APPS:
        expected = run(
            [
                "docker",
                "image",
                "inspect",
                config["services"][service]["image"],
                "--format",
                "{{.Id}}",
            ]
        )
        if inspect_service(old_path, service)["Image"] != expected:
            raise ReleaseError("running images drifted from deployment state")
    for name in ("rag", "api", "web"):
        run(["docker", "pull", release["images"][name]], timeout=1200)
        revision = run(
            [
                "docker",
                "image",
                "inspect",
                release["images"][name],
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            ]
        )
        if revision != sha:
            raise ReleaseError("pulled image revision does not match release SHA")
    config = copy.deepcopy(config)
    for service in APPS:
        config["services"][service]["image"] = release["images"][APP_IMAGE[service]]
    new_path = state / f"{sequence}-{sha}.compose.json"
    write_json(new_path, config)
    run(compose(new_path, "config", "--quiet"))
    write_json(state / "pending.json", {"previous": active, "candidate": sha})
    try:
        activate(new_path)
        write_json(state / "previous.json", active)
        write_json(
            state / "active.json",
            {
                "sha": sha,
                "sequence": sequence,
                "compatibility": release["compatibility"],
                "config": str(new_path),
            },
        )
        (state / "pending.json").unlink()
    except (Exception, KeyboardInterrupt):
        try:
            recover(state)
        except Exception:
            raise ReleaseError(
                "rollback failed; journal retained; run production-recover"
            ) from None
        raise ReleaseError("deployment failed; previous application images restored") from None
    print(f"Deployed {sha}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("publish", "baseline", "deploy", "recover"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--sha", default="")
    parser.add_argument("--sequence", type=int, default=0)
    parser.add_argument("--manifest", type=Path, default=Path("release.json"))
    parser.add_argument("--state", type=Path, default=Path("/var/lib/rag-deploy"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/rag-mvp/.env.production"))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == "publish":
            publish(args.root, args.sha, args.manifest)
            return
        args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (args.state / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "baseline":
                capture(args.root, args.env_file, args.state, args.sha)
            elif args.action == "recover":
                recover(args.state)
            else:
                deploy(args.root, args.manifest, args.state, args.sha, args.sequence)
    except (ReleaseError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # No exception payload: external failures may contain private paths or settings.
        if isinstance(exc, ReleaseError):
            print(str(exc), flush=True)
        else:
            print(f"Release stopped: {type(exc).__name__}", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
