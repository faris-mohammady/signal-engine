"""The run: discover -> fit check -> signals -> score -> shortlist -> play -> deliver."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select

from signal_engine.plugins.enrich import website_classifier

from . import registry
from .context import RunContext
from .fetch import Fetcher
from .llm import LLM
from .models import AccountCandidate, FitResult, Signal, normalize_domain
from .pack import Pack, PlayConfig
from .plays import choose_play
from .scoring import Contribution, explain, timing_score, total_score
from .store import Account, Run, ScoreRow, SignalRow


@dataclass
class RankedAccount:
    name: str
    domain: str
    fit: FitResult
    timing: float
    total: float
    contributions: list[Contribution]
    explanation: str
    play: PlayConfig | None
    suppressed: bool = False
    shortlisted: bool = False

    @property
    def eligible(self) -> bool:
        return not self.fit.excluded and not self.suppressed


@dataclass
class RunResult:
    run_id: int
    pack: Pack
    today: date
    ranked: list[RankedAccount] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    llm_calls: int = 0
    fit_method: str = ""

    @property
    def shortlist(self) -> list[RankedAccount]:
        return [r for r in self.ranked if r.shortlisted]


def _offline_today(pack: Pack) -> date:
    """Offline samples carry a fixed 'today' so results are reproducible."""
    meta = pack.sample_dir / "meta.json"
    if meta.exists():
        return date.fromisoformat(json.loads(meta.read_text())["today"])
    return date.today()


def discover(pack: Pack, ctx: RunContext) -> list[AccountCandidate]:
    seen: dict[str, AccountCandidate] = {}
    for src in pack.discovery:
        try:
            for cand in registry.get_source(src.source).discover(pack, src.settings, ctx):
                seen.setdefault(cand.domain, cand)  # first source wins on duplicates
        except Exception as exc:
            ctx.errors.append(f"source {src.source}: {exc}")
    return list(seen.values())[: pack.limits.max_accounts]


def _upsert_account(session, pack: Pack, cand: AccountCandidate) -> Account:
    acct = session.scalar(select(Account).where(Account.pack == pack.slug, Account.domain == cand.domain))
    if acct is None:
        acct = Account(pack=pack.slug, domain=cand.domain, name=cand.name, source=cand.source, attrs=cand.attrs)
        session.add(acct)
    else:
        acct.name, acct.attrs = cand.name, {**(acct.attrs or {}), **cand.attrs}
    acct.parent_domain = normalize_domain(cand.attrs["parent_domain"]) if cand.attrs.get("parent_domain") else None
    acct.suppressed = str(cand.attrs.get("suppressed", "")).lower() in ("1", "true", "yes")
    session.flush()
    return acct


def _store_signals(session, acct: Account, signals: list[Signal]) -> None:
    """Save new signals and backdate repeats to when they were first seen.

    Sources without a posting date (e.g. careers pages) report today on every run;
    keeping the first-seen date lets those signals decay like dated ones.
    """
    existing = {(s.type, s.source_url, s.summary): s for s in acct.signals}
    for s in signals:
        key = (s.type, s.source_url, s.summary)
        row = existing.get(key)
        if row is not None:
            if row.observed_at < s.observed_at:
                s.observed_at = row.observed_at
            continue
        row = SignalRow(account_id=acct.id, type=s.type, summary=s.summary, source_url=s.source_url,
                        observed_at=s.observed_at, confidence=s.confidence, raw=s.raw)
        existing[key] = row
        session.add(row)


def select_shortlist(ranked: list[RankedAccount], pack: Pack, limit: int) -> None:
    """Mark the top eligible accounts as shortlisted (ranked must be sorted by total).

    Accounts matched to a no-timing-signal play (e.g. warm_intro) skip the score
    threshold: with no timing their total is capped at fit_weight * 100, which can sit
    below the threshold, and the play's own fit_min is the bar that matters for them.
    """
    def passes(r: RankedAccount) -> bool:
        if r.total >= pack.scoring.threshold:
            return True
        return bool(r.play and r.play.when.get("no_timing_signal"))

    for r in [r for r in ranked if r.eligible and passes(r)][:limit]:
        r.shortlisted = True


def run_pack(pack: Pack, session_factory, offline: bool = False, top: int | None = None) -> RunResult:
    registry.load_builtin_plugins()
    registry.load_pack_plugins(pack.root)

    today = _offline_today(pack) if offline else date.today()
    fetcher = Fetcher(session_factory, offline=offline, sample_dir=pack.sample_dir,
                      cache_days=pack.limits.page_cache_days)
    llm = LLM(offline=offline, max_calls=pack.limits.max_llm_calls)
    ctx = RunContext(fetcher=fetcher, llm=llm, today=today, offline=offline)
    limit = top or pack.shortlist.max_accounts_per_week

    with session_factory() as session:
        run = Run(pack=pack.slug, offline=offline)
        session.add(run)
        session.commit()
        result = RunResult(run_id=run.id, pack=pack, today=today, fit_method="llm" if llm.available else "keywords")

        for cand in discover(pack, ctx):
            acct = _upsert_account(session, pack, cand)
            session.commit()  # release the write lock before plugins fetch (the page cache writes too)
            fit = website_classifier.classify(cand.name, cand.domain, pack, ctx)

            signals: list[Signal] = []
            for sig_type, cfg in pack.signals.items():
                try:
                    signals += registry.get_signal(cfg.plugin).detect(acct, pack, sig_type, cfg, ctx)
                except Exception as exc:
                    ctx.errors.append(f"signal {sig_type} for {cand.domain}: {exc}")
            _store_signals(session, acct, signals)

            timing, contributions = timing_score(signals, pack, today)
            total = total_score(fit, timing, pack)
            play = choose_play(pack, fit, signals, today)
            ranked = RankedAccount(cand.name, cand.domain, fit, timing, total, contributions,
                                   explain(fit, timing, total, contributions, today), play,
                                   suppressed=acct.suppressed)
            result.ranked.append(ranked)
            session.add(
                ScoreRow(run_id=run.id, account_id=acct.id, fit=fit.score, timing=timing, total=total,
                         excluded=fit.excluded, explanation=ranked.explanation,
                         fit_detail={"method": fit.method, "summary": fit.summary, "sources": fit.source_urls,
                                     "criteria": [c.__dict__ for c in fit.criteria]},
                         play=play.name if play else None)
            )
            session.commit()

        result.ranked.sort(key=lambda r: r.total, reverse=True)
        select_shortlist(result.ranked, pack, limit)

        shortlisted_domains = {r.domain for r in result.shortlist}
        for row in session.scalars(select(ScoreRow).where(ScoreRow.run_id == run.id)):
            row.shortlisted = row.account.domain in shortlisted_domains

        result.errors = ctx.errors + fetcher.errors
        result.pages_fetched, result.llm_calls = fetcher.pages_fetched, llm.calls
        run.accounts, run.shortlisted = len(result.ranked), len(result.shortlist)
        run.pages_fetched, run.llm_calls, run.errors = result.pages_fetched, result.llm_calls, result.errors
        run.finished_at = datetime.now(timezone.utc)
        session.commit()

    fetcher.close()
    return result
