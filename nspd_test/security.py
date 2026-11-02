from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SecurityManager:
    def __init__(self, password: str, *, session_ttl: int = 3600, max_failures: int = 5, failure_window: int = 900) -> None:
        self.password = password
        self.session_ttl = session_ttl
        self.max_failures = max_failures
        self.failure_window = failure_window
        self.key = secrets.token_bytes(32)
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.searches: dict[str, deque[float]] = defaultdict(deque)

    def login(self, password: str, client: str) -> tuple[str, str]:
        now = time.time()
        events = self.failures[client]
        while events and now - events[0] > self.failure_window:
            events.popleft()
        if len(events) >= self.max_failures:
            raise HTTPException(429, "Слишком много неудачных попыток. Повторите позже.")
        if not hmac.compare_digest(password.encode(), self.password.encode()):
            events.append(now)
            raise HTTPException(401, "Неверный пароль")
        events.clear()
        csrf = secrets.token_urlsafe(24)
        payload = {"exp": int(now + self.session_ttl), "csrf": csrf, "sid": secrets.token_urlsafe(12)}
        raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(self.key, raw.encode(), hashlib.sha256).hexdigest()
        return f"{raw}.{signature}", csrf

    def session(self, request: Request) -> dict[str, str | int]:
        token = request.cookies.get("nspd_session", "")
        try:
            raw, signature = token.rsplit(".", 1)
            expected = hmac.new(self.key, raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            if int(payload["exp"]) < time.time():
                raise ValueError
            return payload
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise HTTPException(401, "Требуется авторизация")

    def require_csrf(self, request: Request, session: dict[str, str | int]) -> None:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, str(session["csrf"])):
            raise HTTPException(403, "Недействительный CSRF-токен")

    def check_search_rate(self, key: str, *, limit: int = 3, window: int = 60) -> None:
        now = time.time()
        events = self.searches[key]
        while events and now - events[0] > window:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(429, "Превышена частота поиска. Повторите позже.")
        events.append(now)
