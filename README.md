# StockAgent

Portfolio earnings briefing agent. Detects when a company you hold reports
earnings, builds a structured data packet, and emails you a tight briefing
(results vs. expectations, management tone, analyst views, price reaction,
what to watch next).

## How it works

Two layers:

1. **Python data layer** (`src/stockagent/`): deterministic fetchers for SEC
   EDGAR filings, prices/fundamentals/analyst consensus (yfinance), and a
   packet builder that emits markdown.
2. **Claude Code scheduled routine** (`.claude/routines/portfolio_review.md`):
   runs daily, calls the CLI to detect reporters, reads the packet, fetches
   transcripts/press releases via WebFetch, writes the briefing email, and
   sends it via Gmail MCP.

## Quick start

```bash
# Install deps (uv: https://docs.astral.sh/uv/)
uv sync

# Edit holdings & thesis notes
$EDITOR portfolio.yaml

# Dry-run: what reported in the last 7 days?
uv run stockagent check

# Inspect the briefing packet for one ticker
uv run stockagent gather NVDA
```

Then to enable the daily run, invoke `/schedule` in Claude Code with the
prompt from `.claude/routines/portfolio_review.md`. Suggested cadence:
**Mon-Fri at 07:30 ET**.

## Commands

| Command | What it does |
|---|---|
| `stockagent check [--json]` | List portfolio holdings that reported in the last `--lookback-days` (default 7) and haven't been emailed yet. |
| `stockagent gather TICKER` | Build a markdown briefing packet for one ticker. |
| `stockagent mark-sent TICKER DATE SOURCE` | Record that a briefing was sent (so it isn't re-sent tomorrow). |

## Portfolio format

`portfolio.yaml`:

```yaml
contact_email: you@example.com
holdings:
  - ticker: NVDA        # how you refer to the position
    yahoo: NVDA         # yfinance symbol (may differ: BRK-B, ADYEN.AS, SAP.DE)
    sec: true           # has SEC filings? false for ADYEN.NV, MBG.DE
    notes: |
      Your thesis — what you're watching. Fed verbatim to the briefing
      prompt so reports reflect your view, not generic coverage.
```

## State

`state/sent.json` tracks which earnings reports have been emailed. Delete an
entry to force a re-send.

## Why the split between Python and the routine?

- **Python** is good at fetching structured data deterministically (EDGAR
  API, yfinance) and at running on a schedule cheaply.
- **Claude** is good at reading press releases and transcripts and writing
  the actual briefing prose with judgment.

The packet is the contract between them: Python produces it, Claude consumes
it.
