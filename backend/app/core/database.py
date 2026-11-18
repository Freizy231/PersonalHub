"""Connections to existing Hub files only; never implicitly create a database."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, create_engine
from sqlalchemy.pool import NullPool

SCHEMA_REVISION = '0018_ai_workspace'
SUPPORTED_REVISIONS = {'0000_foundation', '0001_tasks', '0002_events_reminders', '0003_notes', '0004_dashboard_preferences', '0005_ordinary_tags', '0006_knowledge_foundation', '0007_phase2', '0008_projects', '0009_project_tools', '0010_diet', '0011_finance_exercise', '0012_intelligence_assistant', '0013_intel_ordering', '0014_epub_documents', '0015_meal_dishes', '0016_intel_schedule', '0017_stalled_reviews', SCHEMA_REVISION}
BASELINE_REVISION = '0000_foundation'
metadata = MetaData()


class HubError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def check_path(path: Path):
    if (path.parent.parent/'HUB_INCOMPLETE.json').exists():
        raise HubError('DATABASE_UNAVAILABLE',503)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise HubError('DATABASE_UNAVAILABLE', 503)
    if not path.is_file():
        raise HubError('DATABASE_NOT_INITIALIZED', 503)


def revision(path: Path) -> str | None:
    check_path(path)
    with connection(path, require_current=False) as con:
        rows = con.exec_driver_sql('SELECT version_num FROM alembic_version').fetchall()
        return rows[0][0] if len(rows) == 1 else None


@contextmanager
def connection(path: Path, *, write=False, require_current=True, enforce_foreign_keys=True):
    if not enforce_foreign_keys and (not write or require_current):
        raise ValueError('FK bypass is restricted to explicit maintenance upgrades')
    check_path(path)
    def connect():
        db = sqlite3.connect(path.absolute().as_uri() + ('?mode=rw' if write else '?mode=ro'),
                             uri=True, timeout=2)
        db.execute('PRAGMA foreign_keys=' + ('ON' if enforce_foreign_keys else 'OFF'))
        db.execute('PRAGMA trusted_schema=OFF')
        if not write:
            db.execute('PRAGMA query_only=ON')
        instant = datetime.now(timezone.utc)
        db.create_function('hub_today', 1, lambda zone: instant.astimezone(ZoneInfo(zone)).date().isoformat())
        return db
    # hide_parameters keeps note titles, amounts and other values out of any SQLAlchemy error text.
    engine = create_engine('sqlite+pysqlite://', creator=connect, poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as con:
            con.exec_driver_sql('BEGIN IMMEDIATE' if write else 'BEGIN')
            if require_current:
                rows = con.exec_driver_sql('SELECT version_num FROM alembic_version').fetchall()
                if rows != [(SCHEMA_REVISION,)]:
                    raise HubError('DATABASE_UPGRADE_REQUIRED', 503)
            yield con
            if write:
                con.commit()
    finally:
        engine.dispose()
