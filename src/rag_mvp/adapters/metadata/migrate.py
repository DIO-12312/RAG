"""Repository-owned Alembic command line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from rag_mvp.config import Settings


def _alembic_config(dsn: str, migrations_root: Path | None = None) -> Config:
    root = migrations_root or Path(__file__).resolve().parents[4]
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    return config


def run_migrations(
    dsn: str,
    action: str,
    revision: str = "head",
    migrations_root: Path | None = None,
) -> None:
    """Run one explicit Alembic action against the supplied database."""

    config = _alembic_config(dsn, migrations_root)
    if action == "upgrade":
        command.upgrade(config, revision)
    elif action == "downgrade":
        command.downgrade(config, revision)
    elif action == "current":
        command.current(config, verbose=True)
    else:
        raise ValueError(f"unsupported migration action: {action}")


def main(argv: Sequence[str] | None = None) -> None:
    """Run migrations with the same Settings source used by production roles."""

    parser = argparse.ArgumentParser(prog="rag-migrate")
    parser.add_argument("action", choices=("upgrade", "downgrade", "current"))
    parser.add_argument("revision", nargs="?", default="head")
    arguments = parser.parse_args(argv)
    settings = Settings()
    run_migrations(
        settings.mysql_dsn,
        arguments.action,
        arguments.revision,
        settings.migrations_root,
    )


if __name__ == "__main__":  # pragma: no cover - console script boundary
    main()
