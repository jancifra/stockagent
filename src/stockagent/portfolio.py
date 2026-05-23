"""Portfolio config loader."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Holding:
    ticker: str  # user-facing label
    yahoo: str  # yfinance symbol
    sec: bool  # whether SEC EDGAR has filings for this issuer
    notes: str = ""


@dataclass(frozen=True)
class Portfolio:
    contact_email: str
    holdings: tuple[Holding, ...]


def load_portfolio(path: str | Path = "portfolio.yaml") -> Portfolio:
    p = Path(path)
    data = yaml.safe_load(p.read_text())
    holdings = tuple(
        Holding(
            ticker=h["ticker"],
            yahoo=h["yahoo"],
            sec=bool(h.get("sec", True)),
            notes=h.get("notes", "") or "",
        )
        for h in data.get("holdings", [])
    )
    return Portfolio(
        contact_email=data.get("contact_email", ""),
        holdings=holdings,
    )
