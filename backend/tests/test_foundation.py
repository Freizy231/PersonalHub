import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import httpx

from backend.tests.async_support import AsyncAPITestCase
from backend.app.core.config import Settings
from backend.app.main import create_app, database_status
from backend.app.core.security import COOKIE_NAME
from backend.manage import initialize_empty, main


class FoundationTests(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(reminders_enabled=False, data_root=self.root / "data", legacy_database=self.root / "legacy.db")
        self.origin = "http://127.0.0.1:8765"
        self.app = create_app(self.settings)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=self.origin)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temp.cleanup()

    async def login(self):
        response = await self.client.post("/api/v1/session/exchange", json={"token": self.app.state.auth.bootstrap},
                                    headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    async def test_start_and_health_never_create_or_migrate_either_database(self):
        self.assertEqual((await self.client.get("/api/v1/health")).status_code, 200)
        await self.login()
        self.assertEqual((await self.client.get("/api/v1/system")).json()["database"], "not_initialized")
        self.assertFalse(self.settings.database.exists())
        self.assertFalse(self.settings.legacy_database.exists())
        launch = self.settings.data_root / "config" / "launch-8765.url"
        self.assertEqual(launch.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.settings.data_root.stat().st_mode & 0o777, 0o700)

    async def test_session_origin_csrf_and_one_time_bootstrap(self):
        self.assertEqual((await self.client.get("/api/v1/system")).status_code, 401)
        token = self.app.state.auth.bootstrap
        csrf = await self.login()
        response = await self.client.post("/api/v1/session/exchange", json={"token": token}, headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 401)
        for headers in ({}, {"Origin": "https://evil.example"}, {"Origin": self.origin},
                        {"Origin": self.origin, "X-CSRF-Token": "wrong"}):
            self.assertEqual((await self.client.post("/api/v1/session/logout", headers=headers)).status_code, 403)
        self.assertEqual((await self.client.post("/api/v1/session/logout", headers={"Origin": self.origin, "X-CSRF-Token": csrf})).status_code, 200)
        self.assertEqual((await self.client.get("/api/v1/system")).status_code, 401)

    async def test_invalid_host_cross_origin_and_expired_session(self):
        self.assertEqual((await self.client.get("/api/v1/health", headers={"Host": "evil.example"})).status_code, 400)
        self.assertEqual((await self.client.get("/api/v1/health", headers={"Origin": "null"})).status_code, 403)
        await self.login()
        key = self.client.cookies.get(COOKIE_NAME)
        self.app.state.auth.sessions[key].expires_at = time.monotonic() - 1
        self.assertEqual((await self.client.get("/api/v1/session")).status_code, 401)

    async def test_session_slides_with_use_but_never_beyond_the_absolute_ceiling(self):
        from backend.app.core.security import SESSION_ABSOLUTE_SECONDS, SESSION_SECONDS
        await self.login()
        key = self.client.cookies.get(COOKIE_NAME)
        session = self.app.state.auth.sessions[key]
        now = time.monotonic()

        # Two hours of idle time left: use renews the idle window back to the full length.
        session.expires_at = now + 2 * 60 * 60
        self.assertEqual((await self.client.get("/api/v1/session")).status_code, 200)
        self.assertGreater(session.expires_at - time.monotonic(), SESSION_SECONDS - 60)

        # A session created long ago may slide only up to the absolute ceiling.
        session.created_at = time.monotonic() - SESSION_ABSOLUTE_SECONDS + 600
        session.expires_at = time.monotonic() + 2 * 60 * 60
        self.assertEqual((await self.client.get("/api/v1/session")).status_code, 200)
        self.assertLessEqual(session.expires_at - time.monotonic(), 601)

        # Past the ceiling, the session is refused even though it was just used.
        session.created_at = time.monotonic() - SESSION_ABSOLUTE_SECONDS - 5
        session.expires_at = time.monotonic() - 1
        self.assertEqual((await self.client.get("/api/v1/session")).status_code, 401)

    async def test_recent_use_does_not_rewrite_the_session_on_every_request(self):
        await self.login()
        session = self.app.state.auth.sessions[self.client.cookies.get(COOKIE_NAME)]
        before = session.expires_at
        await self.client.get("/api/v1/session")
        await self.client.get("/api/v1/session")
        self.assertEqual(session.expires_at, before, "renewal is rate-limited to once per few minutes")

    async def test_unexpected_and_database_errors_return_a_json_body_with_a_request_id(self):
        from sqlalchemy.exc import OperationalError

        # A separate app without the built frontend: its catch-all route would otherwise answer first.
        from dataclasses import replace
        probe = create_app(replace(self.settings, frontend_dist=self.settings.data_root / "no-frontend"))

        @probe.get("/api/v1/_probe/boom")
        async def boom():
            raise RuntimeError("secret-title")

        @probe.get("/api/v1/_probe/db")
        async def database():
            raise OperationalError("SELECT 1", {"title": "secret-title"}, Exception("locked"))

        transport = httpx.ASGITransport(app=probe, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            unexpected = await client.get("/api/v1/_probe/boom")
            self.assertEqual(unexpected.status_code, 500)
            self.assertEqual(unexpected.json()["code"], "INTERNAL_ERROR")
            self.assertEqual(len(unexpected.json()["request_id"]), 16)
            busy = await client.get("/api/v1/_probe/db")
            self.assertEqual(busy.status_code, 503)
            self.assertEqual(busy.json()["code"], "DATABASE_UNAVAILABLE")
            for body in (unexpected.text, busy.text):
                self.assertNotIn("secret-title", body)

    async def test_non_ascii_credentials_are_rejected_not_a_server_error(self):
        # Header values travel as latin-1 bytes; a byte above 0x7f used to crash compare_digest with a 500.
        response = await self.client.post("/api/v1/session/exchange", json={"token": "ü" * 24}, headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 401)
        csrf = await self.login()
        headers = {"Origin": self.origin, "X-CSRF-Token": "\u00fc" + csrf[1:]}
        response = await self.client.post("/api/v1/session/logout", headers=[(k.encode(), v.encode("latin-1")) for k, v in headers.items()])
        self.assertEqual(response.status_code, 403)

    def test_rotated_log_files_stay_private_under_a_permissive_umask(self):
        import logging, os
        from backend.app.main import PrivateRotatingHandler
        directory = self.root / "logcheck"
        directory.mkdir(mode=0o700)
        previous = os.umask(0o022)
        handler = PrivateRotatingHandler(directory / "hub.log", maxBytes=1024, backupCount=2, encoding="utf-8")
        try:
            handler.doRollover()
            handler.emit(logging.LogRecord("personal_hub", logging.ERROR, __file__, 0, "probe", None, None))
            self.assertEqual((directory / "hub.log").stat().st_mode & 0o777, 0o600)
        finally:
            handler.close()
            os.umask(previous)

    async def test_cookie_and_response_headers_and_no_secret_in_validation_error(self):
        token = self.app.state.auth.bootstrap
        response = await self.client.post("/api/v1/session/exchange", json={"token": token}, headers={"Origin": self.origin})
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        response = await self.client.post("/api/v1/session/exchange", json={"token": "very-secret", "unexpected": "secret"}, headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("very-secret", response.text)
        self.assertNotIn(token, (await self.client.get("/api/v1/system")).text)

    async def test_explicit_new_database_initialization_and_repeated_init_refusal(self):
        initialize_empty(self.settings.data_root)
        self.assertEqual(database_status(self.settings.database), "upgrade_required")
        before = self.settings.database.read_bytes()
        with self.assertRaises(FileExistsError):
            initialize_empty(self.settings.data_root)
        self.assertEqual(before, self.settings.database.read_bytes())
        self.assertEqual(self.settings.database.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.settings.database) as con:
            self.assertEqual(con.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall(), [("alembic_version",)])

    async def test_unknown_revision_is_not_repaired_or_overwritten(self):
        initialize_empty(self.settings.data_root)
        with sqlite3.connect(self.settings.database) as con:
            con.execute("UPDATE alembic_version SET version_num='future_schema'")
        before = self.settings.database.read_bytes()
        await self.login()
        self.assertEqual((await self.client.get("/api/v1/system")).json()["database"], "version_mismatch")
        self.assertEqual(before, self.settings.database.read_bytes())

    async def test_private_root_and_symlink_guard(self):
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(PermissionError):
            initialize_empty(public)
        self.assertFalse((public / "db").exists())
        link = self.root / "link"
        link.symlink_to(public, target_is_directory=True)
        with self.assertRaises(ValueError):
            Settings(reminders_enabled=False, data_root=link, legacy_database=self.root / "legacy.db")

    async def test_serve_environment_port_and_explicit_override_keep_loopback_binding(self):
        with mock.patch.dict("os.environ", {"PERSONAL_HUB_PORT": "18765"}), \
                mock.patch("uvicorn.run") as run, mock.patch("builtins.print"):
            self.assertEqual(main(["serve", "--data-root", str(self.settings.data_root)]), 0)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["port"], 18765)
            self.assertEqual(run.call_args.args[0].state.settings.port, 18765)
            self.assertEqual(main(["serve", "--data-root", str(self.settings.data_root), "--port", "18766"]), 0)
            self.assertEqual(run.call_args.kwargs["port"], 18766)
            self.assertEqual(run.call_args.args[0].state.settings.origins,
                             frozenset({"http://127.0.0.1:18766", "http://localhost:18766"}))
            self.assertFalse(self.settings.database.exists())


if __name__ == "__main__":
    unittest.main()
