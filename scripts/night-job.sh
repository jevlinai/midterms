#!/bin/bash
# One election-night job, run by launchd (scripts/night.sh installs it). launchd restarts it if it
# crashes; every start sends an alert, so a restart is visible.
set -uo pipefail
JOB="$1"
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
set -a; . "$HOME/.config/midterms/env"; set +a
export MIDTERMS_MODEL=claude-sonnet-5-5
export MIDTERMS_RUN_CAP_USD="${MIDTERMS_RUN_CAP_USD:-2}" MIDTERMS_DAILY_CAP_USD="${MIDTERMS_DAILY_CAP_USD:-150}" MIDTERMS_TOTAL_CAP_USD="${MIDTERMS_TOTAL_CAP_USD:-250}"
SINCE="${NIGHT_SINCE:-2026-11-03}"

alert() {
  echo "$(date '+%F %T') ALERT $*"
  [ -n "${ALERT_WEBHOOK:-}" ] && curl -s -m 10 -H 'content-type: application/json' \
    -d "{\"text\":\"[midterms $(hostname -s)] $*\",\"content\":\"[midterms $(hostname -s)] $*\"}" "$ALERT_WEBHOOK" >/dev/null || true
}
[ -f DRYRUN ] && { alert "$JOB refused: this is a dry-run checkout"; exit 0; }
alert "$JOB started"

case "$JOB" in
  poller)        exec uv run python poller/poller.py --budget-usd "${NIGHT_POLLER_BUDGET:-350}" --count-spend-since "$SINCE" ;;
  detector)      exec uv run python detector/detect.py --mode page --interval 30 ;;
  detector-eng)  exec uv run python detector/detect.py --mode engine --interval 120 ;;
  state)
    while true; do
      if [ -n "${CF_API_TOKEN:-}" ]; then uv run python state/build_state.py --push || alert "state build/push failed"
      else uv run python state/build_state.py || alert "state build failed"; fi
      sleep 60
    done ;;
  call-finder)   exec uv run midterms call-finder --interval 300 --jevlin-cap-usd "${NIGHT_CALLFINDER_BUDGET:-20}" --budget-since "$SINCE" ;;
  post-drafter)  exec uv run midterms post-drafter --interval 60 ;;
  confirm-web)
    # The confirm page, on the Mac mini's Tailscale address only.
    export CONFIRM_WEB_HOST="${CONFIRM_WEB_HOST:-127.0.0.1}"
    exec uv run python confirm/web.py ;;
  autopost)
    # Posts only with MIDTERMS_AUTOPOST_LIVE=true and MIDTERMS_AUTOPOST_ANCHOR=<post id> in the env file.
    # Stop at once: touch state/STOP_POSTING.
    export MIDTERMS_PUBLISH_LIVE="${MIDTERMS_AUTOPOST_LIVE:-false}"
    exec uv run midterms autopost --interval 30 ;;
  *) alert "unknown job $JOB"; exit 2 ;;
esac
