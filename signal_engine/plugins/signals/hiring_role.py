"""Signal plugin: the account is hiring for roles that matter to the seller.

Uses public job-board APIs when the account row says which ATS it uses
(columns `ats` = greenhouse | lever | ashby and `ats_slug`), otherwise reads the
careers page and looks for matching titles (lower confidence, no posting date).

One plugin, many signal types: configure it several times with different titles.

pack.yaml:
  signals:
    gtm_role_open:
      plugin: hiring_role
      weight: 40
      decay_days: 45
      titles: [gtm engineer, revenue operations]
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from signal_engine.core.models import Signal
from signal_engine.core.registry import signal

CAREERS_PATHS = ["/careers", "/jobs"]
MAX_SIGNALS_PER_TYPE = 3


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _jobs_from_ats(ats: str, slug: str, fetcher) -> list[dict]:
    """Normalize postings to {title, url, posted}."""
    ats = ats.lower()
    if ats == "greenhouse":
        data = fetcher.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs") or {}
        return [
            {"title": j.get("title", ""), "url": j.get("absolute_url", ""), "posted": _parse_date(j.get("updated_at"))}
            for j in data.get("jobs", [])
        ]
    if ats == "lever":
        data = fetcher.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json") or []
        return [
            {"title": j.get("text", ""), "url": j.get("hostedUrl", ""), "posted": _parse_date(j.get("createdAt"))}
            for j in data
        ]
    if ats == "ashby":
        data = fetcher.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}") or {}
        return [
            {
                "title": j.get("title", ""),
                "url": j.get("jobUrl") or j.get("applyUrl", ""),
                "posted": _parse_date(j.get("publishedAt") or j.get("publishedDate")),
            }
            for j in data.get("jobs", [])
        ]
    return []


def _title_matches(title: str, wanted: list[str]) -> bool:
    t = title.lower()
    return any(re.search(rf"\b{re.escape(w.lower())}\b", t) for w in wanted)


@signal("hiring_role")
class HiringRole:
    def detect(self, account, pack, signal_type: str, config, ctx) -> list[Signal]:
        titles = config.settings.get("titles") or []
        if not titles:
            raise ValueError(f"signal '{signal_type}': hiring_role needs a 'titles' list")

        ats, slug = account.attrs.get("ats", ""), account.attrs.get("ats_slug", "")
        found: list[Signal] = []

        if ats and slug:
            for job in _jobs_from_ats(ats, slug, ctx.fetcher):
                if job["url"] and _title_matches(job["title"], titles):
                    found.append(
                        Signal(
                            type=signal_type,
                            summary=f"Hiring: {job['title']}",
                            source_url=job["url"],
                            observed_at=job["posted"] or ctx.today,
                            confidence=0.95 if job["posted"] else 0.8,
                            raw={"ats": ats, "title": job["title"]},
                        )
                    )
        else:
            for path in CAREERS_PATHS:
                url = f"https://{account.domain}{path}"
                page = ctx.fetcher.get_page(url)
                if not page:
                    continue
                for line in page.splitlines():
                    line = line.strip(" #*-\t")
                    if 3 < len(line) < 120 and _title_matches(line, titles):
                        found.append(
                            Signal(
                                type=signal_type,
                                summary=f"Hiring (careers page): {line}",
                                source_url=url,
                                observed_at=ctx.today,
                                confidence=0.6,
                                raw={"title": line},
                            )
                        )
                break

        found.sort(key=lambda s: s.observed_at, reverse=True)
        unique, seen = [], set()
        for s in found:
            key = (s.summary, s.source_url)
            if key not in seen:
                seen.add(key)
                unique.append(s)
        return unique[:MAX_SIGNALS_PER_TYPE]
