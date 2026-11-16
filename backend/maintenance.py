"""Explicit, rehearsed Hub-only upgrades and isolated, verified DB backups."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from scripts.data_safety import (DATABASE_NAME, SafetyError, audit_connection, create_backup,
    private_directory, restore_isolated, sync_directory, write_json)
from .app.core.note_files import file_lock
from .app.core.note_snapshot import snapshot, restore_snapshot
from .app.core.config import PROJECT_ROOT, Settings
from .app.core.database import BASELINE_REVISION, SCHEMA_REVISION, SUPPORTED_REVISIONS, HubError, connection, revision


def require_private(path: Path, *, directory=False):
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise HubError('UNSAFE_DATA_PATH', 503)
    if not path.exists() or (directory and not path.is_dir()) or path.stat().st_mode & 0o077:
        raise HubError('PRIVATE_PATH_REQUIRED', 503)


@contextmanager
def process_lock(settings: Settings, *, exclusive=False):
    require_private(settings.data_root, directory=True)
    directory = settings.data_root / 'config'
    directory.mkdir(mode=0o700, exist_ok=True)
    require_private(directory, directory=True)
    descriptor = os.open(directory / 'hub-maintenance.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(descriptor).st_mode & 0o077:
            raise HubError('PRIVATE_PATH_REQUIRED', 503)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HubError('HUB_RUNNING_STOP_FIRST', 409) from None
        yield
    finally:
        os.close(descriptor)


def validated_revision(settings: Settings):
    require_private(settings.data_root, directory=True)
    require_private(settings.database.parent, directory=True)
    require_private(settings.database)
    current = revision(settings.database)
    if current not in SUPPORTED_REVISIONS:
        raise HubError('DATABASE_VERSION_UNSUPPORTED', 503)
    return current


def _apply(path: Path, expected_audit: dict):
    with connection(path, write=True, require_current=False, enforce_foreign_keys=False) as con:
        raw = con.connection.driver_connection
        if audit_connection(raw) != expected_audit:
            raise HubError('DATABASE_CHANGED_RETRY_BACKUP', 409)
        objects_before = con.exec_driver_sql("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name").fetchall()
        config = Config(str(PROJECT_ROOT / 'backend' / 'alembic.ini'))
        config.attributes['connection'] = con
        command.upgrade(config, SCHEMA_REVISION)
        after = audit_connection(raw)
        objects_after = set(con.exec_driver_sql("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE type IN ('index','trigger') AND sql IS NOT NULL").fetchall())
        if any(row not in objects_after for row in objects_before):
            raise HubError('MIGRATION_SCHEMA_OBJECT_MISMATCH',503)
        for name, table in expected_audit['tables'].items():
            if name != 'alembic_version' and after['tables'].get(name) != table:
                raise HubError('MIGRATION_DATA_MISMATCH', 503)
        if con.exec_driver_sql('SELECT version_num FROM alembic_version').fetchall() != [(SCHEMA_REVISION,)]:
            raise HubError('MIGRATION_REVISION_MISMATCH', 503)


def upgrade(settings: Settings):
    # Shared API lifetime lock and exclusive maintenance lock prevent new writers.
    with process_lock(settings, exclusive=True), file_lock(settings.data_root):
        current = validated_revision(settings)
        if current == SCHEMA_REVISION:
            return {'status': 'up_to_date', 'revision': current}
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        folder = private_directory(settings.data_root / 'maintenance' / ('upgrade-' + stamp))
        backup = snapshot(settings.database, settings.data_root, folder / 'pre-upgrade')
        expected = backup['database']['audit']
        restore_snapshot(folder / 'pre-upgrade', folder / 'rehearsal')
        _apply(folder / 'rehearsal' / DATABASE_NAME, expected)
        # Explicit BEGIN IMMEDIATE makes SQLite DDL + Alembic revision transactional.
        # A late writer changes the audit, causing rollback before any real DDL.
        _apply(settings.database, expected)
        result = {'status': 'upgraded', 'revision': SCHEMA_REVISION, 'rehearsal': 'passed',
                  'backup_directory': str(folder / 'pre-upgrade')}
        write_json(folder / 'upgrade-result.json', result)
        sync_directory(folder)
        return result


def backup_hub(settings: Settings, output: Path):
    with process_lock(settings, exclusive=True), file_lock(settings.data_root):
        validated_revision(settings)
        snapshot(settings.database, settings.data_root, output)
        return {'status': 'verified', 'scope': 'hub_database_notes_vault_and_library', 'output': str(output)}


def restore_hub(settings: Settings, backup: Path, output: Path):
    # Always restore into a NEW isolated directory. Never replace an active path.
    if settings.database.resolve() == output.resolve() or settings.data_root.resolve() == output.resolve():
        raise HubError('RESTORE_REQUIRES_NEW_DIRECTORY', 409)
    result = restore_snapshot(backup, output)
    restored_revision = revision(output / DATABASE_NAME)
    if restored_revision not in SUPPORTED_REVISIONS:
        raise HubError('DATABASE_VERSION_UNSUPPORTED', 503)
    return {'status': 'verified', 'revision': restored_revision,
            'output': str(output), 'integrity_check': result['audit']['integrity_check']}
