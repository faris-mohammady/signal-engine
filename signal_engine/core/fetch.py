"""Web access for plugins: pages as markdown, JSON from public APIs.

Order of preference for pages: offline sample -> cache -> plain HTTP -> Firecrawl
(only when plain HTTP fails or returns a near-empty JavaScript page).
Successful pages are cached; failures are not, so the next run retries them.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .store import PageCache

USER_AGENT = "Mozilla/5.0 (compatible; signal-engine/0.1)"
TIMEOUT = 30.0
MAX_CHARS = 60_000
MIN_USEFUL_CHARS = 400          # less than this usually means a JavaScript-only page
FIRECRAWL_MAX_ATTEMPTS = 3


def fixture_name(url: str, suffix: str) -> str:
    """Stable file name for an offline sample of a URL."""
    stem = re.sub(r"[^a-z0-9]+", "_", url.lower().split("://", 1)[-1]).strip("_")
    return f"{stem}{suffix}"


class Fetcher:
    def __init__(self, session_factory, offline: bool = False, sample_dir: Path | None = None,
                 cache_days: int = 7) -> None:
        self.session_factory = session_factory
        self.offline = offline
        self.sample_dir = sample_dir
        self.cache_days = cache_days
        self.firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        self.firecrawl_url = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev").rstrip("/")
        self.pages_fetched = 0
        self.errors: list[str] = []
        self._json_memo: dict[str, object] = {}
        # Seconds between Firecrawl calls; raise it if you still see HTTP 429
        self._firecrawl_min_interval = float(os.getenv("FIRECRAWL_MIN_INTERVAL", "6"))
        self._last_firecrawl = 0.0
        self._http = None if offline else httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    # ---------- pages ----------

    def get_page(self, url: str) -> str | None:
        """Return the page as markdown-ish text, or None if unavailable."""
        if self.offline:
            return self._read_sample(fixture_name(url, ".md"))

        cached = self._cache_get(url)
        if cached:
            return cached

        # Plain HTTP first: free and fast. Firecrawl only when the page is missing or
        # too thin (JavaScript-rendered sites), which keeps credits and rate limits low.
        content = None
        try:
            content = self._plain_fetch(url)
        except Exception as exc:  # network errors should never kill a run
            self.errors.append(f"fetch {url}: {exc}")
        if (not content or len(content) < MIN_USEFUL_CHARS) and self.firecrawl_key:
            try:
                content = self._firecrawl_scrape(url) or content
            except Exception as exc:
                self.errors.append(f"firecrawl {url}: {exc}")
        self.pages_fetched += 1
        if content:  # never cache failures; a later run should retry them
            content = content[:MAX_CHARS]
            self._cache_put(url, content)
        return content or None

    def _firecrawl_scrape(self, url: str) -> str | None:
        """Scrape via Firecrawl, pacing requests and retrying politely on HTTP 429."""
        for attempt in range(FIRECRAWL_MAX_ATTEMPTS):
            wait = self._firecrawl_min_interval - (time.monotonic() - self._last_firecrawl)
            if wait > 0:
                time.sleep(wait)
            self._last_firecrawl = time.monotonic()
            resp = self._http.post(
                f"{self.firecrawl_url}/v2/scrape",
                headers={"Authorization": f"Bearer {self.firecrawl_key}"},
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            )
            if resp.status_code == 429 and attempt < FIRECRAWL_MAX_ATTEMPTS - 1:
                retry_after = resp.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 10.0 * (attempt + 1)
                time.sleep(min(delay, 60.0))
                continue
            if resp.status_code >= 400:
                self.errors.append(f"firecrawl {url}: HTTP {resp.status_code}")
                return None
            data = resp.json().get("data") or {}
            return data.get("markdown")
        return None

    def _plain_fetch(self, url: str) -> str | None:
        resp = self._http.get(url)
        if resp.status_code >= 400:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = soup.get_text("\n")
        return re.sub(r"\n\s*\n+", "\n\n", text).strip()

    # ---------- JSON APIs (job boards etc.) ----------

    def get_json(self, url: str):
        """Fetched at most once per run (several signal types often read the same job board)."""
        if url in self._json_memo:
            return self._json_memo[url]
        if self.offline:
            raw = self._read_sample(fixture_name(url, ".json"))
            data = json.loads(raw) if raw else None
        else:
            data = None
            try:
                resp = self._http.get(url, headers={"Accept": "application/json"})
                self.pages_fetched += 1
                if resp.status_code < 400:
                    data = resp.json()
            except Exception as exc:
                self.errors.append(f"json {url}: {exc}")
        self._json_memo[url] = data
        return data

    # ---------- helpers ----------

    def _read_sample(self, filename: str) -> str | None:
        if not self.sample_dir:
            return None
        path = self.sample_dir / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _cache_get(self, url: str) -> str | None:
        with self.session_factory() as s:
            row = s.get(PageCache, url)
            if not row:
                return None
            fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched > timedelta(days=self.cache_days):
                return None
            return row.content

    def _cache_put(self, url: str, content: str) -> None:
        with self.session_factory() as s:
            row = s.get(PageCache, url)
            if row:
                row.content, row.fetched_at = content, datetime.now(timezone.utc)
            else:
                s.add(PageCache(url=url, content=content))
            s.commit()

    def close(self) -> None:
        if self._http:
            self._http.close()
