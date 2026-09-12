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
    # Earthly 在 CI 中必须使用非交互模式，与 quality.yml 的发布门禁保持一致。
    assert workflow["jobs"]["release"]["env"]["EARTHLY_FLAGS"] == "--ci"
    steps = workflow["jobs"]["release"]["steps"]
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
    assert "systemd-run --wait" in sender
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
