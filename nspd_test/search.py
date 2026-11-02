from __future__ import annotations

import time
import traceback
from typing import Any, Protocol

from .address_normalization import build_search_variants, split_full_address
from .candidate_filter import filter_candidates_with_reasons
from .models import SearchAttempt, SearchResult


class Searcher(Protocol):
    def search(self, query: str, *, region: str, locality: str, address: str, timeout_seconds: float | None = None) -> list[dict[str, Any]]: ...


class CadastralSearchService:
    """One-address cascade extracted from Bot 2 without queue/Sheets concerns."""

    def __init__(self, searcher: Searcher, *, request_timeout: float = 10.0, overall_timeout: float = 30.0, attempts: int = 2, request_delay: float = 1.0) -> None:
        self.searcher = searcher
        self.request_timeout = request_timeout
        self.overall_timeout = overall_timeout
        self.attempts = max(1, min(attempts, 3))
        self.request_delay = max(0.0, request_delay)

    def run(self, full_address: str) -> SearchResult:
        started = time.monotonic()
        region, locality, address = split_full_address(full_address)
        attempts_log: list[SearchAttempt] = []
        all_received: dict[str, dict[str, str]] = {}
        all_rejected = []
        rejected_seen: set[tuple[str, str]] = set()
        accepted_final: list[dict[str, Any]] = []
        try:
            if not locality or not address:
                raise ValueError("locality_or_street_not_recognized")
            for level, query in build_search_variants(region=region, locality=locality, address=address):
                if time.monotonic() - started >= self.overall_timeout:
                    raise TimeoutError("overall_timeout")
                candidates: list[dict[str, str]] = []
                last_error: Exception | None = None
                for retry in range(self.attempts):
                    remaining = self.overall_timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError("overall_timeout")
                    try:
                        candidates = self.searcher.search(query, region=region, locality=locality, address=address, timeout_seconds=min(self.request_timeout, remaining))
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if retry + 1 < self.attempts and self.request_delay:
                            time.sleep(min(self.request_delay, max(0, remaining)))
                if last_error:
                    raise last_error
                for candidate in candidates:
                    number = candidate.get("cadastral_number", "")
                    if number:
                        all_received[number] = candidate
                accepted, reasons, rejected = filter_candidates_with_reasons(candidates, region=region, locality=locality, address=address)
                for item in rejected:
                    key = (item.cadastral_number, item.reason)
                    if key not in rejected_seen:
                        rejected_seen.add(key)
                        all_rejected.append(item)
                attempts_log.append(SearchAttempt(level, query, len(candidates), len(accepted), len(rejected), reasons))
                if accepted:
                    accepted_final = accepted
                    break
            count = len(accepted_final)
            status = "НАЙДЕНО" if count == 1 else "НЕОДНОЗНАЧНО" if count > 1 else "НЕ НАЙДЕНО"
            ambiguity = "Найдено несколько проверенных кадастровых объектов; автоматический выбор запрещён." if count > 1 else ""
            return SearchResult(full_address, status, len(all_received), count, accepted_final, all_rejected, attempts_log, round((time.monotonic() - started) * 1000), ambiguity)
        except Exception as exc:
            return SearchResult(full_address, "ОШИБКА", len(all_received), 0, [], all_rejected, attempts_log, round((time.monotonic() - started) * 1000), error=type(exc).__name__, error_traceback=traceback.format_exc())

