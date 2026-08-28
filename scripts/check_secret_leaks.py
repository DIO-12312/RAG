"""Fail when Compose logs contain the configured embedding API key.

从标准输入读取日志，只报告泄漏结果，绝不回显密钥本身。
"""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import sys

from rag_mvp.config import load_settings


def main() -> int:
    """检查 Compose 日志中是否出现当前配置的模型或 Elasticsearch Secret。"""

    settings = load_settings()
    secrets: list[str] = []
    embedding = settings.embedding_model_api_key
    if embedding is not None and embedding.get_secret_value():
        secrets.append(embedding.get_secret_value())
    try:
        elasticsearch = settings.require_elasticsearch_profile()
    except ValueError:
        elasticsearch = None
    if elasticsearch is not None:
        secrets.append(elasticsearch.password.get_secret_value())
    if not secrets:
        # 未配置任何密钥时无法判定泄漏，使用独立退出码提示调用方修正测试环境。
        print("no secret is configured", file=sys.stderr)
        return 2
    # 只在内存中比较；错误输出不能包含 secret，以免检查器反而造成二次泄漏。
    # 一次读取管道日志并做精确子串匹配；检查结果中不暴露匹配位置或密钥内容。
    logs = sys.stdin.read()
    if any(secret in logs for secret in secrets):
        print("secret leak detected")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
