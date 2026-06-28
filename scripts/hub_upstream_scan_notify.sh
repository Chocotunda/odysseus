#!/bin/sh
# Weekly upstream security-commit scan (B2 fork strategy; see memory
# fork-strategy-decision + scripts/hub_upstream_security_scan.py).
#
# Wired into launchd as com.odysseus.upstream-scan. Runs the filtered scanner,
# appends a timestamped record to a durable log (the safety net, since the
# scanner advances its watermark each run), and posts a macOS notification only
# when commits actually need a look. Read-only w.r.t. the code; never merges.
set -u

REPO="/Users/kganpat/Projects/odysseus"
PY="$REPO/venv/bin/python"
LOG="$HOME/Library/Logs/odysseus-upstream-scan.log"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

out="$("$PY" "$REPO/scripts/hub_upstream_security_scan.py" 2>&1)"
printf '\n===== %s =====\n%s\n' "$TS" "$out" >> "$LOG"

# Notify only on real security/dependency hits (not on the "up to date" path).
if printf '%s' "$out" | grep -q 'worth a look'; then
    n="$(printf '%s' "$out" | grep -oE '[0-9]+ worth a look' | grep -oE '^[0-9]+' | head -1)"
    osascript -e "display notification \"${n:-Some} upstream commit(s) worth a look — see ~/Library/Logs/odysseus-upstream-scan.log\" with title \"Odysseus upstream security scan\"" >/dev/null 2>&1
fi

exit 0
