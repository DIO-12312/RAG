#!/usr/bin/env sh
set -eu

python3 /app/scripts/search_guard/materials.py \
  --environment "${RAG_ENVIRONMENT:-development}" \
  --node-output /node-secrets \
  --client-output /client-secrets

# Elasticsearch runs as UID 1000. Keep its transport credentials inaccessible
# to the RAG runtime, and keep the bootstrap-only admin key root-readable only.
chown 1000:0 /node-secrets
chmod 0700 /node-secrets
chown 1000:0 /node-secrets/ca.pem /node-secrets/node.pem \
  /node-secrets/node-key.pem /node-secrets/rag_mvp_password
chmod 0644 /node-secrets/ca.pem /node-secrets/node.pem
chmod 0600 /node-secrets/node-key.pem /node-secrets/rag_mvp_password
chmod 0600 /node-secrets/admin-key.pem

# The application image runs as UID 100:GID 101. Only it can read its password.
chown 100:101 /client-secrets
chmod 0700 /client-secrets
chown 100:101 /client-secrets/rag_mvp_password
chmod 0644 /client-secrets/ca.pem
chmod 0600 /client-secrets/rag_mvp_password
