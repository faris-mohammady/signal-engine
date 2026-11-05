"""Post the weekly shortlist to Slack through an incoming webhook (SLACK_WEBHOOK_URL)."""

from __future__ import annotations

import os

import httpx

from signal_engine.core.pipeline import RunResult


def build_blocks(result: RunResult) -> list[dict]:
    pack = result.pack
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{pack.company.name}: this week's shortlist"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"{len(result.shortlist)} of {len(result.ranked)} accounts made the cut (threshold "
            f"{pack.scoring.threshold:.0f}). Run {result.run_id}, {result.today}."}]},
    ]
    for r in result.shortlist:
        sources = "\n".join(f"<{c.signal.source_url}|{c.signal.summary}>" for c in r.contributions[:3])
        play = f"\n*Play:* {r.play.name} - {r.play.description or r.play.type}" if r.play else ""
        blocks += [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                f"*{r.name}* (`{r.domain}`) - *{r.total:.0f}*\n{r.explanation}{play}"}},
        ]
        if sources:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": sources}]})
    return blocks[:50]  # Slack's per-message block limit


def post_run(result: RunResult, webhook_url: str | None = None) -> bool:
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL", "")
    if not url:
        return False
    resp = httpx.post(url, json={"text": f"{result.pack.company.name} shortlist", "blocks": build_blocks(result)},
                      timeout=20)
    resp.raise_for_status()
    return True
