"""End-to-end run on the bundled sample data: no network, no keys."""

from datetime import timedelta
from pathlib import Path

from signal_engine.core import pipeline
from signal_engine.core.models import CriterionResult, FitResult
from signal_engine.core.pack import load_pack
from signal_engine.core.pipeline import RankedAccount, run_pack, select_shortlist
from signal_engine.core.store import ScoreRow, SignalRow, make_session_factory

PACKS = Path(__file__).resolve().parents[1] / "packs"


def run(tmp_path):
    sf = make_session_factory(f"sqlite:///{tmp_path / 'test.db'}")
    return sf, run_pack(load_pack("job-search", PACKS), sf, offline=True)


def test_offline_run_ranks_and_shortlists(tmp_path):
    _, result = run(tmp_path)
    by_domain = {r.domain: r for r in result.ranked}

    assert result.ranked[0].domain == "pipelinepilot.example"
    assert by_domain["pipelinepilot.example"].shortlisted
    assert by_domain["ledgerloop.example"].contributions, "Lever postings should produce signals"

    assert by_domain["snackdash.example"].fit.excluded          # consumer app
    assert by_domain["brightdesk.example"].fit.excluded         # agency
    assert not by_domain["oldemployer.example"].shortlisted     # suppressed
    assert len(result.shortlist) <= result.pack.shortlist.max_accounts_per_week
    assert all(r.total >= result.pack.scoring.threshold or r.play.when.get("no_timing_signal")
               for r in result.shortlist)


def test_rerun_is_idempotent(tmp_path):
    sf, _ = run(tmp_path)
    with sf() as s:
        first = s.query(SignalRow).count()
    run_pack(load_pack("job-search", PACKS), sf, offline=True)
    with sf() as s:
        assert s.query(SignalRow).count() == first      # no duplicate signals
        assert s.query(ScoreRow).count() == 2 * 7       # score history kept per run


def test_careers_page_signal_keeps_first_seen_date(tmp_path):
    sf, first = run(tmp_path)
    first_today = first.today
    later = first_today + timedelta(days=30)
    orig = pipeline._offline_today
    pipeline._offline_today = lambda pack: later
    try:
        second = run_pack(load_pack("job-search", PACKS), sf, offline=True)
    finally:
        pipeline._offline_today = orig
    fw = {r.domain: r for r in second.ranked}["fieldwise.example"]
    assert all(c.signal.observed_at == first_today for c in fw.contributions)
    assert "(30d ago" in fw.explanation
    assert fw.timing < {r.domain: r for r in first.ranked}["fieldwise.example"].timing


def _ranked(pack, name, total, play_name, suppressed=False):
    fit = FitResult(total * 2, False, [CriterionResult("x", "met", "")], "", "keywords")
    play = next(p for p in pack.plays if p.name == play_name) if play_name else None
    return RankedAccount(name, f"{name}.example", fit, 0.0, total, [], "", play, suppressed=suppressed)


def test_warm_intro_accounts_can_be_shortlisted_below_threshold():
    pack = load_pack("job-search", PACKS)
    hot = _ranked(pack, "hot", 70, "mini_pack")
    warm = _ranked(pack, "warm", 45, "warm_intro")
    cold = _ranked(pack, "cold", 45, None)
    suppressed = _ranked(pack, "old", 50, "warm_intro", suppressed=True)
    ranked = [hot, suppressed, warm, cold]
    select_shortlist(ranked, pack, limit=5)
    assert [r.name for r in ranked if r.shortlisted] == ["hot", "warm"]

    for r in ranked:
        r.shortlisted = False
    select_shortlist(ranked, pack, limit=1)          # signal accounts rank first
    assert [r.name for r in ranked if r.shortlisted] == ["hot"]
