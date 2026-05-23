#!/usr/bin/env bash
#
# refresh_packets.sh — generate briefing packets for any portfolio company
# that reported earnings recently, then commit & push to the public repo so
# the cloud routine can read them.
#
# Runs on the user's Mac (SEC.gov + yfinance work here; they 403 from the
# Anthropic cloud egress).
#
# Idempotent: rebuilds packets/index.json each run; only adds files for
# tickers whose packet doesn't already exist.
#
# Output paths:
#   packets/{TICKER}_{YYYY-MM-DD}.md     — the briefing packet for one event
#   packets/index.json                    — list of pending packets the routine reads
#
# Exit codes:
#   0   normal (whether or not anything was added)
#   1   internal error
#
set -euo pipefail

# Resolve repo root from script location so launchd / cron need no CWD setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Make uv reachable when invoked from launchd (no shell profile loaded).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

LOG_DIR="$REPO_ROOT/state/logs"
mkdir -p "$LOG_DIR" packets
LOG="$LOG_DIR/refresh_$(date -u +%Y%m%d).log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

log "=== refresh_packets start ==="

# 1) Sync deps (cheap if uv.lock matches)
uv sync --quiet 2>>"$LOG"

# 2) Detect pending reports. We pass --include-sent because state/sent.json
#    is per-machine and not authoritative for cloud dedup; the cloud uses
#    Gmail-based dedup on its end. Locally we just want every recent reporter.
PENDING_JSON="$(uv run stockagent check --lookback-days 7 --json --include-sent)"
log "check returned: $(echo "$PENDING_JSON" | jq -c 'length') reporters"

# 3) For each pending ticker, build a packet if one doesn't already exist.
ADDED=0
INDEX_TMP="$(mktemp)"
echo "[]" >"$INDEX_TMP"

echo "$PENDING_JSON" | jq -c '.[]' | while read -r row; do
    TICKER="$(echo "$row" | jq -r .ticker)"
    DATE="$(echo "$row" | jq -r .earnings_date)"
    SAFE_TICKER="$(echo "$TICKER" | tr '.' '_')"
    PACKET="packets/${SAFE_TICKER}_${DATE}.md"

    if [[ ! -f "$PACKET" ]]; then
        log "building $PACKET ..."
        if uv run stockagent gather "$TICKER" --lookback-days 7 --out "$PACKET" 2>>"$LOG"; then
            ADDED=$((ADDED + 1))
            log "  built $PACKET"
        else
            log "  FAILED to build $PACKET — skipping"
            continue
        fi
    fi

    # Append to index regardless of new/existing — routine reads the index.
    jq --arg t "$TICKER" --arg d "$DATE" --arg p "$PACKET" \
       --argjson row "$row" \
       '. += [{ticker: $t, earnings_date: $d, packet: $p, meta: $row}]' \
       "$INDEX_TMP" >"$INDEX_TMP.new" && mv "$INDEX_TMP.new" "$INDEX_TMP"
done

mv "$INDEX_TMP" packets/index.json
log "wrote packets/index.json ($(jq -c 'length' packets/index.json) entries)"

# 4) Clean up packets older than 14 days — the cloud routine only acts on
#    recent ones; old packets keep the repo small.
find packets -maxdepth 1 -name '*.md' -mtime +14 -print -delete 2>>"$LOG" | while read -r f; do
    log "pruned old packet: $f"
done

# 5) Commit & push if anything changed.
if [[ -n "$(git status --porcelain packets/)" ]]; then
    git add packets/
    git commit -m "refresh packets: $(date -u +%Y-%m-%dT%H:%MZ)" >>"$LOG" 2>&1
    git push origin main >>"$LOG" 2>&1
    log "pushed updates ($(git log -1 --oneline))"
else
    log "no changes to commit"
fi

log "=== refresh_packets done ==="
