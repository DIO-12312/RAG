"""Fail when Compose logs contain the configured embedding API key."""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import sys

from rag_mvp.config import load_settings


def main() -> int:
    configured = load_settings().embedding_model_api_key
    if configured is None or not configured.get_secret_value():
        print("embedding secret is not configured", file=sys.stderr)
        return 2
    secret = configured.get_secret_value()
    if secret in sys.stdin.read():
        print("secret leak detected")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
