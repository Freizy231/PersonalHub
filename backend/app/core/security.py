"""Single-user local sessions. No user/tenant database or cloud identity."""
from dataclasses import dataclass
import secrets
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "personal_hub_session"
SESSION_SECONDS = 8 * 60 * 60
# Hard ceiling regardless of activity, so a stolen cookie cannot live forever.
SESSION_ABSOLUTE_SECONDS = 7 * 24 * 60 * 60
# Renew at most this often, so ordinary polling does not rewrite state on every request.
SESSION_RENEW_AFTER = 5 * 60


@dataclass
class Session:
    csrf_token: str
    expires_at: float
    created_at: float = 0.0


class LocalAuth:
    def __init__(self):
        self.bootstrap = secrets.token_urlsafe(32)
        self.sessions: dict[str, Session] = {}

    def exchange(self, token: str) -> tuple[str, Session]:
        if not self.bootstrap or not secrets.compare_digest(token.encode(), self.bootstrap.encode()):
            raise HTTPException(401, "BOOTSTRAP_INVALID")
        self.bootstrap = ""  # One-time exchange.
        cookie = secrets.token_urlsafe(48)
        now = time.monotonic()
        session = Session(secrets.token_urlsafe(32), now + SESSION_SECONDS, now)
        self.sessions[cookie] = session
        return cookie, session

    def require(self, request: Request) -> Session:
        key = request.cookies.get(COOKIE_NAME, "")
        session = self.sessions.get(key)
        if not session or session.expires_at <= time.monotonic():
            self.sessions.pop(key, None)
            raise HTTPException(401, "SESSION_REQUIRED")
        # Sliding idle window: 8 hours since last use, never beyond the absolute ceiling.
        # The private vault keeps its own, much shorter idle and absolute limits.
        now = time.monotonic()
        if session.expires_at - now < SESSION_SECONDS - SESSION_RENEW_AFTER:
            session.expires_at = min(now + SESSION_SECONDS, (session.created_at or now) + SESSION_ABSOLUTE_SECONDS)
        return session


async def require_session(request: Request) -> Session:
    return request.app.state.auth.require(request)
