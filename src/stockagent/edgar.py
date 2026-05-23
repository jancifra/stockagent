"""SEC EDGAR client: ticker → CIK, recent earnings-related filings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Iterable

import httpx

USER_AGENT = "StockAgent (jan@cifra.co)"  # SEC requires a contact UA
TIMEOUT = httpx.Timeout(20.0)

# Filing types that typically signal earnings:
#   8-K  Item 2.02 — US issuers' earnings press release
#   10-Q / 10-K     — US quarterly / annual reports
#   6-K            — foreign private issuers' interim filings (often earnings)
#   20-F           — foreign private issuers' annual report
EARNINGS_FORMS = {"8-K", "10-Q", "10-K", "6-K", "20-F"}


@dataclass(frozen=True)
class Filing:
    cik: str
    ticker: str
    form: str
    accession: str  # e.g. 0000320193-25-000123
    filed_at: date
    primary_doc: str  # filename of the primary document
    primary_url: str  # full URL to the document
    index_url: str  # filing index page (lists all exhibits)
    report_date: date | None  # period of report, if known
    items: str | None  # for 8-K, e.g. "2.02,9.01"

    @property
    def is_earnings_8k(self) -> bool:
        return self.form == "8-K" and (self.items or "").find("2.02") >= 0

    @property
    def is_likely_earnings(self) -> bool:
        if self.form in {"10-Q", "10-K", "20-F"}:
            return True
        if self.form == "8-K":
            return self.is_earnings_8k
        if self.form == "6-K":
            # 6-Ks are noisy but the ones we want for earnings are still earnings.
            # Defer to caller; we mark all 6-Ks as candidates and let the routine judge.
            return True
        return False


@lru_cache(maxsize=1)
def _ticker_map() -> dict[str, str]:
    """Map uppercased ticker → 10-digit zero-padded CIK string."""
    url = "https://www.sec.gov/files/company_tickers.json"
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT) as c:
        r = c.get(url)
        r.raise_for_status()
    data = r.json()
    out: dict[str, str] = {}
    for row in data.values():
        out[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
    return out


def cik_for(ticker: str) -> str | None:
    """Return zero-padded CIK for a ticker, or None if not found.

    Tries several normalizations:
      - `BRK.B` and `BRK-B` both resolve (SEC uses BRK-B).
      - Foreign suffixes (`SAP.DE`, `ASML.AS`) are stripped — SEC indexes ADRs
        under the bare US symbol when the issuer is a 20-F filer.
    """
    t = ticker.upper()
    candidates = {t, t.replace(".", "-"), t.split(".")[0]}
    m = _ticker_map()
    for c in candidates:
        if c in m:
            return m[c]
    return None


def recent_filings(
    ticker: str,
    *,
    since: date | None = None,
    forms: Iterable[str] = EARNINGS_FORMS,
    limit: int = 40,
) -> list[Filing]:
    """Return recent earnings-relevant filings for a ticker.

    Pulls from data.sec.gov/submissions/CIK{cik}.json (most-recent first).
    """
    cik = cik_for(ticker)
    if cik is None:
        return []
    forms_set = {f.upper() for f in forms}
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT) as c:
        r = c.get(url)
        r.raise_for_status()
    doc = r.json()
    recent = doc.get("filings", {}).get("recent", {})
    if not recent:
        return []

    forms_col = recent["form"]
    accs = recent["accessionNumber"]
    filed = recent["filingDate"]
    reports = recent.get("reportDate", [""] * len(accs))
    items = recent.get("items", [""] * len(accs))
    primary_docs = recent["primaryDocument"]

    out: list[Filing] = []
    for i, form in enumerate(forms_col):
        if form.upper() not in forms_set:
            continue
        try:
            filed_at = date.fromisoformat(filed[i])
        except (ValueError, IndexError):
            continue
        if since and filed_at < since:
            continue
        acc_no_dashes = accs[i].replace("-", "")
        report_dt: date | None = None
        if reports[i]:
            try:
                report_dt = date.fromisoformat(reports[i])
            except ValueError:
                pass
        index_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?"
            f"action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=10"
        )
        primary_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{primary_docs[i]}"
        )
        out.append(
            Filing(
                cik=cik,
                ticker=ticker.upper(),
                form=form,
                accession=accs[i],
                filed_at=filed_at,
                primary_doc=primary_docs[i],
                primary_url=primary_url,
                index_url=index_url,
                report_date=report_dt,
                items=items[i] or None,
            )
        )
        if len(out) >= limit:
            break
    return out


def latest_earnings_filing(ticker: str, *, lookback_days: int = 14) -> Filing | None:
    """Return the most recent filing that *looks like* an earnings report.

    Within the lookback window we prefer (in order):
      1. 8-K Item 2.02 (US press release — the canonical earnings 8-K)
      2. 10-Q / 10-K / 20-F
      3. 6-K (best-effort; caller should sanity-check the doc)
    """
    since = date.today() - timedelta(days=lookback_days)
    filings = recent_filings(ticker, since=since)
    earnings = [f for f in filings if f.is_likely_earnings]
    if not earnings:
        return None
    # Stable-sort by preference then recency.
    def score(f: Filing) -> tuple[int, date]:
        if f.is_earnings_8k:
            pref = 3
        elif f.form in {"10-Q", "10-K", "20-F"}:
            pref = 2
        else:  # 6-K
            pref = 1
        return (pref, f.filed_at)

    earnings.sort(key=score, reverse=True)
    return earnings[0]
