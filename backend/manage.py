"""Explicit local maintenance entry point; serving never creates a database."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from .app.core.config import PROJECT_ROOT, Settings
from .app.main import create_app
from .app.core.database import BASELINE_REVISION, HubError, revision
from .maintenance import backup_hub, restore_hub, upgrade
from scripts.data_safety import SafetyError


def initialize_empty(data_root: Path) -> Path:
    settings = Settings(data_root=data_root.absolute(), legacy_database=PROJECT_ROOT / "data" / "intel.db")
    if settings.database.exists() or settings.database.is_symlink():
        raise FileExistsError("DATABASE_EXISTS")
    settings.data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if settings.data_root.stat().st_mode & 0o077:
        raise PermissionError("DATA_ROOT_MUST_BE_PRIVATE")
    directory = settings.database.parent
    directory.mkdir(mode=0o700, exist_ok=True)
    temporary = directory / f".initialize-{uuid.uuid4()}.sqlite3"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    engine = create_engine("sqlite+pysqlite:///" + str(temporary), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
            config.attributes["connection"] = connection
            # Phase 0 is an empty baseline, not all future business tables.
            command.upgrade(config, "0000_foundation")
        if revision(temporary) != BASELINE_REVISION:
            raise RuntimeError("INITIALIZATION_VERIFICATION_FAILED")
        # Hard-link creation is atomic and refuses an existing target, unlike replace.
        os.link(temporary, settings.database)
    finally:
        engine.dispose()
        temporary.unlink(missing_ok=True)  # Only this invocation's new temp file.
    return settings.database


def _open_when_ready(settings: Settings) -> None:
    """Open the one-time launch URL in the default browser once the server answers.

    The URL carries the bootstrap token in its fragment. It is read from the
    0600 launch file and handed straight to the browser: it is never printed
    or logged. The token is single-use, so a second tab cannot reuse it.
    """
    import threading
    import time
    import urllib.request
    import webbrowser

    launch = settings.data_root / "config" / f"launch-{settings.port}.url"
    health = f"http://127.0.0.1:{settings.port}/api/v1/health"

    def wait_and_open() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(health, timeout=1) as response:  # noqa: S310 - fixed loopback URL
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.3)
        else:
            print("服务未在 30 秒内就绪，未自动打开浏览器；请手动打开 launch 文件中的地址。", flush=True)
            return
        try:
            url = launch.read_text(encoding="utf-8").strip()
        except OSError:
            print("未找到入口文件，未自动打开浏览器。", flush=True)
            return
        if not url.startswith(f"http://127.0.0.1:{settings.port}/"):
            return
        if not webbrowser.open(url):
            print("无法启动默认浏览器；请手动打开 launch 文件中的地址。", flush=True)

    threading.Thread(target=wait_and_open, name="open-browser", daemon=True).start()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Personal Hub本机维护；不会升级或覆盖旧情报库。")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="显式创建新的空hub.db；目标存在时拒绝")
    init.add_argument("--data-root", required=True, type=Path)
    serve = sub.add_parser("serve", help="仅绑定127.0.0.1；不自动创建或迁移数据库")
    serve.add_argument("--data-root", type=Path)
    serve.add_argument("--port", type=int)
    serve.add_argument("--open", action="store_true", dest="open_browser",
                       help="服务就绪后用默认浏览器打开本机入口；凭据不会输出到终端或日志")
    check = sub.add_parser("check", help="只读显示新数据库版本状态，不读取密钥")
    check.add_argument("--data-root", type=Path)
    migrate = sub.add_parser("upgrade", help="新hub.db备份、副本演练后显式升级；先停止新服务")
    migrate.add_argument("--data-root", required=True, type=Path)
    backup = sub.add_parser("backup", help="新hub.db与Markdown文件一致性备份；输出目录必须全新，先停止服务")
    backup.add_argument("--data-root", required=True, type=Path)
    backup.add_argument("--output", required=True, type=Path)
    restore = sub.add_parser("restore-check", help="仅恢复新hub备份到全新隔离目录，不切换活动路径")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--output", required=True, type=Path)
    for name in ['notes-check','notes-reconcile']:
        note_command=sub.add_parser(name,help='停服诊断或标记未提交笔记文件；不删除文件、不覆盖正文')
        note_command.add_argument('--data-root',required=True,type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize_empty(args.data_root)
            print(json.dumps({"status": "initialized", "revision": "0000_foundation"}))
        elif args.command in {'notes-check','notes-reconcile'}:
            from .maintenance import process_lock,validated_revision
            from .app.modules.library.notes.service import NoteService
            settings=Settings(data_root=args.data_root.absolute(),legacy_database=PROJECT_ROOT/'data'/'intel.db')
            with process_lock(settings,exclusive=True):
                validated_revision(settings)
                result=NoteService(settings.database,settings.data_root).diagnose(args.command=='notes-reconcile')
            print(json.dumps(result))
        elif args.command in {"upgrade", "backup", "restore-check"}:
            default = Settings.from_environment()
            settings = Settings(data_root=(getattr(args, "data_root", None) or default.data_root).absolute(),
                                legacy_database=default.legacy_database)
            if args.command == "upgrade":
                result = upgrade(settings)
            elif args.command == "backup":
                result = backup_hub(settings, args.output)
            else:
                result = restore_hub(settings, args.backup, args.output)
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "check":
            from .app.main import database_status
            default = Settings.from_environment()
            state = database_status((args.data_root.absolute() if args.data_root else default.data_root) / "db" / "hub.db")
            print(json.dumps({"database": state}))
            return 0 if state == "ready" else 1
        else:
            import uvicorn
            default = Settings.from_environment()
            settings = Settings(data_root=(args.data_root or default.data_root).absolute(),
                                legacy_database=default.legacy_database,
                                reminders_enabled=default.reminders_enabled,
                                port=args.port if args.port is not None else default.port)
            print("Personal Hub: http://127.0.0.1:" + str(settings.port), flush=True)
            print("首次进入请打开数据目录config/下的launch-<端口>.url；凭据不输出到日志。", flush=True)
            if args.open_browser:
                _open_when_ready(settings)
            uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, access_log=False)
        return 0
    except (HubError, SafetyError) as error:
        print(json.dumps({"code": error.code, "message": "维护未完成；检查版本/权限，升级与备份前须停止新Hub服务。"}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, sqlite3.Error, SQLAlchemyError):
        print(json.dumps({"code": "MAINTENANCE_FAILED", "message": "检查目录权限、现有文件与数据库版本；没有覆盖既有数据库。"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
