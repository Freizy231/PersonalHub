from contextlib import asynccontextmanager, closing
import logging
import logging.handlers
import asyncio
import os
from pathlib import Path
import secrets
import time
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from .core.config import Settings
from .core.security import COOKIE_NAME, SESSION_ABSOLUTE_SECONDS, LocalAuth, Session, require_session

from .core.database import BASELINE_REVISION, SCHEMA_REVISION, SUPPORTED_REVISIONS, HubError
from .modules.life.tasks.router import router as task_router

from .modules.life.events.router import router as event_router
from .modules.life.reminders.router import router as reminder_router
from .modules.life.reminders.worker import ReminderWorker

from .modules.library.notes.router import router as note_router

from .modules.library.notes.vault import VaultNoteStore
from .modules.library.notes.vault_router import router as vault_router

from .modules.dashboard.router import router as dashboard_router

from .modules.tags.router import router as tag_router
from .modules.knowledge.router import router as knowledge_router
from .modules.knowledge.export import router as export_router
from .modules.knowledge.worker import SearchWorker
from .modules.library.documents.router import router as document_router
from .modules.library.documents.reader import router as reader_router
from .modules.library.catalog import router as catalog_router
from .modules.learning.router import router as learning_router
from .modules.life.diet.router import router as diet_router
from .modules.projects.router import router as project_router
from .modules.projects.tools import router as project_tools_router
from .modules.library.documents.worker import ExtractionWorker

from .modules.life.finance.router import router as finance_router
from .modules.life.exercise.router import router as exercise_router

from .modules.intelligence.router import router as intelligence_router
from .modules.intelligence.service import IntelligenceService
from .modules.assistant.router import router as assistant_router
from .modules.assistant.service import AssistantService

VERSION = "0.14.2"


def database_status(path: Path) -> str:
    if (path.parent.parent / "HUB_INCOMPLETE.json").exists():
        return "unavailable"
    if not path.is_file():
        return "not_initialized"
    if path.is_symlink():
        return "unavailable"
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            con.execute("PRAGMA query_only=ON")
            rows = con.execute("SELECT version_num FROM alembic_version").fetchall()
            if rows == [(SCHEMA_REVISION,)]:
                return "ready"
            return "upgrade_required" if len(rows) == 1 and rows[0][0] in SUPPORTED_REVISIONS else "version_mismatch"
    except sqlite3.Error:
        return "unavailable"


