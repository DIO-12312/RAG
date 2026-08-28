#!/usr/bin/env sh
set -eu

exec python3 /app/scripts/search_guard/materials.py \
  --environment "${RAG_ENVIRONMENT:-development}" \
  --node-output /node-secrets \
  --client-output /client-secrets
