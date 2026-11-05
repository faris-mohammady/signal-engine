"""Fit check: read an account's website and score it against the pack's ICP rubric.

With an LLM key, the model judges each criterion and must quote its evidence.
Without one, it falls back to the `keywords` listed on each criterion, so the
engine still runs (less accurately) with no keys at all.
"""

from __future__ import annotations

import json

from signal_engine.core.llm import BudgetExceeded
from signal_engine.core.models import CriterionResult, FitResult

ABOUT_PATHS = ["/about", "/company", "/about-us"]
MAX_PAGE_CHARS = 12_000

SYSTEM = (
    "You qualify B2B accounts against an ideal customer profile. Judge only from the page text "
    "provided. Never guess: if the text does not show a criterion, mark it not_met or partial. "
    "Every evidence string must quote or closely paraphrase the page. Respond with JSON only."
)


def read_site(domain: str, ctx) -> tuple[str, list[str]]:
    """Homepage plus the first about-style page that exists."""
    texts, urls = [], []
    home = f"https://{domain}"
    page = ctx.fetcher.get_page(home)
    if page:
        texts.append(page[:MAX_PAGE_CHARS])
        urls.append(home)
    for path in ABOUT_PATHS:
        url = home + path
        page = ctx.fetcher.get_page(url)
        if page:
            texts.append(page[:MAX_PAGE_CHARS])
            urls.append(url)
            break
    return "\n\n---\n\n".join(texts), urls


def _score(criteria: list[CriterionResult]) -> float:
    total_weight = sum(c.weight for c in criteria) or 1.0
    return round(100.0 * sum(c.points * c.weight for c in criteria) / total_weight, 1)


def classify_with_llm(name: str, text: str, pack, ctx) -> FitResult:
    rubric = [{"name": c.name, "description": c.description} for c in pack.icp.criteria]
    prompt = (
        f"Seller: {pack.company.name} - {pack.company.product_summary}\n\n"
        f"Account: {name}\n\n"
        f"ICP criteria:\n{json.dumps(rubric, indent=2)}\n\n"
        f"Exclusions (any one excludes the account): {json.dumps(pack.icp.exclusions)}\n\n"
        f"Website text:\n<<<\n{text}\n>>>\n\n"
        'Return: {"criteria": [{"name": str, "verdict": "met"|"partial"|"not_met", "evidence": str}], '
        '"excluded": bool, "exclusion_reason": str, "summary": str (one sentence on what the company does)}'
    )
    data = ctx.llm.complete_json(SYSTEM, prompt)
    by_name = {c.get("name"): c for c in data.get("criteria", [])}
    results = []
    for crit in pack.icp.criteria:
        got = by_name.get(crit.name, {})
        verdict = got.get("verdict", "not_met")
        if verdict not in ("met", "partial", "not_met"):
            verdict = "not_met"
        results.append(CriterionResult(crit.name, verdict, str(got.get("evidence", ""))[:300], crit.weight))
    excluded = bool(data.get("excluded"))
    summary = data.get("exclusion_reason") if excluded else data.get("summary", "")
    return FitResult(_score(results), excluded, results, summary or "", "llm")


def classify_with_keywords(text: str, pack) -> FitResult:
    lower = text.lower()
    results = []
    for crit in pack.icp.criteria:
        if not crit.keywords:
            results.append(CriterionResult(crit.name, "partial", "no keywords set; needs LLM to judge", crit.weight))
            continue
        hits = [k for k in crit.keywords if k.lower() in lower]
        verdict = "met" if hits else "not_met"
        evidence = f"matched: {', '.join(hits[:3])}" if hits else "no keyword match"
        results.append(CriterionResult(crit.name, verdict, evidence, crit.weight))
    excl_hits = [k for k in pack.icp.exclusion_keywords if k.lower() in lower]
    summary = f"matched exclusion keyword(s): {', '.join(excl_hits)}" if excl_hits else "keyword match only"
    return FitResult(_score(results), bool(excl_hits), results, summary, "keywords")


def classify(name: str, domain: str, pack, ctx) -> FitResult:
    text, urls = read_site(domain, ctx)
    if not text:
        criteria = [CriterionResult(c.name, "not_met", "website unreadable", c.weight) for c in pack.icp.criteria]
        return FitResult(0.0, False, criteria, "website could not be read", "none", [])
    result = None
    if ctx.llm.available:
        try:
            result = classify_with_llm(name, text, pack, ctx)
        except BudgetExceeded:
            ctx.errors.append("LLM budget reached; falling back to keywords")
        except Exception as exc:
            ctx.errors.append(f"classifier LLM error for {domain}: {exc}")
    if result is None:
        result = classify_with_keywords(text, pack)
    result.source_urls = urls
    return result
