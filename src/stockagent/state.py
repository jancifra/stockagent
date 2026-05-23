"""Tracks which earnings reports we've already emailed about.

Keyed by ticker. Records the most recent earnings_date we sent a briefing for,
plus a list of (date, accession-or-source-url) for audit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_PATH = Path("state/sent.json")


@dataclass
class Sent:
    ticker: str
    earnings_date: date
    source: str  # SEC accession # or URL


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text() or "{}")


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def has_been_sent(ticker: str, earnings_date: date, *, path: Path = DEFAULT_PATH) -> bool:
    data = _load(path)
    entry = data.get(ticker)
    if not entry:
        return False
    last = entry.get("last_earnings_date")
    if not last:
        return False
    try:
        return date.fromisoformat(last) >= earnings_date
    except ValueError:
        return False


def mark_sent(ticker: str, earnings_date: date, source: str, *, path: Path = DEFAULT_PATH) -> None:
    data = _load(path)
    entry = data.setdefault(ticker, {"history": []})
    entry["last_earnings_date"] = earnings_date.isoformat()
    entry["last_source"] = source
    entry["last_sent_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    history = entry.setdefault("history", [])
    history.append(
        {
            "earnings_date": earnings_date.isoformat(),
            "source": source,
            "sent_at": entry["last_sent_at"],
        }
    )
    # Cap history so the file doesn't grow forever.
    entry["history"] = history[-20:]
    _save(path, data)
