"""Print a run as a ranked table plus a brief for each shortlisted account."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from signal_engine.core.pipeline import RunResult

console = Console()


def print_run(result: RunResult, show_all: bool = False) -> None:
    pack = result.pack
    table = Table(title=f"{pack.company.name} - run {result.run_id} ({result.today})", show_lines=False)
    for col, kw in [("#", {"justify": "right"}), ("Account", {}), ("Fit", {"justify": "right"}),
                    ("Timing", {"justify": "right"}), ("Total", {"justify": "right"}), ("Status", {}),
                    ("Play", {})]:
        table.add_column(col, **kw)

    rows = result.ranked if show_all else [r for r in result.ranked if r.shortlisted] or result.ranked
    for i, r in enumerate(rows, 1):
        if r.shortlisted:
            status = "[green]shortlist[/green]"
        elif r.fit.excluded:
            status = "[red]excluded[/red]"
        elif r.suppressed:
            status = "[yellow]suppressed[/yellow]"
        else:
            status = "watch"
        table.add_row(str(i), f"{r.name}\n[dim]{r.domain}[/dim]", f"{r.fit.score:.0f}", f"{r.timing:.0f}",
                      f"[bold]{r.total:.0f}[/bold]", status, r.play.name if r.play else "-")
    console.print(table)

    for r in result.shortlist:
        console.rule(f"[bold]{r.name}[/bold]  ({r.total:.0f})")
        console.print(r.explanation)
        for c in r.contributions[:3]:
            console.print(f"  [dim]source:[/dim] {c.signal.source_url}")
        if r.fit.source_urls:
            console.print(f"  [dim]fit read from:[/dim] {', '.join(r.fit.source_urls)}")
        if r.play:
            console.print(f"  [bold]Play:[/bold] {r.play.name} - {r.play.description or r.play.type}")

    console.print(
        f"\n[dim]{len(result.ranked)} accounts scored, {len(result.shortlist)} shortlisted "
        f"(threshold {pack.scoring.threshold:.0f}, max {pack.shortlist.max_accounts_per_week}) | "
        f"fit method: {result.fit_method} | pages fetched: {result.pages_fetched} | "
        f"LLM calls: {result.llm_calls}[/dim]"
    )
    if result.errors:
        console.print(f"[yellow]{len(result.errors)} warning(s):[/yellow]")
        for e in result.errors[:10]:
            console.print(f"  [yellow]-[/yellow] {e}")
