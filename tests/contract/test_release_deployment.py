"""Release failure simulation; no live Docker, registry, SSH or production data."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from scripts import release

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


class DockerSimulator:
    """Command-level Docker boundary with real release files and rollback journal."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.state = tmp_path / "state"
        self.calls: list[list[str]] = []
        self.failures: list[str] = []
        self.old = {name: f"old-{name}" for name in release.APPS}
        self.running = dict(self.old)
        self.config = self.state / "baseline.json"
        release.write_json(
            self.config, {"services": {name: {"image": ref} for name, ref in self.old.items()}}
        )
        self.previous = {
            "sha": "b" * 40,
            "sequence": 1,
            "compatibility": release.compatibility(ROOT),
            "config": str(self.config),
        }
        release.write_json(self.state / "active.json", self.previous)
        self.manifest = tmp_path / "release.json"
        release.write_json(
            self.manifest,
            {
                "sha": SHA,
                "compatibility": release.compatibility(ROOT),
                "compatible_base_shas": [SHA],
                "images": {
                    name: f"ghcr.io/dio-12312/rag-{name}@sha256:{'c' * 64}"
                    for name in release.IMAGES
                },
            },
        )
        monkeypatch.setattr(release, "run", self.run)
        monkeypatch.setattr(release, "health", lambda _: None)
        monkeypatch.setattr(release.time, "sleep", lambda _: None)
        monkeypatch.setattr(release, "inspect_service", self.inspect)

    def inspect(self, config: Path, service: str) -> dict[str, str]:
        return {"Image": self.running[service]}

    def run(self, args: list[str], **kwargs: object) -> str:
        self.calls.append(args)
        if self.failures and self.failures[0] in args:
            self.failures.pop(0)
            raise release.ReleaseError("injected Docker failure")
        if args[1:3] == ["image", "inspect"]:
            if "revision" in args[-1]:
                return SHA
            return args[3]
        if "up" in args:
            config = release.read_json(Path(args[args.index("-f") + 1]))
            for name in release.APPS:
                if name in args:
                    self.running[name] = config["services"][name]["image"]
        return ""

    def deploy(self, sequence: int = 2) -> None:
        release.deploy(ROOT, self.manifest, self.state, SHA, sequence)


def test_release_success_persists_previous_and_never_recreates_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    docker.deploy()
    active = release.read_json(docker.state / "active.json")
    assert active["sha"] == SHA
    assert release.read_json(docker.state / "previous.json") == docker.previous
    assert not (docker.state / "pending.json").exists()
    assert all(ref.startswith("ghcr.io/") for ref in docker.running.values())
    for command in docker.calls:
        assert "down" not in command and "build" not in command
        if "up" in command:
            assert "--no-deps" in command and "--no-build" in command
            assert command[command.index("--pull") + 1] == "never"
            assert not set(release.INFRA) & set(command)
            assert "rag-migrate" not in command


@pytest.mark.parametrize("failure", ["pull", "api", "reload"])
def test_release_failure_restores_images_and_preserves_active_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    docker.failures = [failure]
    with pytest.raises(release.ReleaseError):
        docker.deploy()
    assert docker.running == docker.old
    assert release.read_json(docker.state / "active.json") == docker.previous
    assert not (docker.state / "pending.json").exists()
    if failure == "pull":
        assert not any("stop" in call for call in docker.calls)


def test_failed_rollback_keeps_journal_for_next_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    docker.failures = ["api", "api"]
    with pytest.raises(release.ReleaseError):
        docker.deploy()
    assert (docker.state / "pending.json").is_file()
    release.recover(docker.state)
    assert docker.running == docker.old
    assert not (docker.state / "pending.json").exists()


def test_schema_change_and_stale_release_fail_before_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    with pytest.raises(release.ReleaseError, match="stale"):
        docker.deploy(sequence=1)
    previous = copy.deepcopy(docker.previous)
    previous["compatibility"] = "changed"
    release.write_json(docker.state / "active.json", previous)
    with pytest.raises(release.ReleaseError, match="maintenance"):
        docker.deploy()
    assert not docker.calls


def test_legacy_fingerprint_allows_safe_application_only_transition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """旧版摘要可由清单中的同基础设施祖先安全迁移到新版摘要。"""

    docker = DockerSimulator(monkeypatch, tmp_path)
    previous = copy.deepcopy(docker.previous)
    previous["compatibility"] = "legacy-fingerprint"
    release.write_json(docker.state / "active.json", previous)
    manifest = release.read_json(docker.manifest)
    manifest["compatible_base_shas"] = [previous["sha"], SHA]
    release.write_json(docker.manifest, manifest)

    docker.deploy()

    active = release.read_json(docker.state / "active.json")
    assert active["sha"] == SHA
    assert active["compatibility"] == release.compatibility(ROOT)


