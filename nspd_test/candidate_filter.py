from __future__ import annotations

from collections import Counter

from .address_normalization import (
    _house, _locality_key, _normalized_text, _qualifier, _region_key, _street,
    cadastral_quarter, normalize_cadastral_number, normalize_cadastral_quarter,
)
from .models import RejectedCandidate


def candidate_rejection_reason(candidate: dict[str, str], *, region: str, locality: str, address: str) -> str | None:
    return candidate_filter_trace(
        candidate, region=region, locality=locality, address=address
    )[0]


def candidate_filter_trace(candidate: dict[str, str], *, region: str, locality: str, address: str) -> tuple[str | None, dict[str, str]]:
    """Apply Bot 2 checks in their original order and expose diagnostics."""
    checks: dict[str, str] = {}
    candidate_address = str(candidate.get("address", "")).strip()
    if not candidate_address:
        checks["address"] = "ОШИБКА"
        return "недостаточно данных", checks
    checks["address"] = "УСПЕШНО"
    normalized = _normalized_text(candidate_address)
    locality_key = _locality_key(locality)
    region_key = _region_key(region)
    if region_key and region_key != locality_key and region_key not in normalized:
        checks["region"] = "ОШИБКА"
        return "регион", checks
    checks["region"] = "УСПЕШНО" if region_key else "НЕ ЗАДАН"
    if locality_key and locality_key not in normalized:
        checks["locality"] = "ОШИБКА"
        return "населённый пункт", checks
    checks["locality"] = "УСПЕШНО" if locality_key else "НЕ ЗАДАН"
    source_street, candidate_street = _street(address), _street(candidate_address)
    if not candidate_street:
        checks["street"] = "НЕДОСТАТОЧНО ДАННЫХ"
        return "недостаточно данных", checks
    if source_street and candidate_street != source_street:
        checks["street"] = "ОШИБКА"
        return "улица", checks
    checks["street"] = "УСПЕШНО"
    source_house = _house(address, allow_trailing=True)
    candidate_house = _house(candidate_address, allow_trailing=True)
    if not candidate_house:
        checks["house"] = "НЕДОСТАТОЧНО ДАННЫХ"
        return "недостаточно данных", checks
    if source_house and candidate_house != source_house:
        checks["house"] = "ОШИБКА"
        return "дом", checks
    checks["house"] = "УСПЕШНО"
    for names, reason in (("корп|корпус", "корпус"), ("стр|строение", "строение")):
        source_value = _qualifier(address, names)
        if not source_value:
            checks[reason] = "НЕ ЗАДАН"
            continue
        candidate_value = _qualifier(candidate_address, names)
        if not candidate_value:
            checks[reason] = "НЕДОСТАТОЧНО ДАННЫХ"
            return "недостаточно данных", checks
        if candidate_value != source_value:
            checks[reason] = "ОШИБКА"
            return reason, checks
        checks[reason] = "УСПЕШНО"
    return None, checks


def filter_candidates_with_reasons(candidates: list[dict[str, str]], *, region: str, locality: str, address: str) -> tuple[list[dict[str, str]], dict[str, int], list[RejectedCandidate]]:
    accepted: list[dict[str, str]] = []
    reasons: Counter[str] = Counter()
    rejected: list[RejectedCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        number = normalize_cadastral_number(candidate.get("cadastral_number"))
        if not number or number in seen:
            reason, checks = "недостаточно данных", {"cadastral_number": "ОШИБКА"}
        else:
            reason, checks = candidate_filter_trace(candidate, region=region, locality=locality, address=address)
        if reason:
            reasons[reason] += 1
            rejected.append(RejectedCandidate(
                cadastral_number=number,
                address=str(candidate.get("address", ""))[:1000],
                reason=reason,
                source_street=_street(address),
                candidate_street=_street(candidate.get("address", "")),
                source_house=_house(address, allow_trailing=True),
                candidate_house=_house(candidate.get("address", ""), allow_trailing=True),
                category_name=str(candidate.get("category_name", "")),
                cadastral_quarter=normalize_cadastral_quarter(candidate.get("cadastral_quarter")) or cadastral_quarter(number),
                score=candidate.get("score"),
                checks=checks,
            ))
            continue
        seen.add(number)
        accepted.append({
            **candidate,
            "cadastral_number": number,
            "cadastral_quarter": normalize_cadastral_quarter(candidate.get("cadastral_quarter")) or cadastral_quarter(number),
            "filter_result": "ПРИНЯТ",
            "filter_checks": checks,
        })
    return accepted, dict(reasons), rejected
