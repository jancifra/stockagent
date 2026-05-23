"""Build a markdown 'briefing packet' the scheduled routine turns into an email.

The packet is the deterministic part: filings, numbers, analyst stats, links.
The routine reads it, opens the linked URLs via WebFetch to pull transcript /
press release / guidance details, and writes the actual briefing prose.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import StringIO

from . import edgar, price
from .portfolio import Holding


@dataclass
class CandidateReport:
    holding: Holding
    earnings_date: date
    source: str  # accession # for SEC; "yfinance" otherwise
    filing: edgar.Filing | None
    eps_estimate: float | None
    eps_actual: float | None
    surprise_pct: float | None


def detect_recent_earnings(
    holding: Holding, *, lookback_days: int = 7
) -> CandidateReport | None:
    """Return a CandidateReport if this holding reported earnings within the
    lookback window, else None.

    Detection prefers a 1-2 punch:
      1. yfinance earnings_dates row with a Reported EPS within the window
      2. SEC EDGAR earnings filing in the window (when sec=True)

    For non-SEC tickers (ADYEN.AS, MBG.DE), only path #1 is available.
    For SEC tickers, path #2 confirms there's actually a filing to read.
    """
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)
    # yfinance often has the *announce* date (earlier than the 10-Q filing date).
    # We accept a slightly wider yfinance window so the announce date anchors
    # post-earnings price moves correctly even when the 10-Q lags.
    yf_cutoff = today - timedelta(days=lookback_days + 10)

    rows = price.earnings_history(holding.yahoo, n=8)
    yf_row = None
    for r in rows:
        if r.eps_actual is None:
            continue
        if yf_cutoff <= r.date <= today:
            yf_row = r
            break

    filing: edgar.Filing | None = None
    if holding.sec:
        filing = edgar.latest_earnings_filing(holding.ticker, lookback_days=lookback_days)
        # 6-K is noisy (corporate actions, dividends, governance). Only trust it
        # as earnings if yfinance corroborates. 8-K/2.02, 10-Q, 10-K, 20-F are definitive.
        if filing is not None and filing.form == "6-K" and yf_row is None:
            filing = None

    # Require something in the inner lookback window — either a filing OR a yf
    # row no older than the inner cutoff. The wider yf window is for *anchoring*
    # earnings_date, not for triggering detection on its own.
    has_inner_signal = (filing is not None) or (yf_row is not None and yf_row.date >= cutoff)
    if not has_inner_signal:
        return None

    earnings_date = yf_row.date if yf_row else filing.filed_at  # type: ignore[union-attr]
    source = filing.accession if filing else "yfinance"

    return CandidateReport(
        holding=holding,
        earnings_date=earnings_date,
        source=source,
        filing=filing,
        eps_estimate=(yf_row.eps_estimate if yf_row else None),
        eps_actual=(yf_row.eps_actual if yf_row else None),
        surprise_pct=(yf_row.surprise_pct if yf_row else None),
    )


def build_packet(report: CandidateReport) -> str:
    """Build the markdown briefing packet for the routine to ingest."""
    h = report.holding
    snap = price.price_snapshot(h.yahoo)
    move = price.post_earnings_move(h.yahoo, report.earnings_date)
    analyst = price.analyst_snapshot(h.yahoo)
    quarters = price.quarterly_financials(h.yahoo, n=6)
    history = price.earnings_history(h.yahoo, n=8)

    buf = StringIO()
    w = buf.write

    w(f"# Briefing packet: {h.ticker} — {snap.name or h.ticker}\n\n")
    w(f"_Generated {datetime.utcnow().isoformat(timespec='seconds')}Z_\n\n")

    # --- Identity & thesis ---------------------------------------------------
    w("## Position context\n")
    w(f"- **Ticker (yfinance):** {h.yahoo}\n")
    if snap.sector or snap.industry:
        w(f"- **Sector / industry:** {snap.sector or '?'} / {snap.industry or '?'}\n")
    w(f"- **Your thesis note:** {h.notes or '_(none — add to portfolio.yaml)_'}\n\n")

    # --- Headline ------------------------------------------------------------
    w("## Headline\n")
    w(f"- **Reported:** {report.earnings_date.isoformat()}\n")
    if report.eps_actual is not None:
        beat_str = ""
        if report.eps_estimate is not None:
            diff = report.eps_actual - report.eps_estimate
            sign = "+" if diff >= 0 else ""
            beat_str = f" (est. {report.eps_estimate:.2f}, {sign}{diff:.2f}"
            if report.surprise_pct is not None:
                beat_str += f", {sign}{report.surprise_pct:.1f}%)"
            else:
                beat_str += ")"
        w(f"- **EPS:** {report.eps_actual:.2f}{beat_str}\n")
    else:
        w("- **EPS:** _not yet in yfinance — extract from filing/press release below_\n")
    if report.filing:
        w(f"- **Filing:** {report.filing.form}  ({report.filing.items or '—'})\n")
        w(f"- **Filing URL:** {report.filing.primary_url}\n")
        w(f"- **Filing index (exhibits):** https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(report.filing.cik)}&type={report.filing.form}&dateb=&owner=include&count=10\n")
    w("\n")

    # --- Price reaction ------------------------------------------------------
    w("## Price reaction\n")
    cur = snap.currency or ""
    if move.pre_close is not None:
        w(f"- **Close on report date ({report.earnings_date.isoformat()}):** {move.pre_close:.2f} {cur}\n")
    if move.next_close is not None and move.one_day_pct is not None:
        w(f"- **Next session close:** {move.next_close:.2f} {cur}  ({move.one_day_pct:+.2f}%)\n")
    if move.five_day_close is not None and move.five_day_pct is not None:
        w(f"- **+5 trading days close:** {move.five_day_close:.2f} {cur}  ({move.five_day_pct:+.2f}%)\n")
    if snap.last_price is not None:
        w(f"- **Last price:** {snap.last_price:.2f} {cur}\n")
    if snap.fifty_two_high is not None and snap.fifty_two_low is not None and snap.last_price is not None:
        rng = snap.fifty_two_high - snap.fifty_two_low
        pos = (snap.last_price - snap.fifty_two_low) / rng if rng > 0 else None
        pos_str = f" — {pos*100:.0f}% of 52w range" if pos is not None else ""
        w(f"- **52-week range:** {snap.fifty_two_low:.2f} – {snap.fifty_two_high:.2f} {cur}{pos_str}\n")
    if snap.forward_pe is not None:
        trailing = f", trailing {snap.trailing_pe:.1f}" if snap.trailing_pe else ""
        w(f"- **Valuation:** forward P/E {snap.forward_pe:.1f}{trailing}\n")
    if snap.market_cap:
        w(f"- **Market cap:** {_fmt_big(snap.market_cap)} {cur}\n")
    w("\n")

    # --- Earnings history (trend) -------------------------------------------
    w("## Earnings beat/miss trend (recent 6)\n")
    w("| Date | EPS est | EPS act | Surprise % |\n|---|---:|---:|---:|\n")
    shown = 0
    for r in history:
        if r.eps_actual is None:
            continue
        shown += 1
        if shown > 6:
            break
        w(
            f"| {r.date.isoformat()} | "
            f"{_fmt_num(r.eps_estimate)} | {_fmt_num(r.eps_actual)} | {_fmt_num(r.surprise_pct, suffix='%')} |\n"
        )
    w("\n")

    # --- Revenue / margin trajectory (from quarterly_income_stmt) ----------
    if quarters:
        w("## Revenue & margins (last reported quarters, from yfinance)\n")
        w(
            "_Note: this table lags the just-filed quarter by ~1–3 days. "
            "Pull the current quarter's revenue from the filing/press release URL above._\n\n"
        )
        w("| Period end | Revenue | Gross profit | Op. income | Net income | Dil. EPS |\n|---|---:|---:|---:|---:|---:|\n")
        for q in quarters:
            w(
                f"| {q.period_end} | "
                f"{_fmt_big(q.revenue)} | {_fmt_big(q.gross_profit)} | "
                f"{_fmt_big(q.operating_income)} | {_fmt_big(q.net_income)} | "
                f"{_fmt_num(q.diluted_eps)} |\n"
            )
        w("\n")

    # --- Analyst snapshot --------------------------------------------------
    w("## Analyst consensus & recent actions\n")
    if analyst.target_mean is not None:
        upside = ""
        if snap.last_price:
            up = (analyst.target_mean - snap.last_price) / snap.last_price * 100
            upside = f"  ({up:+.1f}% vs last price)"
        w(
            f"- **Mean target:** {analyst.target_mean:.2f} {cur}{upside}  "
            f"[range {analyst.target_low or '?'}–{analyst.target_high or '?'}, "
            f"n={analyst.n_analysts or '?'}]\n"
        )
    if analyst.recommendation_key:
        w(f"- **Consensus rating:** {analyst.recommendation_key} ({analyst.recommendation_mean:.2f}/5, lower=more bullish)\n")
    if analyst.rec_breakdown:
        b = analyst.rec_breakdown
        w(
            f"- **Breakdown:** strong-buy {b.get('strongBuy', 0)}, buy {b.get('buy', 0)}, "
            f"hold {b.get('hold', 0)}, sell {b.get('sell', 0)}, strong-sell {b.get('strongSell', 0)}\n"
        )
    if analyst.recent_actions:
        w(f"\n**Recent analyst actions (last 21 days):**\n\n")
        for a in analyst.recent_actions[:12]:
            tgt = ""
            if a.get("price_target") and a.get("prior_price_target"):
                tgt = f"  PT {a['prior_price_target']}→{a['price_target']}"
            elif a.get("price_target"):
                tgt = f"  PT {a['price_target']}"
            elif a.get("prior_price_target"):
                tgt = f"  (prior PT {a['prior_price_target']})"
            grade = (
                f"{a.get('from_grade') or '—'}→{a.get('to_grade') or '—'}"
                if (a.get("from_grade") or a.get("to_grade"))
                else ""
            )
            w(f"- {a['date']} · {a.get('firm') or '?'} · {a.get('action') or ''} {grade}{tgt}\n")
    w("\n")

    # --- Sources for the routine to fetch ----------------------------------
    w("## Sources to fetch with WebFetch\n")
    w(
        "_The routine should open these URLs to extract revenue/guidance numbers, "
        "management commentary, and Q&A pushback that aren't in the structured data above._\n\n"
    )
    if report.filing:
        w(f"1. **SEC filing (primary doc):** {report.filing.primary_url}\n")
        w(
            f"   - For 8-K Item 2.02: this is the press release with the headline numbers and guidance.\n"
        )
        w(
            f"   - Also list exhibits at: https://www.sec.gov/Archives/edgar/data/"
            f"{int(report.filing.cik)}/{report.filing.accession.replace('-', '')}/\n"
        )
    w(
        f"2. **Earnings call transcript (search):** https://www.google.com/search?q="
        f"{_q(h.ticker + ' Q' + _quarter_of(report.earnings_date) + ' ' + str(report.earnings_date.year) + ' earnings call transcript')}\n"
    )
    w(
        f"   - Likely sources: fool.com/earnings/call-transcripts/…, seekingalpha.com/article/…, motleyfool.com\n"
    )
    w(
        f"3. **Analyst commentary (search):** https://www.google.com/search?q="
        f"{_q(h.ticker + ' analyst reaction ' + report.earnings_date.isoformat())}\n"
    )
    w(
        f"4. **Company IR page (best-effort):** https://www.google.com/search?q="
        f"{_q((snap.name or h.ticker) + ' investor relations earnings')}\n"
    )
    w("\n")

    # --- Briefing instructions for the routine -----------------------------
    w("## Briefing instructions for the routine\n")
    w(
        "Write a briefing-style email (~500 words) to the user. Sections, in order:\n\n"
        "1. **TL;DR** — 2–3 sentences. Beat/miss, market reaction, your one-line take.\n"
        "2. **Results vs. expectations** — revenue & EPS vs. consensus, key segment numbers, guidance.\n"
        "3. **Management tone & transcript highlights** — what they said, what they avoided, Q&A pushback. Quote selectively.\n"
        "4. **Analyst views** — consensus, target changes, notable upgrades/downgrades and why.\n"
        "5. **Price reaction & valuation** — the move, where the stock sits vs. 52w range, fwd P/E context.\n"
        "6. **What to watch next quarter** — explicitly tied to the user's thesis note above.\n\n"
        "Style: direct, no hype, no filler. Quote numbers exactly. If something is unclear or unavailable, say so — don't invent.\n"
    )

    return buf.getvalue()


# --- helpers ----------------------------------------------------------------

def _fmt_num(v, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}{suffix}" if suffix == "%" else f"{v:.2f}{suffix}"


def _fmt_big(v) -> str:
    if v is None:
        return "—"
    av = abs(v)
    if av >= 1e12:
        return f"{v/1e12:.2f}T"
    if av >= 1e9:
        return f"{v/1e9:.2f}B"
    if av >= 1e6:
        return f"{v/1e6:.1f}M"
    return f"{v:.0f}"


def _quarter_of(d: date) -> str:
    return str(((d.month - 1) // 3) + 1)


def _q(s: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(s)
