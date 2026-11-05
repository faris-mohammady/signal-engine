# Signal Engine

> Your next best customers aren't missing. They're missing from your database — and they show up on a signal, not a list.

Signal Engine finds the few accounts worth real effort this week. It scores each one on **fit** (does it look like your best customers?) and **timing** (is something happening right now?), explains every score with sources, and recommends a creative **play** to run instead of another templated email.

One engine runs for any company. Everything company-specific lives in a **company pack**: one YAML file plus optional plugins.

## What's built (v0.1, runs end to end)

1. **Discover:** `csv_import` loads target accounts from a list.
2. **Fit check:** `website_classifier` reads each account's homepage and about page (Firecrawl, or plain HTTP) and scores it against the pack's ICP rubric with an LLM. Without an LLM key it falls back to keyword matching, so it still runs.
3. **Signals:** `hiring_role` reads public Greenhouse, Lever and Ashby job boards (or the careers page) for roles that matter. One plugin covers many signal types.
4. **Score:** fit × timing, with a half-life decay on every signal. Only the strongest signal per type counts.
5. **Shortlist:** only the top N accounts above the threshold move on. Excluded and suppressed accounts are scored but never shortlisted.
6. **Play:** the first play in the pack's library whose conditions match is recommended.
7. **Deliver:** a ranked table and a brief per account in the terminal, and optionally a Slack message.
8. **Learn:** `signal-engine log` records outcomes; `signal-engine outcomes` reports meetings per 10 accounts worked, by play.

Re-runs are idempotent (no duplicate signals), every signal must carry a source URL, and pages are cached so repeated runs don't re-fetch.

## Designed, not built yet

- Discovery plugins: Exa Websets, Clay lookalikes, Google Places
- More signals: news mentions, leadership changes, website changes (Firecrawl Search / Monitor)
- Play assets: generate the teardown, audit or brief for each shortlisted account
- Hidden-customer backtest, auto-onboarding from a URL, Streamlit review UI
- More demo packs: retail promo software, hospitality hiring, mainstream B2B SaaS

## Quickstart (no keys needed)

```bash
git clone <your-repo-url> signal-engine && cd signal-engine
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

signal-engine run --pack job-search --offline --all
pytest
```

The offline run uses fictional sample data in `packs/job-search/sample/`, so the output is the same on any machine.

## Live runs

1. `cp .env.example .env` and add what you have:
   - `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) for LLM fit scoring, then `pip install -e ".[anthropic]"` (or `.[openai]`)
   - `FIRECRAWL_API_KEY` for reliable page reading (free tier is enough to start)
   - `SLACK_WEBHOOK_URL` to post the shortlist to Slack
2. Replace `packs/job-search/lists/targets.csv` with your own targets. Columns:

   | Column | Required | Meaning |
   | --- | --- | --- |
   | `name`, `domain` | yes | The account |
   | `ats`, `ats_slug` | no | `greenhouse` / `lever` / `ashby` and the board name from the jobs URL (e.g. `boards.greenhouse.io/<slug>`) |
   | `parent_domain` | no | Rolls a location or subsidiary up to the company that buys |
   | `suppressed` | no | `true` = never shortlist (current employer, existing customer, opted out) |

3. Run it: `signal-engine run --pack job-search` (add `--slack` to post).

## The weekly loop

```bash
signal-engine run --pack job-search                     # Monday: get the shortlist
signal-engine log acme.com play_run --pack job-search --play mini_pack
signal-engine log acme.com meeting  --pack job-search --play mini_pack --note "call with Head of RevOps"
signal-engine outcomes --pack job-search                # Friday: which plays work
```

Outcomes: `play_run`, `replied`, `meeting`, `referral`, `no_response`, `skip`.

## Adding a company

Copy a pack and edit the YAML; no engine code changes:

```bash
cp -r packs/job-search packs/my-company
signal-engine validate --pack my-company
```

A pack defines: `company`, `icp` (weighted criteria + exclusions), `discovery` sources, `signals` (plugin, weight, half-life, settings), `plays` (ordered; first match wins), `scoring`, `shortlist`, `destinations` and `limits`. See `packs/job-search/pack.yaml` for a commented example.

## Writing a plugin

Built-in plugins live in `signal_engine/plugins/`. A pack can also ship its own in `packs/<pack>/plugins/*.py`; they load automatically.

```python
from signal_engine.core.models import Signal
from signal_engine.core.registry import signal

@signal("my_signal")
class MySignal:
    def detect(self, account, pack, signal_type, config, ctx) -> list[Signal]:
        page = ctx.fetcher.get_page(f"https://{account.domain}/news")
        ...
        return [Signal(type=signal_type, summary="...", source_url=url, observed_at=ctx.today, confidence=0.8)]
```

Source plugins use `@source("name")` and implement `discover(pack, settings, ctx) -> list[AccountCandidate]`.

## Layout

```
signal_engine/
  core/        pack schema, pipeline, scoring, plays, fetch (Firecrawl + cache), llm, store (SQLAlchemy)
  plugins/     sources/csv_import, enrich/website_classifier, signals/hiring_role
  delivery/    console, slack
  cli.py       run | packs | validate | log | outcomes
packs/
  job-search/  pack.yaml, lists/targets.csv, sample/ (offline fixtures)
tests/
```

## Scoring

```
timing = min(100, Σ over signal types of  weight × confidence × 0.5^(age_days / half_life))
total  = fit_weight × fit + timing_weight × timing      (0 if excluded)
```

All sample companies in this repo are fictional.
