#!/usr/bin/env sh
set -eu

exec python3 /app/scripts/search_guard/bootstrap.py \
  --host elasticsearch \
  --port 9200 \
  --node-dir /node-secrets \
  --client-dir /client-secrets \
  --config-dir /app/docker/search-guard/sgconfig
