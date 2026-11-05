"""Fetcher behavior with a fake network: fallback order, 429 retries, no cached failures."""

import httpx

from signal_engine.core import fetch as fetch_mod
from signal_engine.core.fetch import Fetcher
from signal_engine.core.store import make_session_factory

LONG_PAGE = "<html><body>" + "<p>B2B platform for sales teams. Book a demo.</p>" * 30 + "</body></html>"


def make_fetcher(tmp_path, handler, monkeypatch, firecrawl_key="fc-test"):
    monkeypatch.setenv("FIRECRAWL_API_KEY", firecrawl_key)
    monkeypatch.setenv("FIRECRAWL_MIN_INTERVAL", "0")
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    f = Fetcher(make_session_factory(f"sqlite:///{tmp_path / 'f.db'}"))
    f._http = httpx.Client(transport=httpx.MockTransport(handler))
    return f


def test_plain_http_used_first_when_page_is_good(tmp_path, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(200, text=LONG_PAGE)

    f = make_fetcher(tmp_path, handler, monkeypatch)
    assert "Book a demo" in f.get_page("https://acme.com")
    assert "api.firecrawl.dev" not in calls


def test_firecrawl_retries_after_429(tmp_path, monkeypatch):
    fc_attempts = []

    def handler(req):
        if req.url.host == "api.firecrawl.dev":
            fc_attempts.append(1)
            if len(fc_attempts) == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return httpx.Response(200, json={"data": {"markdown": "# Acme\nRendered by Firecrawl " * 30}})
        return httpx.Response(200, text="<html><body><div id=root></div></body></html>")  # JS-only shell

    f = make_fetcher(tmp_path, handler, monkeypatch)
    page = f.get_page("https://acme.com")
    assert "Rendered by Firecrawl" in page
    assert len(fc_attempts) == 2


def test_failures_are_not_cached(tmp_path, monkeypatch):
    state = {"up": False}

    def handler(req):
        if state["up"]:
            return httpx.Response(200, text=LONG_PAGE)
        return httpx.Response(503)

    f = make_fetcher(tmp_path, handler, monkeypatch, firecrawl_key="")
    assert f.get_page("https://acme.com") is None
    state["up"] = True
    assert f.get_page("https://acme.com")  # retried, not served from a cached failure
