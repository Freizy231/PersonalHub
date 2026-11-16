"""Read-only SQLite audit, verified backup, and isolated restore (stdlib only).

Never imports the legacy application, configuration, or migration modules.
Every output directory must be new. This tool cannot replace an active database.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import time
from typing import Any

FORMAT_VERSION = 1
SUPPORTED_DATABASE_VERSION = 2
DATABASE_NAME = "database.sqlite3"


class SafetyError(Exception):
    """Only controlled messages may be returned to CLI users."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def existing_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise SafetyError("SOURCE_MISSING", "输入必须是已存在的普通文件，不能是符号链接。")
    if not path.stat().st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
        raise SafetyError("SOURCE_UNREADABLE", "输入文件不可读。")
    return path.resolve(strict=True)


def open_readonly(path: Path) -> sqlite3.Connection:
    path = existing_file(path)
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    return con


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def encode_value(value: Any) -> bytes:
    """Preserve NULL, numeric types, binary values, and field boundaries."""
    if value is None:
        payload = b"n"
    elif isinstance(value, int):
        payload = b"i" + str(value).encode("ascii")
    elif isinstance(value, float):
        payload = b"f" + struct.pack("!d", value)
    elif isinstance(value, bytes):
        payload = b"b" + value
    else:
        payload = b"s" + value.encode("utf-8", errors="surrogatepass")
    return len(payload).to_bytes(8, "big") + payload


