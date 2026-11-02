from __future__ import annotations

import json
import queue
import re
import socket
import ssl
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from .client import API_URL, HOST, ROOT_URL, _NoRedirect, build_verified_nspd_context, nspd_headers
from .models import ProbeResult

SENSITIVE = re.compile(r"token|secret|password|authorization|cookie|api[_-]?key", re.I)


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class DiagnosticStore:
    max_response_bytes = 1_000_000

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.events_file = directory / "events.jsonl"
        self._lock = Lock()
        self._request_count = 0

    def event(self, stage: str, result: str, *, url: str = "", http_status: int | None = None, duration_ms: int = 0, error: str = "", exception: BaseException | None = None, traceback_text: str = "", details: Any = None) -> None:
        item = redact({
            "timestamp": datetime.now(timezone.utc).isoformat(), "stage": stage,
            "url": url, "http_status": http_status, "duration_ms": duration_ms,
            "result": result, "error": error,
            "traceback": traceback_text or ("".join(traceback.format_exception(exception)) if exception else ""),
            "details": details or {},
        })
        with self._lock:
            with self.events_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")

    def capture_http(self, **values: Any) -> None:
        with self._lock:
            self._request_count += 1
            path = self.directory / f"nspd_response_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{self._request_count:03d}.json"
            body = values.pop("body", None)
            parsed: Any = None
            if body:
                try:
                    parsed = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    parsed = {"non_json_body": body.decode("utf-8", errors="replace")[:10000]}
            payload = redact({"timestamp": datetime.now(timezone.utc).isoformat(), **values, "response": parsed})
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.events_file.exists():
            return []
        lines = self.events_file.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]
        result = []
        for line in reversed(lines):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    def files(self) -> list[dict[str, Any]]:
        return [{"name": p.name, "size": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()} for p in sorted(self.directory.glob("*.json*"), key=lambda x: x.stat().st_mtime, reverse=True)]


def _duration(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _bounded_call(function, timeout: float):
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
    def target():
        try:
            results.put((True, function()))
        except BaseException as exc:
            results.put((False, exc))
    Thread(target=target, daemon=True, name="nspd-probe").start()
    try:
        ok, value = results.get(timeout=max(0.001, timeout))
    except queue.Empty as exc:
        raise TimeoutError("probe_timeout") from exc
    if ok:
        return value
    raise value


def run_network_diagnostics(store: DiagnosticStore, *, timeout: float = 8.0) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    started = time.monotonic()
    try:
        records = _bounded_call(lambda: socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM), min(timeout, 5.0))
        addresses = list(dict.fromkeys(item[4][0] for item in records))
        if not addresses:
            raise socket.gaierror("no addresses")
        result = ProbeResult("DNS", "УСПЕШНО", _duration(started), ", ".join(addresses[:4]))
    except Exception as exc:
        addresses = []
        result = ProbeResult("DNS", "ОШИБКА", _duration(started), error=type(exc).__name__)
    results.append(result); store.event("DNS", result.status, duration_ms=result.duration_ms, error=result.error, details=result.result)
    if not addresses:
        return _append_skipped(results, store, ("TCP", "TLS", "HTTP", "API"), "DNS недоступен")

    address = addresses[0]
    started = time.monotonic()
    try:
        with socket.create_connection((address, 443), timeout=timeout):
            pass
        result = ProbeResult("TCP", "УСПЕШНО", _duration(started), f"{address}:443")
    except Exception as exc:
        result = ProbeResult("TCP", "ОШИБКА", _duration(started), error=type(exc).__name__)
    results.append(result); store.event("TCP", result.status, duration_ms=result.duration_ms, error=result.error)
    if result.status != "УСПЕШНО":
        return _append_skipped(results, store, ("TLS", "HTTP", "API"), "TCP недоступен")

    started = time.monotonic()
    try:
        context = build_verified_nspd_context()
        with socket.create_connection((address, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=HOST) as tls:
                version = tls.version() or "unknown"
        result = ProbeResult("TLS", "УСПЕШНО", _duration(started), version)
    except Exception as exc:
        result = ProbeResult("TLS", "ОШИБКА", _duration(started), error=type(exc).__name__)
    results.append(result); store.event("TLS", result.status, duration_ms=result.duration_ms, error=result.error)
    if result.status != "УСПЕШНО":
        return _append_skipped(results, store, ("HTTP", "API"), "TLS недоступен")

    results.append(_http_probe("HTTP", ROOT_URL, False, timeout, store))
    # API is intentionally still tested if HTTP returned a valid error response.
    query = urllib.parse.urlencode({"query": "NSPD diagnostic connectivity test", "thematicSearchId": "1"})
    results.append(_http_probe("API", f"{API_URL}?{query}", True, timeout, store))
    return results


def _http_probe(stage: str, url: str, expect_json: bool, timeout: float, store: DiagnosticStore) -> ProbeResult:
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=build_verified_nspd_context()), _NoRedirect())
    request = urllib.request.Request(url, headers=nspd_headers(), method="GET")
    try:
        try:
            with opener.open(request, timeout=timeout) as response:
                status, body = int(response.status), response.read(256_001)
        except urllib.error.HTTPError as exc:
            status, body = int(exc.code), exc.read(256_001)
        if len(body) > 256_000:
            raise ValueError("response_too_large")
        if expect_json and 200 <= status < 300:
            json.loads(body.decode("utf-8"))
        ok = status < 400
        result = ProbeResult(stage, "УСПЕШНО" if ok else "ОШИБКА", _duration(started), f"HTTP {status}", "" if ok else f"HTTP_{status}", status)
    except Exception as exc:
        result = ProbeResult(stage, "ОШИБКА", _duration(started), error=type(exc).__name__)
    store.event(stage, result.status, url=url, http_status=result.http_status, duration_ms=result.duration_ms, error=result.error)
    return result


def _append_skipped(results: list[ProbeResult], store: DiagnosticStore, stages: tuple[str, ...], reason: str) -> list[ProbeResult]:
    for stage in stages:
        result = ProbeResult(stage, "ПРОПУЩЕНО", 0, error=reason)
        results.append(result); store.event(stage, result.status, error=reason)
    return results
