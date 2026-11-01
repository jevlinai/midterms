#!/bin/bash
# NFL dry run controller. Run ONLY inside a scratch clone prepared by scripts/make_dryrun.py.
#
#   scripts/dryrun.sh start     # starts poller, detector, state builder, agent track in the background
#   scripts/dryrun.sh status    # what's running, last log lines, spend so far
#   scripts/dryrun.sh stop
#   scripts/dryrun.sh report    # after confirming finals: leaderboard from state/state.json
#
# Budgets (hard stops): poller $8 of Jevlin; agent track $3 of Jevlin + $6 of Claude. Expected total ~$10.
# Keys come from ~/.config/midterms/env (never printed). Jevlin key from ~/.jevlin/credentials.json.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f DRYRUN ] || { echo "not a dry-run clone (no DRYRUN marker); refusing" >&2; exit 1; }
mkdir -p logs
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
set -a; . "$HOME/.config/midterms/env"; set +a
export MIDTERMS_MODEL=claude-sonnet-5-5 MIDTERMS_RUN_CAP_USD=1 MIDTERMS_DAILY_CAP_USD=6 MIDTERMS_TOTAL_CAP_USD=6

start() {
  [ -n "${TYPESAFE_API_KEY:-}" ] && [ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "fill ~/.config/midterms/env first" >&2; exit 1; }
  [ -f "$HOME/.jevlin/credentials.json" ] || { echo "Jevlin CLI not logged in (~/.jevlin/credentials.json missing)" >&2; exit 1; }
  nohup uv run python poller/poller.py --budget-usd 8 > logs/poller.log 2>&1 & echo $! > logs/poller.pid
  nohup uv run python detector/detect.py --interval 60 > logs/detector.log 2>&1 & echo $! > logs/detector.pid
  nohup bash -c 'while true; do uv run python state/build_state.py; sleep 60; done' > logs/state.log 2>&1 & echo $! > logs/state.pid
  nohup uv run midterms track --budget-usd 3 > logs/track.log 2>&1 & echo $! > logs/track.pid
  sleep 3; status
}

status() {
  for p in poller detector state track; do
    if [ -f logs/$p.pid ] && kill -0 "$(cat logs/$p.pid)" 2>/dev/null; then s=running; else s=STOPPED; fi
    echo "== $p: $s"; tail -n 3 logs/$p.log 2>/dev/null || true
  done
}

stop() {
  for p in poller detector state track; do
    [ -f logs/$p.pid ] && kill "$(cat logs/$p.pid)" 2>/dev/null && echo "stopped $p" || true
    rm -f logs/$p.pid
  done
}

report() {
  uv run python state/build_state.py
  uv run python - <<'EOF'
import json
s = json.load(open("state/state.json"))
print(s["summary"])
print(f"{'engine':20} {'median min':>10} {'first':>6} {'lead vs G':>9} {'never':>6} {'wrong':>6} {'fails':>6}")
for b in s["leaderboard"]:
    print(f"{b['engine']:20} {str(b['median_minutes_after_close']):>10} {b['found_first']:>6} {str(b['median_lead_vs_google']):>9} {b['never_found']:>6} {b['wrong']:>6} {b['failed_rounds']:>6}")
print("excluded sites:", [x["site"] for x in s.get("excluded_sites", [])])
for r in s["races"]:
    if r["status"] == "called":
        t = {e: v["minutes_after_close"] for e, v in r["engines"].items()}
        print(r["id"], "winner", r["call"]["winner"], "first", r["first"], t)
EOF
}

case "${1:-}" in
  start) start ;; status) status ;; stop) stop ;; report) report ;;
  *) echo "usage: $0 start|status|stop|report" >&2; exit 2 ;;
esac
