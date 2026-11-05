"""Command line: signal-engine run | packs | validate | log | outcomes."""

from __future__ import annotations

from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from signal_engine.core.pack import DEFAULT_PACKS_DIR, PackError, list_packs, load_pack
from signal_engine.core.pipeline import run_pack
from signal_engine.core.store import Account, Action, make_session_factory
from signal_engine.delivery.console import print_run
from signal_engine.delivery.slack import post_run

app = typer.Typer(help="Find, score and time the few accounts worth real effort this week.", no_args_is_help=True)
console = Console()

OUTCOMES = ["play_run", "replied", "meeting", "referral", "no_response", "skip"]


@app.callback()
def _setup() -> None:
    load_dotenv()


@app.command()
def run(
    pack: str = typer.Option(..., "--pack", "-p", help="Pack folder name under packs/ or a path"),
    offline: bool = typer.Option(False, help="Use the pack's bundled sample data; no network, no keys"),
    top: int = typer.Option(None, help="Override the pack's weekly shortlist size"),
    all_rows: bool = typer.Option(False, "--all", help="Show every scored account, not just the shortlist"),
    slack: bool = typer.Option(None, help="Post to Slack (default: the pack's destinations.slack)"),
    packs_dir: Path = typer.Option(DEFAULT_PACKS_DIR, help="Where packs live"),
) -> None:
    """Run a pack end to end and print the shortlist."""
    try:
        p = load_pack(pack, packs_dir)
    except PackError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    db = "sqlite:///signal_engine_offline.db" if offline else None
    result = run_pack(p, make_session_factory(db), offline=offline, top=top)
    if p.destinations.console:
        print_run(result, show_all=all_rows)
    if (slack if slack is not None else p.destinations.slack) and not offline:
        if post_run(result):
            console.print("[green]Posted to Slack.[/green]")
        else:
            console.print("[yellow]Slack skipped: SLACK_WEBHOOK_URL is not set.[/yellow]")


@app.command()
def packs(packs_dir: Path = typer.Option(DEFAULT_PACKS_DIR)) -> None:
    """List available packs."""
    names = list_packs(packs_dir)
    if not names:
        console.print(f"No packs found in {packs_dir}/")
    for name in names:
        p = load_pack(name, packs_dir)
        console.print(f"[bold]{name}[/bold] - {p.company.product_summary}")


@app.command()
def validate(pack: str = typer.Option(..., "--pack", "-p"), packs_dir: Path = typer.Option(DEFAULT_PACKS_DIR)) -> None:
    """Check a pack.yaml without running it."""
    try:
        p = load_pack(pack, packs_dir)
    except PackError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]OK[/green] {p.slug}: {len(p.icp.criteria)} ICP criteria, {len(p.discovery)} source(s), "
                  f"{len(p.signals)} signal type(s), {len(p.plays)} play(s)")


@app.command()
def log(
    domain: str = typer.Argument(..., help="Account domain, e.g. example.com"),
    outcome: str = typer.Argument(..., help=f"One of: {', '.join(OUTCOMES)}"),
    pack: str = typer.Option(..., "--pack", "-p"),
    play: str = typer.Option(None, help="Which play this was"),
    note: str = typer.Option("", help="Free-text note"),
) -> None:
    """Record what happened with an account (feeds the meetings-per-10-accounts metric)."""
    if outcome not in OUTCOMES:
        console.print(f"[red]Outcome must be one of {OUTCOMES}[/red]")
        raise typer.Exit(1)
    sf = make_session_factory()
    with sf() as s:
        acct = s.scalar(select(Account).where(Account.pack == pack, Account.domain == domain.lower()))
        if not acct:
            console.print(f"[red]No account {domain} in pack {pack}. Run the pack first.[/red]")
            raise typer.Exit(1)
        s.add(Action(account_id=acct.id, type=outcome, play=play, note=note))
        s.commit()
    console.print(f"Logged {outcome} for {domain}.")


@app.command()
def outcomes(pack: str = typer.Option(..., "--pack", "-p")) -> None:
    """Meetings per 10 accounts worked, overall and by play."""
    sf = make_session_factory()
    with sf() as s:
        rows = s.execute(
            select(Action.play, Action.type, func.count(func.distinct(Action.account_id)))
            .join(Account, Account.id == Action.account_id)
            .where(Account.pack == pack)
            .group_by(Action.play, Action.type)
        ).all()
    stats: dict[str, dict[str, int]] = {}
    for play, typ, n in rows:
        stats.setdefault(play or "(none)", {})[typ] = n
    table = Table(title=f"Outcomes - {pack}")
    for col in ["Play", "Accounts worked", "Replies", "Meetings", "Meetings per 10"]:
        table.add_column(col)
    for play, d in sorted(stats.items()):
        worked = d.get("play_run", 0)
        meetings = d.get("meeting", 0)
        per10 = f"{10 * meetings / worked:.1f}" if worked else "-"
        table.add_row(play, str(worked), str(d.get("replied", 0)), str(meetings), per10)
    console.print(table)


if __name__ == "__main__":
    app()
