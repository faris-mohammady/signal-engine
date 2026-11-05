"""Plain data objects passed between plugins and the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


def normalize_domain(value: str) -> str:
    """'https://www.Example.com/about' -> 'example.com'."""
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0]
    if value.startswith("www."):
        value = value[4:]
    return value


@dataclass
class AccountCandidate:
    name: str
    domain: str
    source: str
    attrs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.domain = normalize_domain(self.domain)
        if not self.domain:
            raise ValueError(f"Account '{self.name}' has no domain")


@dataclass
class Signal:
    type: str
    summary: str
    source_url: str
    observed_at: date
    confidence: float = 1.0
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_url:
            raise ValueError("Signal requires a source_url: no URL, no signal")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass
class CriterionResult:
    name: str
    verdict: str  # "met" | "partial" | "not_met"
    evidence: str
    weight: float = 1.0

    @property
    def points(self) -> float:
        return {"met": 1.0, "partial": 0.5}.get(self.verdict, 0.0)


@dataclass
class FitResult:
    score: float  # 0-100
    excluded: bool
    criteria: list[CriterionResult]
    summary: str
    method: str  # "llm" | "keywords"
    source_urls: list[str] = field(default_factory=list)
