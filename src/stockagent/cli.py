"""CLI entry point — the scheduled routine talks to this."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import click

from . import packet, state
from .portfolio import load_portfolio


@click.group()
@click.option(
    "--portfolio",
    "portfolio_path",
    default="portfolio.yaml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.pass_context
def main(ctx: click.Context, portfolio_path: str) -> None:
    """StockAgent — portfolio earnings briefings."""
    ctx.ensure_object(dict)
    ctx.obj["portfolio_path"] = portfolio_path


@main.command()
@click.option("--lookback-days", default=7, show_default=True, type=int)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit JSON instead of human-readable text."
)
@click.option(
    "--include-sent",
    is_flag=True,
    help="Include reports we've already emailed (for debugging).",
)
@click.pass_context
def check(ctx: click.Context, lookback_days: int, as_json: bool, include_sent: bool) -> None:
    """List portfolio holdings that have reported in the last N days and need a briefing."""
    p = load_portfolio(ctx.obj["portfolio_path"])
    pending = []
    for h in p.holdings:
        try:
            cand = packet.detect_recent_earnings(h, lookback_days=lookback_days)
        except Exception as e:  # noqa: BLE001 — yfinance can be flaky; never crash check
            click.echo(f"# {h.ticker}: error: {e}", err=True)
            continue
        if cand is None:
            continue
        already = state.has_been_sent(cand.holding.ticker, cand.earnings_date)
        if already and not include_sent:
            continue
        pending.append(
            {
                "ticker": cand.holding.ticker,
                "yahoo": cand.holding.yahoo,
                "earnings_date": cand.earnings_date.isoformat(),
                "source": cand.source,
                "already_sent": already,
                "eps_actual": cand.eps_actual,
                "eps_estimate": cand.eps_estimate,
                "surprise_pct": cand.surprise_pct,
                "filing_url": (cand.filing.primary_url if cand.filing else None),
            }
        )
    if as_json:
        click.echo(json.dumps(pending, indent=2))
        return
    if not pending:
        click.echo("No pending briefings.")
        return
    for x in pending:
        sent_marker = " [already sent]" if x["already_sent"] else ""
        eps = ""
        if x["eps_actual"] is not None:
            eps = f"  EPS {x['eps_actual']:.2f}"
            if x["eps_estimate"] is not None:
                eps += f" (est {x['eps_estimate']:.2f})"
            if x["surprise_pct"] is not None:
                eps += f", {x['surprise_pct']:+.1f}%"
        click.echo(f"{x['ticker']:<10} {x['earnings_date']}{eps}{sent_marker}")


@main.command()
@click.argument("ticker")
@click.option("--lookback-days", default=7, show_default=True, type=int)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, writable=True),
    help="Write packet to file (default: stdout).",
)
@click.pass_context
def gather(ctx: click.Context, ticker: str, lookback_days: int, out: str | None) -> None:
    """Build a briefing packet for TICKER (must be in portfolio.yaml)."""
    p = load_portfolio(ctx.obj["portfolio_path"])
    h = next((h for h in p.holdings if h.ticker.upper() == ticker.upper()), None)
    if h is None:
        click.echo(f"error: {ticker} not in portfolio", err=True)
        sys.exit(2)
    cand = packet.detect_recent_earnings(h, lookback_days=lookback_days)
    if cand is None:
        click.echo(f"error: no recent earnings detected for {ticker} in last {lookback_days}d", err=True)
        sys.exit(3)
    md = packet.build_packet(cand)
    if out:
        Path(out).write_text(md)
        click.echo(f"wrote {out}")
    else:
        click.echo(md)


@main.command("mark-sent")
@click.argument("ticker")
@click.argument("earnings_date")
@click.argument("source")
def mark_sent_cmd(ticker: str, earnings_date: str, source: str) -> None:
    """Record that a briefing was emailed.

    EARNINGS_DATE: YYYY-MM-DD
    SOURCE: SEC accession # or any URL identifying the report.
    """
    state.mark_sent(ticker, date.fromisoformat(earnings_date), source)
    click.echo(f"marked {ticker} {earnings_date} ({source}) as sent")


if __name__ == "__main__":
    main()
