# StockAgent

Portfolio earnings briefing agent. Detects when a company you hold reports
earnings, builds a structured data packet from SEC filings + market data,
and emails you a tight briefing (results vs. expectations, management tone,
analyst views, price reaction, what to watch next).

## Architecture

Three pieces, working together:

1. **Python data layer** (`src/stockagent/`) — deterministic fetchers for SEC
   EDGAR filings and yfinance (prices, fundamentals, analyst consensus). Emits
   a markdown briefing packet per pending earnings event.
2. **Local refresher** (`bin/refresh_packets.sh`, scheduled via launchd) —
   runs on your Mac each weekday morning, generates packets for any new
   reporters, commits & pushes them to the repo. The Python data sources
   (SEC, yfinance) return HTTP 403 from Anthropic's cloud egress IPs, so
   data fetching has to happen on a machine that they accept.
3. **Claude Code scheduled routine** (`.claude/routines/portfolio_review.md`)
   — fires daily after the local refresher pushes packets, reads
   `packets/index.json`, fetches transcripts/press releases via WebFetch,
   writes the briefing, and saves Gmail drafts via the Gmail MCP.

```
┌────────────────────┐    push     ┌──────────────────┐    clone     ┌─────────────────┐
│  Mac (launchd)     │ ──packets/─ │  GitHub repo     │ ───────────▶ │ Cloud routine   │
│  refresh_packets   │  to repo    │  packets/*.md    │              │ writes Gmail    │
│  - SEC EDGAR       │             │  packets/index   │              │ drafts          │
│  - yfinance        │             │                  │              │ (Gmail MCP)     │
└────────────────────┘             └──────────────────┘              └─────────────────┘
```

## Quick start

```bash
# Install deps (uv: https://docs.astral.sh/uv/)
uv sync

# Edit holdings & thesis notes
$EDITOR portfolio.yaml

# Dry-run: what reported in the last 7 days?
uv run stockagent check

# Build a single packet by hand
uv run stockagent gather NVDA

# Run the full refresher (what launchd runs)
bin/refresh_packets.sh
```

## Commands

| Command | What it does |
|---|---|
| `stockagent check [--json]` | List portfolio holdings that reported in the last `--lookback-days` (default 7). |
| `stockagent gather TICKER [--out FILE]` | Build a markdown briefing packet for one ticker. |
| `stockagent mark-sent TICKER DATE SOURCE` | (Local-only) record a briefing as sent. The cloud routine dedups via Gmail search instead. |

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

## Scheduling

**Local refresher (launchd):** `~/Library/LaunchAgents/co.cifra.stockagent.refresh.plist`
fires weekday mornings at 12:00 local time. Logs to `state/logs/launchd.{out,err}.log`.

To pause / resume:
```bash
launchctl bootout "gui/$(id -u)/co.cifra.stockagent.refresh"   # disable
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/co.cifra.stockagent.refresh.plist  # re-enable
```

**Cloud routine:** managed at https://claude.ai/code/routines. Fires `30 11 * * 1-5`
UTC (07:30 EDT / 06:30 EST). Edit the routine's prompt to change behavior;
edit `.claude/routines/portfolio_review.md` and push to update the procedure
the routine reads each run.

## Files & state

- `packets/` — built briefing packets (one `.md` per earnings event), plus
  `index.json`. Pruned after 14 days by the refresher.
- `state/sent.json` — local-only sent-tracking (cloud dedups via Gmail).
- `state/logs/` — refresher logs, ignored by routine but useful for debugging.

## Why the split?

- **Python locally**: SEC EDGAR and Yahoo Finance respond to home/office IPs
  but 403 cloud egress ranges. The Mac runs the deterministic data layer.
- **Claude in the cloud**: the actual briefing prose needs judgment — what
  to quote from a transcript, how to relate guidance to your thesis. Claude
  Sonnet 4.6 in a scheduled routine does this well, and the Gmail MCP makes
  delivery free of OAuth plumbing.

The `packets/` directory is the contract between the two halves.
