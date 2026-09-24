"""异步 SQLAlchemy 引擎与会话工厂；统一数据库隔离级别与连接生命周期。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


# 创建使用 READ COMMITTED 隔离级别和连接预检的异步 MySQL 引擎。
def create_mysql_engine(dsn: str) -> AsyncEngine:
    """Create a pooled MySQL engine using the repository isolation contract."""

    if not dsn.startswith("mysql+asyncmy://"):
        raise ValueError("MySQL DSN must use the mysql+asyncmy driver")
    return create_async_engine(
        dsn,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
    )


# 创建提交后仍可读取已加载 ORM 对象的短生命周期异步会话工厂。
def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create short-lived sessions that retain loaded values after commit."""

    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
