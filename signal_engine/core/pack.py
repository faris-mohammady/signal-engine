"""Company pack schema and loader.

A company pack is packs/<slug>/pack.yaml plus optional plugins/ and lists/ folders.
Everything company-specific lives here; the engine code never changes per company.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_PACKS_DIR = Path("packs")


class Company(BaseModel):
    name: str
    website: str = ""
    product_summary: str


class Criterion(BaseModel):
    name: str
    description: str
    weight: float = 1.0
    keywords: list[str] = Field(default_factory=list, description="Used only when no LLM key is set")


class ICP(BaseModel):
    criteria: list[Criterion]
    exclusions: list[str] = Field(default_factory=list)
    exclusion_keywords: list[str] = Field(default_factory=list)

    @field_validator("criteria")
    @classmethod
    def _non_empty(cls, v: list[Criterion]) -> list[Criterion]:
        if not v:
            raise ValueError("icp.criteria needs at least one criterion")
        return v


class SourceConfig(BaseModel):
    """One discovery source. Extra keys are passed to the plugin as settings."""

    model_config = ConfigDict(extra="allow")
    source: str

    @property
    def settings(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class SignalConfig(BaseModel):
    """One signal type. Extra keys are passed to the plugin as settings."""

    model_config = ConfigDict(extra="allow")
    plugin: str
    weight: float = Field(gt=0, le=100)
    decay_days: float = Field(default=90, gt=0, description="Half-life in days")

    @property
    def settings(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class PlayConfig(BaseModel):
    name: str
    type: str
    description: str = ""
    when: dict[str, Any] = Field(default_factory=dict)
    assets: list[str] = Field(default_factory=list)
    channel: str | None = None
    budget_usd: float | None = None


class Scoring(BaseModel):
    fit_weight: float = 0.5
    timing_weight: float = 0.5
    threshold: float = 60

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "Scoring":
        if abs(self.fit_weight + self.timing_weight - 1.0) > 1e-6:
            raise ValueError("scoring.fit_weight + scoring.timing_weight must equal 1")
        return self


class Shortlist(BaseModel):
    max_accounts_per_week: int = Field(default=10, gt=0)


class Destinations(BaseModel):
    console: bool = True
    slack: bool = False


class Limits(BaseModel):
    max_accounts: int = 200
    max_llm_calls: int = 300
    page_cache_days: int = 7


class Pack(BaseModel):
    company: Company
    seed_customers: list[str] = Field(default_factory=list)
    icp: ICP
    discovery: list[SourceConfig]
    signals: dict[str, SignalConfig] = Field(default_factory=dict)
    personas: list[str] = Field(default_factory=list)
    messaging: dict[str, Any] = Field(default_factory=dict)
    plays: list[PlayConfig] = Field(default_factory=list)
    scoring: Scoring = Field(default_factory=Scoring)
    shortlist: Shortlist = Field(default_factory=Shortlist)
    destinations: Destinations = Field(default_factory=Destinations)
    limits: Limits = Field(default_factory=Limits)

    # Set by the loader, not by the YAML
    slug: str = ""
    root: Path = Path(".")

    @field_validator("discovery")
    @classmethod
    def _has_source(cls, v: list[SourceConfig]) -> list[SourceConfig]:
        if not v:
            raise ValueError("discovery needs at least one source")
        return v

    @property
    def sample_dir(self) -> Path:
        return self.root / "sample"


class PackError(Exception):
    pass


def resolve_pack_dir(name_or_path: str, packs_dir: Path = DEFAULT_PACKS_DIR) -> Path:
    candidate = Path(name_or_path)
    if candidate.is_dir() and (candidate / "pack.yaml").exists():
        return candidate
    candidate = packs_dir / name_or_path
    if (candidate / "pack.yaml").exists():
        return candidate
    raise PackError(f"No pack.yaml found for '{name_or_path}' (looked in {packs_dir}/)")


def load_pack(name_or_path: str, packs_dir: Path = DEFAULT_PACKS_DIR) -> Pack:
    pack_dir = resolve_pack_dir(name_or_path, packs_dir)
    with open(pack_dir / "pack.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    try:
        pack = Pack.model_validate(raw)
    except Exception as exc:  # pydantic gives a readable message
        raise PackError(f"Invalid pack '{pack_dir.name}':\n{exc}") from exc
    pack.slug = pack_dir.name
    pack.root = pack_dir.resolve()
    return pack


def list_packs(packs_dir: Path = DEFAULT_PACKS_DIR) -> list[str]:
    if not packs_dir.exists():
        return []
    return sorted(p.name for p in packs_dir.iterdir() if (p / "pack.yaml").exists())
