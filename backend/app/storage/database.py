"""数据库连接与会话管理。

设计要点（AGENT.md §4.13、§9.4）：
- SQLite 只由本进程管理，Worker 后续通过同一数据服务访问，不做多进程并发写。
- 启用 WAL（仅本地文件系统）与 foreign_keys，保证外键约束真实生效。
- 不得使用 drop_all()；Schema 变化一律走 Alembic 迁移。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _configure_sqlite(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    """每个连接建立时设置 PRAGMA。

    SQLite 默认关闭外键约束，必须显式开启，否则 ON DELETE CASCADE 不生效。
    journal_mode=WAL 只在文件型数据库上有意义，对 :memory: 无害。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        # 忙等 5 秒后再报 SQLITE_BUSY，避免瞬时锁竞争直接失败。
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def build_engine(db_path: Path) -> Engine:
    """创建面向单个 SQLite 文件的引擎。

    check_same_thread=False：FastAPI 的同步依赖可能在任意线程执行；
    同一时刻只有一个 Session 使用该连接，作用域由请求控制。

    注意：此函数会创建父目录，但**不会**创建数据表。表结构只由 Alembic 迁移建立。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        # 避免参数值被原样打印进错误输出（可能含用户内容）。
        hide_parameters=True,
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """autoflush=False：避免隐式 flush 造成难以调试的顺序问题。
    expire_on_commit=False：提交后仍可读取对象属性，便于构造响应。
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
