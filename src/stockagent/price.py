"""Price, fundamentals, and analyst data from yfinance.

yfinance is the only data dependency for non-SEC tickers (ADYEN.AS, MBG.DE).
For US-listed names we still use it for prices and analyst consensus.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

warnings.filterwarnings("ignore")  # yfinance is chatty about deprecations

import yfinance as yf  # noqa: E402


@dataclass
class EarningsRow:
    when: datetime
    eps_estimate: float | None
    eps_actual: float | None
    surprise_pct: float | None

    @property
    def date(self) -> date:
        return self.when.date()

    @property
    def beat(self) -> bool | None:
        if self.eps_estimate is None or self.eps_actual is None:
            return None
        return self.eps_actual >= self.eps_estimate


@dataclass
class QuarterlyRow:
    period_end: date
    revenue: float | None
    net_income: float | None
    diluted_eps: float | None
    gross_profit: float | None
    operating_income: float | None


@dataclass
class PostEarningsMove:
    earnings_date: date
    pre_close: float | None
    next_close: float | None  # 1 trading day after earnings_date
    five_day_close: float | None  # 5 trading days after
    one_day_pct: float | None
    five_day_pct: float | None


@dataclass
class AnalystSnapshot:
    target_mean: float | None
    target_median: float | None
    target_high: float | None
    target_low: float | None
    n_analysts: int | None
    recommendation_key: str | None  # 'buy', 'strong_buy', ...
    recommendation_mean: float | None  # 1=strong buy, 5=strong sell
    rec_breakdown: dict[str, int] = field(default_factory=dict)
    recent_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PriceSnapshot:
    ticker: str
    name: str | None
    currency: str | None
    last_price: float | None
    fifty_two_high: float | None
    fifty_two_low: float | None
    market_cap: float | None
    forward_pe: float | None
    trailing_pe: float | None
    sector: str | None
    industry: str | None


def _safe(d: dict, k: str) -> Any:
    v = d.get(k)
    if v is None:
        return None
    if isinstance(v, float) and (v != v):  # NaN
        return None
    return v


def price_snapshot(ticker: str) -> PriceSnapshot:
    t = yf.Ticker(ticker)
    info = t.info or {}
    return PriceSnapshot(
        ticker=ticker,
        name=_safe(info, "shortName") or _safe(info, "longName"),
        currency=_safe(info, "currency") or _safe(info, "financialCurrency"),
        last_price=_safe(info, "currentPrice") or _safe(info, "regularMarketPrice"),
        fifty_two_high=_safe(info, "fiftyTwoWeekHigh"),
        fifty_two_low=_safe(info, "fiftyTwoWeekLow"),
        market_cap=_safe(info, "marketCap"),
        forward_pe=_safe(info, "forwardPE"),
        trailing_pe=_safe(info, "trailingPE"),
        sector=_safe(info, "sector"),
        industry=_safe(info, "industry"),
    )


def earnings_history(ticker: str, n: int = 8) -> list[EarningsRow]:
    t = yf.Ticker(ticker)
    df = t.earnings_dates
    if df is None or df.empty:
        return []
    rows: list[EarningsRow] = []
    for ts, row in df.head(n).iterrows():
        rows.append(
            EarningsRow(
                when=ts.to_pydatetime(),
                eps_estimate=_nan_to_none(row.get("EPS Estimate")),
                eps_actual=_nan_to_none(row.get("Reported EPS")),
                surprise_pct=_nan_to_none(row.get("Surprise(%)")),
            )
        )
    return rows


def quarterly_financials(ticker: str, n: int = 8) -> list[QuarterlyRow]:
    t = yf.Ticker(ticker)
    qis = t.quarterly_income_stmt
    if qis is None or qis.empty:
        return []
    out: list[QuarterlyRow] = []
    for col in list(qis.columns)[:n]:
        period_end = col.date() if hasattr(col, "date") else col
        out.append(
            QuarterlyRow(
                period_end=period_end,
                revenue=_idx(qis, "Total Revenue", col) or _idx(qis, "Operating Revenue", col),
                net_income=_idx(qis, "Net Income", col),
                diluted_eps=_idx(qis, "Diluted EPS", col),
                gross_profit=_idx(qis, "Gross Profit", col),
                operating_income=_idx(qis, "Operating Income", col),
            )
        )
    return out


def post_earnings_move(ticker: str, earnings_date: date) -> PostEarningsMove:
    """Compute 1-day and 5-day post-earnings moves.

    Most US issuers report after market close; the relevant 'next close' is the
    next trading day. If they reported before open, this is still useful (it
    catches the same-day move). For approximation we look up the close on
    earnings_date (= pre-print close for AMC reporters, or the reaction close
    for BMO reporters) and the next trading days.
    """
    t = yf.Ticker(ticker)
    # Fetch a window around the earnings date
    start = (earnings_date - timedelta(days=4)).isoformat()
    end = (earnings_date + timedelta(days=14)).isoformat()
    hist = t.history(start=start, end=end, auto_adjust=False)
    if hist is None or hist.empty:
        return PostEarningsMove(earnings_date, None, None, None, None, None)
    closes = hist["Close"].to_dict()
    dates = sorted(closes.keys())
    # Find first trading day on/before earnings_date as the pre_close
    pre_close = None
    for d in dates:
        d_only = d.date() if hasattr(d, "date") else d
        if d_only <= earnings_date:
            pre_close = closes[d]
        else:
            break
    # Subsequent trading days
    later = [(d, closes[d]) for d in dates if (d.date() if hasattr(d, "date") else d) > earnings_date]
    next_close = later[0][1] if len(later) >= 1 else None
    five_day_close = later[4][1] if len(later) >= 5 else None
    one_day_pct = _pct(pre_close, next_close)
    five_day_pct = _pct(pre_close, five_day_close)
    return PostEarningsMove(
        earnings_date=earnings_date,
        pre_close=pre_close,
        next_close=next_close,
        five_day_close=five_day_close,
        one_day_pct=one_day_pct,
        five_day_pct=five_day_pct,
    )


def analyst_snapshot(ticker: str, recent_window_days: int = 21) -> AnalystSnapshot:
    t = yf.Ticker(ticker)
    info = t.info or {}
    snap = AnalystSnapshot(
        target_mean=_safe(info, "targetMeanPrice"),
        target_median=_safe(info, "targetMedianPrice"),
        target_high=_safe(info, "targetHighPrice"),
        target_low=_safe(info, "targetLowPrice"),
        n_analysts=_safe(info, "numberOfAnalystOpinions"),
        recommendation_key=_safe(info, "recommendationKey"),
        recommendation_mean=_safe(info, "recommendationMean"),
    )
    # Recommendation breakdown (most recent period)
    try:
        rec = t.recommendations
        if rec is not None and not rec.empty:
            current = rec.iloc[0].to_dict()
            snap.rec_breakdown = {
                k: int(current[k])
                for k in ("strongBuy", "buy", "hold", "sell", "strongSell")
                if k in current and current[k] == current[k]
            }
    except Exception:
        pass
    # Recent upgrades/downgrades
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            cutoff = datetime.now(ud.index.tz) - timedelta(days=recent_window_days)
            recent = ud[ud.index >= cutoff].head(15)
            for ts, row in recent.iterrows():
                snap.recent_actions.append(
                    {
                        "date": ts.date().isoformat(),
                        "firm": row.get("Firm"),
                        "to_grade": row.get("ToGrade"),
                        "from_grade": row.get("FromGrade"),
                        "action": row.get("Action"),
                        "price_target": _nan_to_none(row.get("PriceTarget")),
                        "prior_price_target": _nan_to_none(row.get("priorPriceTarget")),
                    }
                )
    except Exception:
        pass
    return snap


def next_scheduled_earnings(ticker: str) -> datetime | None:
    """Return the next scheduled earnings datetime (future), if any."""
    rows = earnings_history(ticker, n=12)
    now = datetime.now()
    future = [
        r.when for r in rows if r.eps_actual is None and r.when.replace(tzinfo=None) > now
    ]
    return min(future) if future else None


def _idx(df, row: str, col) -> float | None:
    try:
        v = df.at[row, col]
        return _nan_to_none(v)
    except (KeyError, ValueError):
        return None


def _nan_to_none(v):
    if v is None:
        return None
    try:
        if v != v:  # NaN
            return None
    except TypeError:
        pass
    return float(v) if isinstance(v, (int, float)) else v


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100
