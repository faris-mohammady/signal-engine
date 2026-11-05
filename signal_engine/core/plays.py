"""Pick the recommended play for an account from the pack's play library.

The first play whose `when` conditions all hold wins, so order plays from most to
least specific in pack.yaml. Supported conditions:
  signal: <type>            the account has this signal type
  max_age_days: N           ...and its newest signal of that type is at most N days old
  min_confidence: X         ...and that signal's confidence is at least X
  fit_min: N                fit score is at least N
  no_timing_signal: true    the account has no signals at all
Asset generation for each play is the next milestone; v0 recommends the play.
"""

from __future__ import annotations

from datetime import date

from .models import FitResult, Signal
from .pack import Pack, PlayConfig


def _matches(play: PlayConfig, fit: FitResult, signals: list[Signal], today: date) -> bool:
    when = play.when
    if "fit_min" in when and fit.score < float(when["fit_min"]):
        return False
    if when.get("no_timing_signal") and signals:
        return False
    if "signal" in when:
        of_type = [s for s in signals if s.type == when["signal"]]
        if not of_type:
            return False
        newest = max(of_type, key=lambda s: s.observed_at)
        if "max_age_days" in when and (today - newest.observed_at).days > int(when["max_age_days"]):
            return False
        if "min_confidence" in when and newest.confidence < float(when["min_confidence"]):
            return False
    return True


def choose_play(pack: Pack, fit: FitResult, signals: list[Signal], today: date) -> PlayConfig | None:
    if fit.excluded:
        return None
    for play in pack.plays:
        if _matches(play, fit, signals, today):
            return play
    return None
