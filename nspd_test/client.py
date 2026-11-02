"""Direct retry-free NSPD client adapted from Bot 2 nspd_http.py."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from .address_normalization import normalize_cadastral_number, normalize_cadastral_quarter

HOST = "nspd.gov.ru"
ROOT_URL = "https://nspd.gov.ru/"
API_URL = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
MAX_RESPONSE_BYTES = 256_000


class NspdHttpError(ConnectionError):
    def __init__(self, reason: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(reason)


class ResponseRecorder(Protocol):
    max_response_bytes: int
    def capture_http(self, **values: Any) -> None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_verified_nspd_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS context is unavailable")
    return context


def nspd_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Referer": "https://nspd.gov.ru/map?thematic=PKK",
        "Host": HOST,
        "Accept": "application/json, text/plain, */*",
    }


class VerifiedNspdHttpSearcher:
    def __init__(self, *, timeout: float = 10.0, context: ssl.SSLContext | None = None, debug_recorder: ResponseRecorder | None = None) -> None:
        if timeout <= 0:
            raise ValueError("invalid NSPD timeout setting")
        self.timeout = float(timeout)
        self.context = context or build_verified_nspd_context()
        self.debug_recorder = debug_recorder

    def search(self, query: str, *, region: str, locality: str, address: str, timeout_seconds: float | None = None) -> list[dict[str, str]]:
        del region, locality, address
        timeout = min(self.timeout, timeout_seconds or self.timeout)
        if timeout <= 0:
            raise TimeoutError("request_timeout")
        url = API_URL + "?" + urllib.parse.urlencode({"query": query, "thematicSearchId": "1"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=self.context),
            _NoRedirect(),
        )
        request = urllib.request.Request(url, headers=nspd_headers(), method="GET")
        started = time.monotonic()
        read_limit = self.debug_recorder.max_response_bytes if self.debug_recorder else MAX_RESPONSE_BYTES
        try:
            with opener.open(request, timeout=timeout) as response:
                status = int(response.status)
                raw = response.read(read_limit + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            body = exc.read(read_limit + 1) if self.debug_recorder else None
            self._capture(query, url, status, started, body[:read_limit] if body else None, bool(body and len(body) > read_limit), f"HTTP_{status}")
            if status == 404:
                return []
            raise NspdHttpError(f"HTTP_{status}", status=status) from exc
        except Exception as exc:
            self._capture(query, url, None, started, None, False, type(exc).__name__)
            raise
        truncated = len(raw) > read_limit
        body = raw[:read_limit]
        if truncated:
            self._capture(query, url, status, started, body, True, "response_too_large")
            raise NspdHttpError("response_too_large", status=status)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._capture(query, url, status, started, body, False, "invalid_json")
            raise NspdHttpError("invalid_json", status=status) from exc
        features = payload.get("data", {}).get("features") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else None
        if not isinstance(features, list):
            self._capture(query, url, status, started, body, False, "invalid_response_schema")
            raise NspdHttpError("invalid_response_schema", status=status)
        self._capture(query, url, status, started, body, False, None)
        return extract_candidates(features)

    def _capture(self, query: str, url: str, status: int | None, started: float, body: bytes | None, truncated: bool, error: str | None) -> None:
        if self.debug_recorder:
            self.debug_recorder.capture_http(query=query, url=url, method="GET", status=status, duration_ms=round((time.monotonic() - started) * 1000), body=body, body_truncated=truncated, error=error)


def extract_candidates(features: list[Any]) -> list[dict[str, Any]]:
    """Match Bot 2 extraction and retain NSPD score for diagnostics only."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        options = properties.get("options") if isinstance(properties, dict) else None
        if not isinstance(options, dict):
            continue
        number = normalize_cadastral_number(options.get("cad_number") or options.get("cad_num"))
        if not number or number in seen:
            continue
        seen.add(number)
        result.append({
            "cadastral_number": number,
            "address": str(options.get("readable_address") or options.get("address_readable_address") or "").strip(),
            "category": str(properties.get("category") or ""),
            "category_name": str(properties.get("categoryName") or properties.get("category_name") or ""),
            "building_name": str(options.get("building_name") or ""),
            "purpose": str(options.get("purpose") or ""),
            "permitted_use": str(options.get("permitted_use_established_by_document") or ""),
            "cadastral_quarter": normalize_cadastral_quarter(options.get("quarter_cad_number")),
            "score": properties.get("score"),
        })
    return result

