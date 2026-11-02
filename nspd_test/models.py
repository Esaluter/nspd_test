from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RejectedCandidate:
    cadastral_number: str
    address: str
    reason: str
    source_street: str = ""
    candidate_street: str = ""
    source_house: str = ""
    candidate_house: str = ""
    category_name: str = ""
    cadastral_quarter: str = ""
    score: int | float | None = None
    checks: dict[str, str] = field(default_factory=dict)


@dataclass
class SearchAttempt:
    level: int
    query: str
    returned: int
    accepted: int
    rejected: int
    rejection_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class SearchResult:
    source_address: str
    status: str
    candidates_received: int
    candidates_accepted: int
    candidates: list[dict[str, Any]]
    rejected: list[RejectedCandidate]
    attempts: list[SearchAttempt]
    duration_ms: int
    ambiguity_reason: str = ""
    error: str = ""
    diagnostic_file: str = ""
    error_traceback: str = field(default="", repr=False)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("error_traceback", None)
        return result


@dataclass(frozen=True)
class ProbeResult:
    stage: str
    status: str
    duration_ms: int
    result: str = ""
    error: str = ""
    http_status: int | None = None

