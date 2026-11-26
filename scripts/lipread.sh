#!/bin/bash
# Runs the lipread sidecar from the environment setup.sh made. Never downloads anything.
#   lipread.sh --installed     exit 0 when setup is complete (fast: no Python)
#   lipread.sh --history [N]   the last N dictations (default 50), one JSON line each
#   lipread.sh --log <json>    append a line to that history (the mod logs what was sent)
#   lipread.sh --personal [on|off]  switch the personal model; prints {"exists", "enabled", "info"}
#   lipread.sh <args>          python -m lipread <args>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LIPREAD_HOME="${LIPREAD_HOME:-$HOME/.cache/lip-dictation}"
PYTHON="$LIPREAD_HOME/venv/bin/python"
# Runs Python as the "Lip Dictation" app, so macOS asks for camera access for it (scripts/camera-app/main.c).
APP_BIN="$LIPREAD_HOME/Lip Dictation.app/Contents/MacOS/lip-dictation"

LOG="$LIPREAD_HOME/dictations.jsonl"
case "${1:-}" in
  --history) tail -n "${2:-50}" "$LOG" 2>/dev/null || true; exit 0 ;;
  --log) mkdir -p "$LIPREAD_HOME"; printf '%s\n' "${2:?}" >> "$LOG"; exit 0 ;;
  --personal)
    P="$LIPREAD_HOME/personal"
    case "${2:-}" in
      on) rm -f "$P/disabled" ;;
      off) mkdir -p "$P" && : > "$P/disabled" ;;
    esac
    EXISTS=false; [ -f "$P/adapter.pt" ] && EXISTS=true
    ENABLED=true; [ -f "$P/disabled" ] && ENABLED=false
    INFO=null; [ -f "$P/adapter.json" ] && INFO="$(cat "$P/adapter.json")"
    printf '{"exists": %s, "enabled": %s, "info": %s}\n' "$EXISTS" "$ENABLED" "$INFO"
    exit 0 ;;
esac

if [ "${1:-}" = "--installed" ]; then
  [ -x "$PYTHON" ] && [ -x "$APP_BIN" ] && [ -f "$LIPREAD_HOME/vendor/auto_avsr/.lipread-complete" ] \
    && [ -f "${LIPREAD_MODEL:-$LIPREAD_HOME/models/vsr_trlrs2lrs3vox2avsp_base.pth}" ]
  exit
fi

if [ ! -x "$PYTHON" ] || [ ! -x "$APP_BIN" ]; then
  echo '{"event":"error","code":"not-installed","message":"Not set up yet: run /lip-setup (or scripts/setup.sh)."}'
  exit 2
fi

export PYTHONPATH="$ROOT/sidecar${PYTHONPATH:+:$PYTHONPATH}"
exec "$APP_BIN" "$PYTHON" -m lipread "$@"
