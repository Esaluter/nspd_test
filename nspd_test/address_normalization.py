"""Address normalization extracted from Bot 2 cadastral_enrichment.py."""

from __future__ import annotations

import re
from typing import Any


CADASTRAL_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{1,7}):(\d+)$")
CADASTRAL_QUARTER_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{1,7})$")


def normalize_cadastral_number(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "").replace("-", ":")
    match = CADASTRAL_RE.fullmatch(text)
    return ":".join(match.groups()) if match else ""


def normalize_cadastral_quarter(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "").replace("-", ":")
    match = CADASTRAL_QUARTER_RE.fullmatch(text)
    return ":".join(match.groups()) if match else ""


def cadastral_quarter(value: Any) -> str:
    number = normalize_cadastral_number(value)
    return ":".join(number.split(":")[:3]) if number else ""


def _normalized_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[.,]", " ", text)
    text = re.sub(r"[^0-9a-zа-я/]+", " ", text)
    return " ".join(text.split())


def _region_key(value: Any) -> str:
    ignored = {
        "область", "обл", "край", "республика", "респ", "автономный",
        "автономная", "округ", "ао", "город", "г",
    }
    return " ".join(w for w in _normalized_text(value).split() if w not in ignored)


def _locality_key(value: Any) -> str:
    words = _normalized_text(value).split()
    prefixes = {"г", "город", "с", "село", "п", "пос", "поселок", "деревня", "д"}
    while words and words[0] in prefixes:
        words.pop(0)
    return " ".join(words)


def _house(value: Any, *, allow_trailing: bool = False) -> str:
    text = _normalized_text(value)
    match = re.search(r"\b(?:д|дом)\s*(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)", text)
    if not match and allow_trailing:
        match = re.search(r"\b(\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)$", text)
    return match.group(1) if match else ""


def _qualifier(value: Any, names: str) -> str:
    match = re.search(rf"\b(?:{names})\s*(\d+[а-яa-z]?)\b", _normalized_text(value))
    return match.group(1) if match else ""


def _street(value: Any) -> str:
    """Bot 2 implementation fixed on 02.11.2026."""
    marker = (
        r"(?:улица|ул|проспект|просп|пр\s+т|переулок|пер|шоссе|ш|"
        r"бульвар|бул|б\s+р|проезд|пр\s+д|набережная|наб|площадь|пл|"
        r"аллея|тупик|линия)"
    )
    administrative = re.compile(
        r"\b(?:российская федерация|муниципальный округ|городской округ|"
        r"вн\s+тер\s+г|район|город|область|республика|край)\b"
    )
    trailing_house = re.compile(r"\s+\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?$")
    for raw_segment in re.split(r"[,;]", str(value or "")):
        segment = trailing_house.sub("", _normalized_text(raw_segment)).strip()
        prefix = re.search(rf"\b{marker}\s+(.+)$", segment)
        if prefix:
            candidate = re.split(
                r"\s+(?:д|дом|стр|строение|корп|корпус)\b",
                prefix.group(1), maxsplit=1,
            )[0].strip()
            if candidate and not administrative.search(candidate):
                return candidate
        suffix = re.search(rf"^(.+?)\s+{marker}\b", segment)
        if suffix:
            candidate = suffix.group(1).strip()
            if candidate and not administrative.search(candidate):
                return candidate
    text = trailing_house.sub("", _normalized_text(value)).strip()
    match = re.search(
        rf"\b{marker}\s+(.+?)(?=\s+(?:д|дом|стр|строение|корп|корпус)\b|$)",
        text,
    )
    if not match:
        return ""
    candidate = " ".join(match.group(1).split())
    return "" if administrative.search(candidate) else candidate


def normalize_address(*, region: str, locality: str, address: str) -> str:
    components: list[str] = []
    for geo in (_region_key(region), _locality_key(locality)):
        if geo and geo not in components:
            components.append(geo)
    street = _street(address)
    house = _house(address, allow_trailing=True)
    corpus = _qualifier(address, "корп|корпус")
    building = _qualifier(address, "стр|строение")
    if street:
        components.append(street)
    if house:
        components.append(house)
    if corpus:
        components.extend(("корпус", corpus))
    if building:
        components.extend(("строение", building))
    return " ".join(components)


def build_search_variants(*, region: str, locality: str, address: str) -> list[tuple[int, str]]:
    original = ", ".join(p.strip() for p in (region, locality, address) if p.strip())
    normalized = normalize_address(region=region, locality=locality, address=address)
    geo: list[str] = []
    for value in (_region_key(region), _locality_key(locality)):
        if value and value not in geo:
            geo.append(value)
    expanded = ", ".join(p for p in (*geo, _street(address), _house(address, allow_trailing=True)) if p)
    variants: list[tuple[int, str]] = []
    seen: set[str] = set()
    for level, query in ((1, original), (2, normalized), (3, expanded)):
        key = _normalized_text(query)
        if query and key and key not in seen:
            seen.add(key)
            variants.append((level, query))
    return variants


def split_full_address(full_address: str) -> tuple[str, str, str]:
    """Split the web form into the structured fields that Bot 2 receives.

    Only explicit region text is treated as a region.  The last administrative
    segment before an explicit street is the locality; this covers common input
    such as ``Казань, ул. Баумана 52`` without inventing a region.
    """
    parts = [p.strip() for p in full_address.split(",") if p.strip()]
    region = ""
    locality = ""
    address_start: int | None = None
    country_markers = {"российская федерация", "россия", "рф"}
    address_markers = re.compile(
        r"\b(?:улица|ул|проспект|просп|пр\s+т|переулок|пер|шоссе|ш|"
        r"бульвар|бул|б\s+р|проезд|пр\s+д|набережная|наб|площадь|пл|"
        r"аллея|тупик|линия)\b"
    )
    region_markers = re.compile(
        r"\b(?:область|обл|край|республика|респ|автономный\s+округ|ао)\b"
    )
    federal_cities = {"москва", "санкт петербург", "севастополь"}
    for index, part in enumerate(parts):
        norm = _normalized_text(part)
        if address_markers.search(norm):
            address_start = index
            break
        if norm in country_markers:
            continue
        if norm in federal_cities:
            region = part
            locality = part
            continue
        if not region and region_markers.search(norm):
            region = part
            continue
        # Bot 2 gets locality as a separate column.  In the one-field web form
        # the unmarked segment immediately before the street is its equivalent.
        if not locality:
            locality = part
    address = ", ".join(parts[address_start:]) if address_start is not None else ""
    return region, locality, address