def test_manifest_rejects_mutable_tag_wrong_sha_and_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    original = release.read_json(docker.manifest)
    for ref in ("ghcr.io/dio-12312/rag-api:latest", "evil.example/api@sha256:" + "c" * 64):
        value = copy.deepcopy(original)
        value["images"]["api"] = ref
        with pytest.raises(release.ReleaseError, match="digest"):
            release.validate_release(value, SHA, ROOT)
    with pytest.raises(release.ReleaseError, match="SHA"):
        release.validate_release(original, "d" * 40, ROOT)
    assert not docker.calls


def test_deploy_workflow_requires_checks_and_uses_existing_secret_names() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    assert workflow["on"] == {"push": {"branches": ["main"]}}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    job = workflow["jobs"]["release"]
    # --ci 隐含 --strict，Earthly 会拒绝 LOCALLY；发布工作区不得在 job 级继承它。
    assert "EARTHLY_FLAGS" not in job["env"]
    steps = job["steps"]
    checkout = next(step for step in steps if "actions/checkout" in step.get("uses", ""))
    assert checkout["with"]["fetch-depth"] == 0
    checks = next(step for step in steps if step.get("run") == "make release-check")
    assert checks["env"] == {"EARTHLY_FLAGS": "--ci"}
    publish = next(step for step in steps if "make release-publish" in step.get("run", ""))
    assert "EARTHLY_FLAGS" not in publish.get("env", {})
    release_publish = (ROOT / "Earthfile").read_text().split("\nrelease-publish:\n", 1)[1]
    assert "    LOCALLY" in release_publish.split("\n# ", 1)[0]
    commands = [step.get("run", "") for step in steps]
    assert commands.index("make release-check") < commands.index(
        'make release-publish RELEASE_SHA="$GITHUB_SHA"'
    )
    secrets = json.dumps(steps)
    for name in ("MIRROR", "HOST", "GHCR_USERNAME", "GHCR_TOKEN"):
        assert "secrets." + name in secrets
    assert "pull_request_target" not in json.dumps(workflow)
    assert all(not step.get("continue-on-error") for step in steps)
    sender = (ROOT / "deploy/production/send-release.sh").read_text()
    assert "StrictHostKeyChecking=yes" in sender
    assert "systemd-run --wait --pipe --collect" in sender
    assert "--password-stdin" in sender
    assert "refs/heads/main" in sender


def test_health_failure_after_switch_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)

    def unhealthy_candidate(config: Path) -> None:
        if config != docker.config:
            raise release.ReleaseError("candidate probe failed")

    monkeypatch.setattr(release, "health", unhealthy_candidate)
    with pytest.raises(release.ReleaseError, match="restored"):
        docker.deploy()
    assert docker.running == docker.old
    assert release.read_json(docker.state / "active.json") == docker.previous


def test_image_drift_and_revision_mismatch_block_before_stopping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    docker.running["api"] = "manually-replaced"
    with pytest.raises(release.ReleaseError, match="drifted"):
        docker.deploy()
    docker.running = dict(docker.old)
    original_run = docker.run

    def wrong_revision(args: list[str], **kwargs: object) -> str:
        if "revision" in args[-1]:
            return "d" * 40
        return original_run(args, **kwargs)

    monkeypatch.setattr(release, "run", wrong_revision)
    with pytest.raises(release.ReleaseError, match="revision"):
        docker.deploy()
    assert not any("stop" in call for call in docker.calls)


def test_baseline_uses_actual_image_ids_and_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    state = tmp_path / "baseline-state"
    config = release.read_json(docker.config)
    monkeypatch.setattr(release, "render", lambda *_: config)
    original_run = docker.run

    def baseline_run(args: list[str], **kwargs: object) -> str:
        if args[0] == "git":
            return SHA
        return original_run(args, **kwargs)

    monkeypatch.setattr(release, "run", baseline_run)
    release.capture(ROOT, tmp_path / "env", state, SHA)
    assert release.read_json(state / "active.json")["sequence"] == 0
    tags = [call for call in docker.calls if call[1] == "tag"]
    assert {call[2] for call in tags} == set(docker.old.values())
    assert not any("stop" in call or "up" in call for call in docker.calls)
    with pytest.raises(release.ReleaseError, match="already exists"):
        release.capture(ROOT, tmp_path / "env", state, SHA)


