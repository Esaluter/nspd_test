from __future__ import annotations

import asyncio
import os
import platform
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nspd_test import __version__
from nspd_test.client import VerifiedNspdHttpSearcher
from nspd_test.diagnostics import DiagnosticStore, run_network_diagnostics
from nspd_test.search import CadastralSearchService
from nspd_test.security import SecurityManager

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DIAGNOSTICS_DIR = BASE_DIR / "diagnostics"
STARTED_AT = datetime.now(timezone.utc)


class SearchRequest(BaseModel):
    address: str = Field(min_length=5, max_length=1000)
    debug: bool = False


def create_app(*, password: str | None = None, searcher=None, store: DiagnosticStore | None = None) -> FastAPI:
    configured_password = password if password is not None else os.getenv("NSPD_WEB_PASSWORD", "")
    security = SecurityManager(configured_password or "local-only")
    diagnostics = store or DiagnosticStore(DIAGNOSTICS_DIR)
    client = searcher or VerifiedNspdHttpSearcher(timeout=float(os.getenv("NSPD_CLIENT_TIMEOUT", "10")))
    semaphore = asyncio.Semaphore(max(1, min(int(os.getenv("NSPD_MAX_CONCURRENT", "2")), 4)))
    counters = {"checks": 0, "searches": 0}
    last_nspd_status = "НЕ ПРОВЕРЕНО"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.security = security
        yield

    app = FastAPI(title="NSPD TEST WEB", version=__version__, lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

    def auth(request: Request, csrf: bool = False):
        session = security.session(request)
        if csrf:
            security.require_csrf(request, session)
        return session

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.post("/api/login")
    async def login(request: Request, password_value: str = Form(alias="password")):
        client_ip = request.client.host if request.client else "unknown"
        token, csrf = security.login(password_value, client_ip)
        response = JSONResponse({"ok": True, "csrf": csrf})
        secure_cookie = os.getenv("NSPD_SECURE_COOKIE", "0") == "1"
        response.set_cookie("nspd_session", token, max_age=3600, httponly=True, secure=secure_cookie, samesite="strict", path="/")
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        auth(request, True)
        response = JSONResponse({"ok": True})
        response.delete_cookie("nspd_session", path="/")
        return response

    @app.get("/api/status")
    async def status(request: Request):
        auth(request)
        return {"server": "РАБОТАЕТ", "started_at": STARTED_AT.isoformat(), "python": platform.python_version(), "client": "ГОТОВ", "nspd": last_nspd_status, **counters}

    @app.post("/api/diagnostics/network")
    async def network(request: Request):
        nonlocal last_nspd_status
        auth(request, True)
        async with semaphore:
            try:
                results = await asyncio.wait_for(asyncio.to_thread(run_network_diagnostics, diagnostics), timeout=55)
            except asyncio.TimeoutError:
                diagnostics.event("NETWORK", "ОШИБКА", error="overall_timeout")
                raise HTTPException(504, "Диагностика превысила лимит времени")
        counters["checks"] += 1
        last_nspd_status = "ДОСТУПЕН" if all(r.status == "УСПЕШНО" for r in results) else "ЕСТЬ ОШИБКИ"
        return {"stages": [r.__dict__ for r in results], "nspd": last_nspd_status}

    @app.post("/api/search")
    async def search(payload: SearchRequest, request: Request):
        session = auth(request, True)
        security.check_search_rate(str(session["sid"]))
        debug_store = diagnostics if payload.debug else None
        active_searcher = client
        if isinstance(client, VerifiedNspdHttpSearcher):
            active_searcher = VerifiedNspdHttpSearcher(timeout=client.timeout, context=client.context, debug_recorder=debug_store)
        service = CadastralSearchService(active_searcher, overall_timeout=float(os.getenv("NSPD_SEARCH_TIMEOUT", "30")), attempts=int(os.getenv("NSPD_ATTEMPTS", "2")), request_delay=float(os.getenv("NSPD_REQUEST_DELAY_SECONDS", "1")))
        async with semaphore:
            result = await asyncio.to_thread(service.run, payload.address.strip())
        counters["searches"] += 1
        result_payload = result.to_dict()
        diagnostics.event(
            "SEARCH",
            result.status,
            duration_ms=result.duration_ms,
            error=result.error,
            traceback_text=result.error_traceback,
            details={
                "address": result.source_address,
                "received": result.candidates_received,
                "accepted": result.candidates_accepted,
                "candidates": result_payload["candidates"],
                "rejected": result_payload["rejected"],
                "attempts": result_payload["attempts"],
            },
        )
        return result_payload

    @app.get("/api/diagnostics/events")
    async def events(request: Request, limit: int = 100):
        auth(request)
        return {"events": diagnostics.recent(limit), "files": diagnostics.files()}

    @app.get("/api/diagnostics/download/{filename}")
    async def download(filename: str, request: Request):
        auth(request)
        if Path(filename).name != filename or not filename.endswith((".json", ".jsonl")):
            raise HTTPException(400, "Недопустимое имя файла")
        path = DIAGNOSTICS_DIR / filename if store is None else diagnostics.directory / filename
        if not path.is_file() or path.parent.resolve() != diagnostics.directory.resolve():
            raise HTTPException(404, "Файл не найден")
        return FileResponse(path, filename=filename, media_type="application/json")

    return app


_CONFIGURED_PASSWORD = os.getenv("NSPD_WEB_PASSWORD", "")
_LOCAL_EPHEMERAL_PASSWORD = secrets.token_urlsafe(12) if not _CONFIGURED_PASSWORD else ""
app = create_app(password=_CONFIGURED_PASSWORD or _LOCAL_EPHEMERAL_PASSWORD)


if __name__ == "__main__":
    password = os.getenv("NSPD_WEB_PASSWORD", "")
    host = "0.0.0.0" if password else "127.0.0.1"
    port = int(os.getenv("PORT") or os.getenv("BOT_PORT") or "8000")
    if not password:
        print("NSPD_WEB_PASSWORD не задан: безопасный локальный режим 127.0.0.1")
        print(f"Временный локальный пароль: {_LOCAL_EPHEMERAL_PASSWORD}")
    uvicorn.run("main:app", host=host, port=port, reload=False)
