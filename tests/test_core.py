from datetime import date
from pathlib import Path

import pytest

from signal_engine.core.models import CriterionResult, FitResult, Signal, normalize_domain
from signal_engine.core.pack import PackError, load_pack
from signal_engine.core.plays import choose_play
from signal_engine.core.scoring import decay, timing_score, total_score

PACKS = Path(__file__).resolve().parents[1] / "packs"
TODAY = date(2026, 11, 5)


@pytest.fixture
def pack():
    return load_pack("job-search", PACKS)


def fit(score=80.0, excluded=False):
    return FitResult(score, excluded, [CriterionResult("x", "met", "", 1.0)], "", "keywords")


def test_pack_loads(pack):
    assert pack.slug == "job-search"
    assert pack.signals["gtm_role_open"].settings["titles"]


def test_invalid_pack_is_rejected(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "pack.yaml").write_text("company: {name: X, product_summary: y}\n")
    with pytest.raises(PackError):
        load_pack("bad", tmp_path)


def test_normalize_domain():
    assert normalize_domain("https://www.Example.com/about") == "example.com"


def test_signal_requires_source():
    with pytest.raises(ValueError):
        Signal("t", "s", "", TODAY)


def test_decay_half_life():
    assert decay(0, 45) == 1.0
    assert decay(45, 45) == pytest.approx(0.5)


def test_timing_uses_strongest_signal_per_type(pack):
    sigs = [
        Signal("gtm_role_open", "a", "u1", TODAY, 1.0),
        Signal("gtm_role_open", "b", "u2", TODAY, 1.0),  # same type: must not add up
        Signal("sales_buildout", "c", "u3", TODAY, 1.0),
    ]
    timing, contribs = timing_score(sigs, pack, TODAY)
    assert timing == pytest.approx(40 + 20)
    assert len(contribs) == 2


def test_timing_caps_at_100(pack):
    pack.signals["gtm_role_open"].weight = 90
    pack.signals["sales_buildout"].weight = 90
    sigs = [Signal("gtm_role_open", "a", "u", TODAY), Signal("sales_buildout", "b", "u", TODAY)]
    assert timing_score(sigs, pack, TODAY)[0] == 100


def test_excluded_scores_zero(pack):
    assert total_score(fit(100, excluded=True), 100, pack) == 0


def test_play_selection_order(pack):
    gtm = [Signal("gtm_role_open", "a", "u", TODAY)]
    sales = [Signal("sales_buildout", "b", "u", TODAY)]
    assert choose_play(pack, fit(), gtm + sales, TODAY).name == "mini_pack"
    assert choose_play(pack, fit(), sales, TODAY).name == "gtm_teardown"
    assert choose_play(pack, fit(75), [], TODAY).name == "warm_intro"
    assert choose_play(pack, fit(50), [], TODAY) is None


class FakeLLM:
    available = True
    calls = 0

    def complete_json(self, system, prompt, max_tokens=1500):
        return {
            "criteria": [
                {"name": "B2B software", "verdict": "met", "evidence": "platform for B2B sales teams"},
                {"name": "Sales-led motion", "verdict": "partial", "evidence": "mentions demos"},
                {"name": "Growth stage", "verdict": "bogus", "evidence": ""},
            ],
            "excluded": False,
            "summary": "Revenue intelligence for sales teams",
        }


def test_llm_classifier_scores_and_sanitizes(pack):
    from types import SimpleNamespace

    from signal_engine.plugins.enrich.website_classifier import classify_with_llm

    ctx = SimpleNamespace(llm=FakeLLM())
    result = classify_with_llm("Acme", "page text", pack, ctx)
    verdicts = {c.name: c.verdict for c in result.criteria}
    assert verdicts["B2B software"] == "met"
    assert verdicts["Growth stage"] == "not_met"      # invalid verdict is downgraded
    assert verdicts["Location fit"] == "not_met"      # missing criterion counts as not met
    # weights: 1.5 met + 1.5 * 0.5 partial over total weight 5.5
    assert result.score == round(100 * (1.5 + 0.75) / 5.5, 1)


def test_parse_json_handles_fences():
    from signal_engine.core.llm import parse_json

    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_llm_retries_once_on_invalid_json():
    from types import SimpleNamespace

    from signal_engine.core.llm import LLM

    replies = iter(['{"a": "he said "hi""}', '{"a": "he said \'hi\'"}'])
    seen = []

    class FakeMessages:
        def create(self, **kw):
            seen.append(kw["messages"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=next(replies))])

    llm = LLM(offline=True)
    llm.provider, llm.model = "anthropic", "test"
    llm._client = SimpleNamespace(messages=FakeMessages())
    assert llm.complete_json("sys", "prompt") == {"a": "he said 'hi'"}
    assert llm.calls == 2
    assert "not valid JSON" in seen[1][-1]["content"]
