"""Fit x timing scoring, with a plain-language explanation for every account.

timing = min(100, sum over signal types of the strongest signal's  w * c * 0.5 ** (age / half_life))
total  = fit_weight * fit + timing_weight * timing
Only the strongest signal per type counts, so ten job posts of one kind don't add up to 100.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import FitResult, Signal
from .pack import Pack


@dataclass
class Contribution:
    signal: Signal
    points: float


def decay(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def timing_score(signals: list[Signal], pack: Pack, today: date) -> tuple[float, list[Contribution]]:
    best: dict[str, Contribution] = {}
    for sig in signals:
        cfg = pack.signals.get(sig.type)
        if cfg is None:
            continue
        age = (today - sig.observed_at).days
        points = cfg.weight * sig.confidence * decay(age, cfg.decay_days)
        if sig.type not in best or points > best[sig.type].points:
            best[sig.type] = Contribution(sig, points)
    contributions = sorted(best.values(), key=lambda c: c.points, reverse=True)
    return min(100.0, sum(c.points for c in contributions)), contributions


def total_score(fit: FitResult, timing: float, pack: Pack) -> float:
    if fit.excluded:
        return 0.0
    return pack.scoring.fit_weight * fit.score + pack.scoring.timing_weight * timing


def explain(fit: FitResult, timing: float, total: float, contributions: list[Contribution], today: date) -> str:
    if fit.excluded:
        return f"Excluded: {fit.summary}"
    met = [c.name for c in fit.criteria if c.verdict == "met"]
    parts = [f"Score {total:.0f} - Fit {fit.score:.0f}"]
    parts.append(f" ({', '.join(met)})" if met else " (no criteria clearly met)")
    if contributions:
        why = "; ".join(
            f"{c.signal.summary} ({(today - c.signal.observed_at).days}d ago, +{c.points:.0f})"
            for c in contributions[:3]
        )
        parts.append(f". Timing {timing:.0f}: {why}")
    else:
        parts.append(". Timing 0: no recent signals")
    return "".join(parts)
