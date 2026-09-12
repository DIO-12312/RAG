"""Release failure simulation; no live Docker, registry, SSH or production data."""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
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
                "images": {
                    name: f"{release.REGISTRY}/rag-{name}@sha256:{'c' * 64}"
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
    assert all(ref.startswith(f"{release.REGISTRY}/") for ref in docker.running.values())
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


def test_manifest_rejects_mutable_tag_wrong_sha_and_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docker = DockerSimulator(monkeypatch, tmp_path)
    original = release.read_json(docker.manifest)
    host = release.REGISTRY.split("/")[0]
    for ref in (
        f"{release.REGISTRY}/rag-api:latest",
        "evil.example/api@sha256:" + "c" * 64,
        f"{host}/other/rag-api@sha256:" + "c" * 64,
    ):
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
    for name in ("MIRROR", "HOST", "REGISTRY_USERNAME", "REGISTRY_TOKEN"):
        assert "secrets." + name in secrets
    assert "secrets.GITHUB_TOKEN" not in secrets
    assert "pull_request_target" not in json.dumps(workflow)
    assert all(not step.get("continue-on-error") for step in steps)
    sender = (ROOT / "deploy/production/send-release.sh").read_text()
    assert "StrictHostKeyChecking=yes" in sender
    assert "systemd-run --wait" in sender
    assert "--password-stdin" in sender
    assert "refs/heads/main" in sender


def test_release_registry_is_one_approved_prefix_shared_by_publish_and_ci() -> None:
    """仓库常量是发布、部署校验与 CI 登录的唯一来源，禁止散落硬编码主机名。"""

    assert re.fullmatch(r"[a-z0-9.-]+/[a-z0-9._-]+", release.REGISTRY)
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["release"]["steps"]
    for step in steps:
        if "docker login" in step.get("run", "") or "docker logout" in step.get("run", ""):
            assert "scripts/release.py registry" in step["run"]
    sender = (ROOT / "deploy/production/send-release.sh").read_text()
    assert "scripts/release.py registry" in sender
    assert release.REGISTRY.split("/")[0] not in sender
    printed = subprocess.run(
        [sys.executable, "scripts/release.py", "registry"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert printed.stdout.strip() == release.REGISTRY


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
        f"{release.REGISTRY}/rag-web@sha256:" + "e" * 64
    )
