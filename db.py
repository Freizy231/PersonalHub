"""SQLite access and additive, transactional schema migration."""
import sqlite3
import os
from contextlib import closing
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA_VERSION = 2


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def get_connection(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def backup_database(source, destination):
    """Include committed WAL contents through SQLite's backup API."""
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(get_connection(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("数据库备份完整性检查失败")
    os.chmod(destination, 0o600)


def init_db(path=None):
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(get_connection(path)) as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("数据库版本比程序新，请使用匹配版本的程序")
        if version == SCHEMA_VERSION:
            return
        existing = con.execute("SELECT 1 FROM sqlite_master WHERE name='repos'").fetchone()
        if existing:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup_database(path, path.parent / "backups" / f"pre-v{SCHEMA_VERSION}-{stamp}.sqlite3")
        con.execute("PRAGMA journal_mode=WAL")
        try:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
                con.rollback()
                return
            statements = [
                """CREATE TABLE IF NOT EXISTS repos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT UNIQUE NOT NULL, description TEXT, html_url TEXT,
                    language TEXT, created_at TEXT, updated_at TEXT, topics TEXT,
                    category TEXT DEFAULT 'Other', is_archived INTEGER DEFAULT 0,
                    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id INTEGER NOT NULL REFERENCES repos(id),
                    stars INTEGER NOT NULL CHECK(stars>=0),
                    forks INTEGER NOT NULL CHECK(forks>=0),
                    open_issues INTEGER NOT NULL CHECK(open_issues>=0),
                    captured_at TEXT NOT NULL, UNIQUE(repo_id,captured_at))""",
                """CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
                    discovered INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0, message TEXT)""",
            ]
            for statement in statements:
                con.execute(statement)
            additions = {
                "repos": {"github_id": "INTEGER", "description_zh": "TEXT", "description_zh_source": "TEXT"},
                "snapshots": {"run_id": "INTEGER REFERENCES collection_runs(id)"},
                "collection_runs": {
                    "status": "TEXT NOT NULL DEFAULT 'legacy'",
                    "phase": "TEXT NOT NULL DEFAULT 'unknown'",
                    "progress_done": "INTEGER NOT NULL DEFAULT 0",
                    "progress_total": "INTEGER NOT NULL DEFAULT 0",
                    "current_item": "TEXT", "heartbeat_at": "TEXT",
                    "matched": "INTEGER NOT NULL DEFAULT 0",
                    "search_errors": "INTEGER NOT NULL DEFAULT 0",
                    "refresh_errors": "INTEGER NOT NULL DEFAULT 0",
                    "system_errors": "INTEGER NOT NULL DEFAULT 0",
                    "translation_errors": "INTEGER NOT NULL DEFAULT 0",
                },
            }
            for table, columns in additions.items():
                present = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                for name, declaration in columns.items():
                    if name not in present:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            for statement in [
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_repos_github_id ON repos(github_id) WHERE github_id IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS idx_snapshots_repo_time ON snapshots(repo_id,captured_at)",
                "CREATE INDEX IF NOT EXISTS idx_snapshots_repo_jd ON snapshots(repo_id,julianday(captured_at),id)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_run_repo ON snapshots(run_id,repo_id) WHERE run_id IS NOT NULL",
                """CREATE TABLE IF NOT EXISTS collection_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES collection_runs(id),
                    phase TEXT NOT NULL, item TEXT NOT NULL, message TEXT NOT NULL,
                    created_at TEXT NOT NULL)""",
                "CREATE INDEX IF NOT EXISTS idx_collection_errors_run ON collection_errors(run_id,id)",
                """CREATE TRIGGER IF NOT EXISTS validate_snapshot_insert BEFORE INSERT ON snapshots
                    WHEN NEW.stars<0 OR NEW.forks<0 OR NEW.open_issues<0
                         OR typeof(NEW.stars)!='integer' OR typeof(NEW.forks)!='integer'
                         OR typeof(NEW.open_issues)!='integer'
                         OR julianday(NEW.captured_at) IS NULL
                    BEGIN SELECT RAISE(ABORT,'invalid snapshot values'); END""",
                """CREATE TRIGGER IF NOT EXISTS validate_snapshot_update BEFORE UPDATE ON snapshots
                    WHEN NEW.stars<0 OR NEW.forks<0 OR NEW.open_issues<0
                         OR typeof(NEW.stars)!='integer' OR typeof(NEW.forks)!='integer'
                         OR typeof(NEW.open_issues)!='integer'
                         OR julianday(NEW.captured_at) IS NULL
                    BEGIN SELECT RAISE(ABORT,'invalid snapshot values'); END""",
            ]:
                con.execute(statement)
            if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("历史数据存在外键异常；迁移已回滚，请人工核对备份")
            if con.execute("""SELECT 1 FROM snapshots WHERE stars<0 OR forks<0 OR open_issues<0
                OR typeof(stars)!='integer' OR typeof(forks)!='integer' OR typeof(open_issues)!='integer'
                OR julianday(captured_at) IS NULL LIMIT 1""").fetchone():
                raise RuntimeError("历史快照格式异常；迁移已回滚，请人工核对备份")
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            con.commit()
        except BaseException:
            con.rollback()
            raise
