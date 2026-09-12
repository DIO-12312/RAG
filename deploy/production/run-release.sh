#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" =~ ^[0-9a-f]{40}$ ]]
[[ "${2:-}" =~ ^[0-9]+$ ]]
# Never let a mutable checkout overwrite /data/RAG or select the deployed source.
export PATH=/usr/local/bin:/usr/bin:/bin
make production-deploy RELEASE_SHA="$1" RELEASE_SEQUENCE="$2"
