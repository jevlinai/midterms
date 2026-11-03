#!/bin/bash
# Election-night runner on the Mac mini: each job is a launchd agent that restarts on crash.
#
#   scripts/night.sh install     # write ~/Library/LaunchAgents/ai.jevlin.midterms.*.plist (doesn't start)
#   scripts/night.sh start       # load and start every job
#   scripts/night.sh status      # running?, last log lines, poller spend
#   scripts/night.sh stop        # stop every job (they stay installed)
#   scripts/night.sh uninstall
#
# Settings live in ~/.config/midterms/env (never printed): TYPESAFE_API_KEY, ANTHROPIC_API_KEY,
# optional ALERT_WEBHOOK (Slack/Discord), CF_ACCOUNT_ID/CF_KV_NAMESPACE_ID/CF_API_TOKEN (page data),
# NIGHT_POLLER_BUDGET (350), NIGHT_CALLFINDER_BUDGET (20), NIGHT_SINCE (2026-11-03).
# Jevlin key: ~/.jevlin/credentials.json. Start before 4:45pm ET Tue (first pre-close sweep: 5:00pm ET).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
JOBS=(poller detector detector-eng state call-finder post-drafter)  # agent track dropped 3 Nov
LA="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
label() { echo "ai.jevlin.midterms.$1"; }

install() {
  [ -f DRYRUN ] && { echo "dry-run checkout; refusing" >&2; exit 1; }
  mkdir -p "$LA" logs
  for j in "${JOBS[@]}"; do
    cat > "$LA/$(label "$j").plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$(label "$j")</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/scripts/night-job.sh</string><string>$j</string></array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$REPO/logs/$j.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/$j.log</string>
</dict></plist>
PLIST
  done
  echo "installed ${#JOBS[@]} jobs in $LA (not started)"
}

start() {
  for j in "${JOBS[@]}"; do
    launchctl bootstrap "$DOMAIN" "$LA/$(label "$j").plist" 2>/dev/null || launchctl kickstart -k "$DOMAIN/$(label "$j")"
  done
  sleep 3; status
}

stop() {
  for j in "${JOBS[@]}"; do launchctl bootout "$DOMAIN/$(label "$j")" 2>/dev/null && echo "stopped $j" || true; done
}

status() {
  for j in "${JOBS[@]}"; do
    pid=$(launchctl print "$DOMAIN/$(label "$j")" 2>/dev/null | awk '/^\tpid =/{print $3}')
    echo "== $j: ${pid:+running (pid $pid)}${pid:-NOT RUNNING}"
    tail -n 2 "logs/$j.log" 2>/dev/null | sed 's/^/   /' || true
  done
}

uninstall() { stop; for j in "${JOBS[@]}"; do rm -f "$LA/$(label "$j").plist"; done; echo "uninstalled"; }

case "${1:-}" in
  install) install ;; start) start ;; stop) stop ;; status) status ;; uninstall) uninstall ;;
  *) echo "usage: $0 install|start|stop|status|uninstall" >&2; exit 2 ;;
esac