class PrivateRotatingHandler(logging.handlers.RotatingFileHandler):
    """Rotation re-creates the file; without this it would take its permissions from the umask."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)


def configure_logging(settings: Settings) -> None:
    """Persist only error-level events so the request_id returned to the client is actionable.
    The log lives under the private data root; it never records payloads or credentials."""
    logger = logging.getLogger("personal_hub")
    if any(getattr(handler, "_hub_file", False) for handler in logger.handlers):
        return
    directory = settings.data_root / "logs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "hub.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    if path.stat().st_mode & 0o077:
        raise PermissionError("LOG_FILE_MUST_BE_PRIVATE")
    handler = PrivateRotatingHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler._hub_file = True
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)


def write_launch_file(settings: Settings, token: str) -> Path:
    settings.data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if settings.data_root.stat().st_mode & 0o077:
        raise PermissionError("DATA_ROOT_MUST_BE_PRIVATE")
    directory = settings.data_root / "config"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"launch-{settings.port}.url"
    # Fragment is not sent in HTTP requests or access logs.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(f"http://127.0.0.1:{settings.port}/#bootstrap={token}\n")
    return path


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    auth = LocalAuth()

    async def expire_vault():
        while True:
            await asyncio.sleep(1)
            if app.state.vault.owner is not None:
                await asyncio.to_thread(app.state.vault.expire, auth.sessions)

    @asynccontextmanager
    async def lifespan(app):
        from backend.maintenance import process_lock
        settings.data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        configure_logging(settings)
        with process_lock(settings):
            if database_status(settings.database)=='ready':
                await asyncio.to_thread(IntelligenceService(settings).recover)
                await asyncio.to_thread(app.state.assistant.recover)
            app.state.launch_file = write_launch_file(settings, auth.bootstrap)
            await app.state.reminder_worker.start()
            await app.state.extraction_worker.start()
            await app.state.search_worker.start()
            await app.state.intel_worker.start()
            expiry = asyncio.create_task(expire_vault())
            try:
                yield
            finally:
                expiry.cancel()
                try: await expiry
                except asyncio.CancelledError: pass
                await asyncio.to_thread(app.state.vault.lock)
                await app.state.assistant.stop()
                await app.state.reminder_worker.stop()
                await app.state.extraction_worker.stop()
                await app.state.search_worker.stop()
                await app.state.intel_worker.stop()
                auth.sessions.clear()
                auth.bootstrap = ""

    app = FastAPI(title="Personal Hub", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.auth = auth
    app.state.vault = VaultNoteStore(settings.data_root)
    app.include_router(vault_router)
    app.include_router(dashboard_router)
    app.include_router(tag_router)
    app.include_router(knowledge_router)
    app.include_router(export_router)
    app.include_router(document_router)
    app.include_router(catalog_router)
    app.include_router(reader_router)
    app.include_router(learning_router)
    app.include_router(project_router)
    app.include_router(project_tools_router)
    app.include_router(diet_router)
    app.include_router(intelligence_router)
    app.include_router(assistant_router)
    app.state.assistant = AssistantService(settings)
    app.state.assistant.owner_valid = lambda owner:any(s.csrf_token==owner and s.expires_at>time.monotonic() for s in auth.sessions.values())
    app.include_router(finance_router)
    app.include_router(exercise_router)
    app.state.extraction_worker = ExtractionWorker(settings)
    app.state.search_worker = SearchWorker(settings)
    app.state.reminder_worker = ReminderWorker(settings)
    from .modules.intelligence.worker import ScheduleWorker
    app.state.intel_worker = ScheduleWorker(settings)
    app.include_router(note_router)
    app.include_router(task_router)
    app.include_router(event_router)
    app.include_router(reminder_router)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def error_response(code: str, status: int, message: str | None = None, request_id: str | None = None):
        return JSONResponse({"code": code, "message": message or code, "details": {},
                             "request_id": request_id or secrets.token_hex(8)}, status_code=status)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in settings.origins:
            return error_response("ORIGIN_DENIED", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if origin not in settings.origins:
                return error_response("ORIGIN_REQUIRED", 403)
            if request.url.path != "/api/v1/session/exchange":
                try:
                    session = auth.require(request)
                except HTTPException:
                    return error_response("SESSION_REQUIRED", 401)
                csrf = request.headers.get("x-csrf-token", "")
                if not secrets.compare_digest(csrf.encode(), session.csrf_token.encode()):
                    return error_response("CSRF_INVALID", 403)
        private_payload = request.url.path.startswith("/api/v1/vault")
        dashboard_payload = request.url.path == "/api/v1/dashboard/preferences"
        tag_payload = request.url.path.startswith("/api/v1/tags/") or (request.url.path.startswith("/api/v1/resources/") and request.url.path.endswith("/tags"))
        if request.url.path!='/api/v1/documents/import' and request.method in {"POST", "PUT", "PATCH"}:
            chunks = []; size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > (1024 * 1024 if private_payload else 65536 if dashboard_payload or tag_payload else 1024*1024):
                    return error_response("VAULT_REQUEST_TOO_LARGE" if private_payload else "TAG_REQUEST_TOO_LARGE" if tag_payload else "DASHBOARD_REQUEST_TOO_LARGE" if dashboard_payload else "REQUEST_TOO_LARGE", 413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def controlled_error(_request, error):
        return error_response(str(error.detail), error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        # Pydantic errors can include the original input: never return it.
        return error_response("VALIDATION_ERROR", 422, "输入格式不正确。")

    @app.exception_handler(HubError)
    async def hub_error(_request, error):
        return error_response(error.code, error.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, error):
        # The request id is logged with the same value the client receives.
        request_id = secrets.token_hex(8)
        logging.getLogger("personal_hub").error(
            "database_failed request_id=%s type=%s", request_id, type(error).__name__)
        return error_response("DATABASE_UNAVAILABLE", 503,
                              "资料暂不可用，请稍后重试；没有覆盖现有内容。", request_id)

    @app.exception_handler(Exception)
    async def unexpected_error(_request, error):
        request_id = secrets.token_hex(8)
        logging.getLogger("personal_hub").error(
            "request_failed request_id=%s type=%s", request_id, type(error).__name__)
        return error_response("INTERNAL_ERROR", 500, "操作失败，请查看本地错误编号。", request_id)

    class Exchange(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: str = Field(min_length=20, max_length=256)

    @app.get("/api/v1/health")
    async def health():
        return {"app": "Personal Hub", "version": VERSION, "status": "ok"}

    @app.post("/api/v1/session/exchange")
    async def exchange(payload: Exchange, response: Response):
        cookie, session = auth.exchange(payload.token)
        # The cookie is only a handle: the server decides validity (8h sliding, 7d absolute),
        # so the browser must not drop it earlier than the server would.
        response.set_cookie(COOKIE_NAME, cookie, httponly=True, samesite="strict",
                            max_age=SESSION_ABSOLUTE_SECONDS, path="/api")
        return {"csrf_token": session.csrf_token}

    @app.get("/api/v1/session")
    async def session_state(session: Session = Depends(require_session)):
        return {"csrf_token": session.csrf_token}

    @app.post("/api/v1/session/logout")
    async def logout(request: Request, response: Response, _session=Depends(require_session)):
        await asyncio.to_thread(app.state.vault.lock, _session.csrf_token)
        await app.state.assistant.revoke(_session.csrf_token)
        auth.sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
        response.delete_cookie(COOKIE_NAME, path="/api")
        return {"status": "signed_out"}

    @app.get("/api/v1/system", dependencies=[Depends(require_session)])
    async def system_state():
        return {"version": VERSION, "phase": 4, "database": database_status(settings.database),
                "ai": "optional_api", "legacy_intelligence": "preserved", "data_mode": "local",
                "private_vault": "encrypted", "backup_scope": "hub_database_notes_vault_and_library", "documents":"available", "learning":"available", "search":app.state.search_worker.state, "pdf_worker":app.state.extraction_worker.state, "notes": "available", "tasks": "available", "events": "available", "reminders": app.state.reminder_worker.state,
                "intel_schedule": app.state.intel_worker.state}

    if settings.frontend_dist.is_dir():
        assets = settings.frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}")
        async def frontend(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "NOT_FOUND")
            return FileResponse(settings.frontend_dist / "index.html")

    return app
