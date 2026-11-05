"""What every plugin receives: shared fetcher, LLM, the run date and an error log."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .fetch import Fetcher
from .llm import LLM


@dataclass
class RunContext:
    fetcher: Fetcher
    llm: LLM
    today: date
    offline: bool = False
    errors: list[str] = field(default_factory=list)
