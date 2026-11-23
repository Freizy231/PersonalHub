"""Alembic 迁移环境。

关键约定（AGENT.md §9.4）：
- 数据库路径来自应用配置（PH_DATA_DIR / 默认 XDG 目录），与运行时一致。
- 引擎直接复用 app.storage.database.build_engine，保证迁移与运行时使用
  完全相同的 PRAGMA（foreign_keys / WAL），演练结果才有意义。
- 不使用 Base.metadata.create_all() 建表，生产结构只由迁移决定。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# 保证 `alembic` 命令与 `python -m alembic` 都能导入 app 包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.models import Base  # noqa: E402
from app.storage.database import build_engine  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False 必须显式指定：fileConfig 默认会禁用所有
    # 已存在的 logger，导致应用日志在迁移之后整体静默（生产环境同样受影响）。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _db_path() -> Path:
    # 允许命令行用 -x db_path=... 覆盖，便于在测试副本上演练迁移。
    override = context.get_x_argument(as_dictionary=True).get("db_path")
    return Path(override) if override else get_settings().db_path


def run_migrations_offline() -> None:
    context.configure(
        url=f"sqlite:///{_db_path()}",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 的批处理模式让 ALTER TABLE 类变更成为可能。
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = build_engine(_db_path())

    with engine.connect() as connection:
        # 不要在这里执行任何 SQL（例如 PRAGMA）：SQLAlchemy 2.0 会因此隐式开启
        # 事务，Alembic 的 begin_transaction() 变为空操作，最终版本号不提交。
        # PRAGMA 已由 build_engine 的连接事件统一设置。
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
