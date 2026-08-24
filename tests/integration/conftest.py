"""Shared configuration for real infrastructure integration tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def mysql_dsn() -> str:
    """Use the host-published MySQL port unless Docker injects an override."""

    return os.getenv(
        "RAG_TEST_MYSQL_DSN",
        "mysql+asyncmy://rag:rag@127.0.0.1:3306/rag",
    )
