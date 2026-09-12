#!/usr/bin/env bash
# Runs on GitHub-hosted runner. All secrets go through stdin, never SSH arguments.
set -euo pipefail
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$GITHUB_RUN_NUMBER" =~ ^[0-9]+$ ]]
[[ "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]]
[[ "$REGISTRY_USERNAME" =~ ^[a-zA-Z0-9._-]+$ ]]
test -n "$REGISTRY_TOKEN"
# 登录主机与发布/校验共用 release.py 的唯一仓库常量，避免两处手工配置漂移。
registry=$(python3 scripts/release.py registry)
registry_host="${registry%%/*}"
ssh_args=(-i "$RUNNER_TEMP/deploy-ssh/key" -o BatchMode=yes -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$RUNNER_TEMP/deploy-ssh/known_hosts"
  -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
target=root@49.235.110.118
release_dir="/data/RAG/.releases/$GITHUB_SHA-$GITHUB_RUN_NUMBER-$GITHUB_RUN_ATTEMPT"
# Skip outdated queued pushes; the host also rejects older successful run numbers.
main_sha=$(git ls-remote https://github.com/DIO-12312/RAG.git refs/heads/main | cut -f1)
if [[ "$main_sha" != "$GITHUB_SHA" ]]; then
  echo 'A newer main push exists; skipping this deployment.'
  exit 0
fi
ssh "${ssh_args[@]}" -p 22 "$target" 'test -f /var/lib/rag-deploy/active.json'
git archive --format=tar.gz HEAD -o "$RUNNER_TEMP/release.tar.gz"
ssh "${ssh_args[@]}" -p 22 "$target" "umask 077; mkdir -p /data/RAG/.releases; mkdir '$release_dir'"
scp "${ssh_args[@]}" -P 22 "$RUNNER_TEMP/release.tar.gz" release.json "$target:$release_dir/"
ssh "${ssh_args[@]}" -p 22 "$target" "tar -xzf '$release_dir/release.tar.gz' -C '$release_dir'"
# Store pull-only credentials with root's Docker credential store; no token in process args.
printf '%s' "$REGISTRY_TOKEN" | ssh "${ssh_args[@]}" -p 22 "$target" \
  "docker login '$registry_host' -u '$REGISTRY_USERNAME' --password-stdin"
# systemd owns the process: SSH loss does not interrupt the switch or its rollback.
ssh "${ssh_args[@]}" -p 22 "$target" \
  "systemd-run --wait --collect --unit=rag-deploy-$GITHUB_RUN_NUMBER-$GITHUB_RUN_ATTEMPT --property=RuntimeMaxSec=1800 --property=TimeoutStopSec=240 --working-directory='$release_dir' /bin/bash deploy/production/run-release.sh '$GITHUB_SHA' '$GITHUB_RUN_NUMBER'"