def test_publish_injects_release_sha_into_web_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Web 镜像必须把发布 SHA 编入前端产物，页面才能确认实际部署的 commit。"""

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> str:
        calls.append(args)
        if args[:2] == ["git", "rev-parse"]:
            return SHA
        if args[:2] == ["git", "rev-list"]:
            return SHA
        if args[:3] == ["git", "diff", "--name-only"]:
            return ""
        if "--metadata-file" in args:
            release.write_json(
                Path(args[args.index("--metadata-file") + 1]),
                {"containerimage.digest": "sha256:" + "e" * 64},
            )
        return ""

    monkeypatch.setattr(release, "run", fake_run)
    manifest = tmp_path / "release.json"
    release.publish(ROOT, SHA, manifest)

    web = next(call for call in calls if any("rag-web:" in arg for arg in call))
    assert f"VITE_GIT_COMMIT={SHA}" in web
    rag = next(call for call in calls if any("rag-rag:" in arg for arg in call))
    assert not any("VITE_GIT_COMMIT" in arg for arg in rag)
    assert release.read_json(manifest)["images"]["web"] == (
        "ghcr.io/dio-12312/rag-web@sha256:" + "e" * 64
    )
    assert release.read_json(manifest)["compatible_base_shas"] == [SHA]


COMPOSE_TEMPLATE = """name: rag-production

x-rag-environment: &rag-environment
  RAG_ENVIRONMENT: production
  RAG_MAX_UPLOAD_BYTES: ${{RAG_MAX_UPLOAD_BYTES:-33554432}}

services:
  api:
    image: ghcr.io/dio-12312/rag-api@sha256:{digest}
    environment: *rag-environment
    volumes:
      - {volume}
    ports:
      - "8080:8080"
    secrets: *rag-secrets

x-rag-secrets: &rag-secrets
  - source: product_mysql_dsn
    target: /run/secrets/product_mysql_dsn
"""


def _compose_text(*, upload_limit: str = "33554432", volume: str = "obj:/app/data/objects") -> str:
    """构造一份最小 Compose 输入，用于验证摘要投影。"""

    return COMPOSE_TEMPLATE.format(digest="a" * 64, volume=volume).replace(
        ":-33554432", f":-{upload_limit}"
    )


def test_restart_only_projection_ignores_env_and_build_but_keeps_infrastructure() -> None:
    """只改 env 默认值或 build 参数的 Compose 变更不应改变维护敏感摘要。"""

    base = _compose_text()
    env_only = _compose_text(upload_limit="67108864")
    assert release.strip_restart_only_blocks(base) == release.strip_restart_only_blocks(env_only)

    build_added = base.replace(
        "    environment: *rag-environment\n",
        "    environment: *rag-environment\n    build:\n      args:\n        VITE: 1\n",
    )
    assert release.strip_restart_only_blocks(base) == release.strip_restart_only_blocks(build_added)

    volume_changed = _compose_text(volume="other:/app/data/objects")
    assert release.strip_restart_only_blocks(base) != release.strip_restart_only_blocks(
        volume_changed
    )
    port_changed = base.replace('"8080:8080"', '"9090:8080"')
    assert release.strip_restart_only_blocks(base) != release.strip_restart_only_blocks(
        port_changed
    )


def test_restart_only_projection_keeps_every_infrastructure_key_of_real_compose() -> None:
    """生产 Compose 投影后必须仍是合法 YAML，且只丢弃重启即替换的键。"""

    original = (ROOT / "compose.production.yml").read_text()
    projected_text = release.strip_restart_only_blocks(original)
    original_config = yaml.safe_load(original)
    projected_config = yaml.safe_load(projected_text)

    assert set(projected_config["services"]) == set(original_config["services"])
    for name, service in original_config["services"].items():
        expected = set(service) - release.RESTART_ONLY_KEYS
        assert set(projected_config["services"][name]) == expected, name
    for key in ("volumes", "networks", "secrets", "configs"):
        assert projected_config.get(key) == original_config.get(key)


def test_compatibility_digest_moves_only_for_infrastructure_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """compatibility() 只对持久基础设施变化换摘要，env 默认值变化保持不变。"""

    monkeypatch.setattr(release, "MAINTENANCE_PATHS", ("compose.production.yml",))

    def tree(name: str, text: str) -> Path:
        root = tmp_path / name
        root.mkdir()
        (root / "compose.production.yml").write_text(text)
        return root

    base = tree("base", _compose_text())
    env_only = tree("env", _compose_text(upload_limit="67108864"))
    infrastructure = tree("infra", _compose_text(volume="other:/app/data/objects"))

    assert release.compatibility(base) == release.compatibility(env_only)
    assert release.compatibility(base) != release.compatibility(infrastructure)