def audit_connection(con: sqlite3.Connection) -> dict:
    """Hash complete rows without putting any row contents in the report.

    Row digests are sorted so tables without primary keys and duplicate rows
    compare correctly. Hashes are fingerprints, not encrypted copies of data.
    """
    integrity = con.execute("PRAGMA integrity_check").fetchall()
    foreign_key_errors = con.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != [("ok",)] or foreign_key_errors:
        raise SafetyError("DATABASE_INVALID", "数据库完整性或外键检查失败，未生成有效备份。")
    schema = con.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    tables = {}
    # sqlite_sequence is included: losing its AUTOINCREMENT state can reuse IDs.
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') ORDER BY name"
    )]
    for name in names:
        quoted = quote_identifier(name)
        columns = [r[1] for r in con.execute(f"PRAGMA table_info({quoted})")]
        if not columns:
            raise SafetyError("SCHEMA_UNSUPPORTED", "表结构无法安全审计。")
        selection = ",".join(quote_identifier(c) for c in columns)
        digests = []
        for row in con.execute(f"SELECT {selection} FROM {quoted}"):
            digests.append(hashlib.sha256(b"".join(encode_value(v) for v in row)).digest())
        digest = hashlib.sha256()
        for item in sorted(digests):
            digest.update(item)
        tables[name] = {"columns": columns, "row_count": len(digests), "rows_sha256": digest.hexdigest()}
    schema_hash = hashlib.sha256(
        json.dumps(schema, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "user_version": con.execute("PRAGMA user_version").fetchone()[0],
        "application_id": con.execute("PRAGMA application_id").fetchone()[0],
        "integrity_check": "ok", "foreign_key_errors": 0,
        "schema_sha256": schema_hash, "tables": tables,
    }


def audit_database(source: Path) -> dict:
    with closing(open_readonly(source)) as con:
        con.execute("BEGIN")  # One consistent read snapshot for all fields.
        return audit_connection(con)


def private_directory(path: Path) -> Path:
    """Reserve a new leaf atomically. Never alter permissions of existing dirs."""
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        raise SafetyError("OUTPUT_EXISTS", "输出目录已存在；请使用新目录，不会覆盖既有文件。") from None
    return path.resolve(strict=True)


def write_json(path: Path, value: dict) -> None:
    with open(path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_database(con: sqlite3.Connection, destination: Path, timeout: float = 30) -> None:
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    deadline = time.monotonic() + timeout

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise SafetyError("BACKUP_TIMEOUT", "备份超时，输出目录保留为未完成状态。")

    with closing(sqlite3.connect(destination)) as target:
        con.backup(target, pages=256, progress=progress, sleep=0.05)
        # A self-contained artifact should not require WAL sidecars on restore.
        target.execute("PRAGMA journal_mode=DELETE")
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())


def matching_report(expected: dict, actual: dict) -> None:
    if expected != actual:
        raise SafetyError("CONTENT_MISMATCH", "结构、版本或逐字段数据指纹不一致；未通过验证。")


def create_backup(source: Path, output: Path) -> dict:
    source = existing_file(source)
    # Holding the read transaction pins the same snapshot for audit and backup,
    # while WAL writers may continue committing newer observations.
    with closing(open_readonly(source)) as con:
        con.execute("BEGIN")
        report = audit_connection(con)
        if report["user_version"] > SUPPORTED_DATABASE_VERSION:
            raise SafetyError("VERSION_UNSUPPORTED", "数据库版本较新，请使用匹配版本的维护工具。")
        directory = private_directory(output)
        write_json(directory / "INCOMPLETE.json", {"started_at": now(), "state": "incomplete"})
        database = directory / DATABASE_NAME
        copy_database(con, database)
        matching_report(report, audit_database(database))
    manifest = {
        "format": "personal-hub-sqlite-backup", "format_version": FORMAT_VERSION,
        "created_at": now(), "state": "verified", "contains_private_data": True,
        "coverage": "sqlite_database_only", "excluded": ["secrets", "external_files", "markdown", "pdf"],
        "database": {"filename": DATABASE_NAME, "size_bytes": database.stat().st_size,
                     "sha256": file_hash(database), "audit": report},
    }
    # Written last: missing manifest always means the artifact is unusable.
    write_json(directory / "manifest.json", manifest)
    (directory / "INCOMPLETE.json").unlink()
    sync_directory(directory)
    return manifest


def verified_backup(directory: Path) -> tuple[Path, dict]:
    if directory.is_symlink() or (directory / "INCOMPLETE.json").exists():
        raise SafetyError("BACKUP_INCOMPLETE", "备份未完成或目录不可信。")
    path = existing_file(directory / "manifest.json")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise SafetyError("MANIFEST_INVALID", "备份清单过大。")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (manifest["format"] != "personal-hub-sqlite-backup" or
                manifest["format_version"] != FORMAT_VERSION or manifest["state"] != "verified"):
            raise ValueError
        metadata = manifest["database"]
        if metadata["filename"] != DATABASE_NAME:
            raise ValueError
        if metadata["audit"]["user_version"] > SUPPORTED_DATABASE_VERSION:
            raise SafetyError("VERSION_UNSUPPORTED", "备份数据库版本较新，不能用当前工具恢复。")
        database = existing_file(directory / DATABASE_NAME)
        if database.stat().st_size != metadata["size_bytes"] or file_hash(database) != metadata["sha256"]:
            raise SafetyError("CHECKSUM_MISMATCH", "备份文件校验失败；不会继续恢复。")
        matching_report(metadata["audit"], audit_database(database))
    except (KeyError, ValueError, TypeError):
        raise SafetyError("MANIFEST_INVALID", "备份清单格式无效或不支持。") from None
    return database, manifest


def restore_isolated(backup: Path, output: Path) -> dict:
    database, manifest = verified_backup(backup)
    directory = private_directory(output)
    write_json(directory / "INCOMPLETE.json", {"started_at": now(), "state": "incomplete"})
    restored = directory / DATABASE_NAME
    with closing(open_readonly(database)) as con:
        con.execute("BEGIN")
        matching_report(manifest["database"]["audit"], audit_connection(con))
        copy_database(con, restored)
    report = audit_database(restored)
    matching_report(manifest["database"]["audit"], report)
    result = {"format_version": FORMAT_VERSION, "state": "verified", "restored_at": now(),
              "mode": "isolated_only", "audit": report}
    write_json(directory / "restore-verification.json", result)
    (directory / "INCOMPLETE.json").unlink()
    sync_directory(directory)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="只读审计、独立备份和隔离恢复；不支持覆盖活动数据库。")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="只读审计；stdout为不含行内容的JSON")
    audit.add_argument("--source", type=Path, required=True)
    backup = sub.add_parser("backup", help="创建新的、已验证的数据库备份目录")
    backup.add_argument("--source", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify", help="校验备份清单、文件hash和逐字段指纹")
    verify.add_argument("--backup", type=Path, required=True)
    restore = sub.add_parser("restore-check", help="仅恢复到新的隔离目录")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            result = audit_database(args.source)
        elif args.command == "backup":
            result = create_backup(args.source, args.output)
        elif args.command == "verify":
            _, manifest = verified_backup(args.backup)
            result = {"state": "verified", "audit": manifest["database"]["audit"]}
        else:
            result = restore_isolated(args.backup, args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except SafetyError as error:
        result = {"code": error.code, "message": str(error)}
    except (OSError, sqlite3.Error):
        result = {"code": "IO_ERROR", "message": "文件或数据库操作失败，请检查权限、磁盘和输入格式。"}
    except KeyboardInterrupt:
        result = {"code": "INTERRUPTED", "message": "操作已中断；既有输入未被覆盖，未完成目录保留供核查。"}
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
