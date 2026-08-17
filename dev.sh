#!/usr/bin/env bash
# Sonus development runner with live reload.
#
#     ./dev.sh
#
# Runs the panel from this checkout and restarts it whenever a .py file
# changes, so edits show up in the live panel a moment after you save.
# Uses inotifywait (inotify-tools) when available, otherwise falls back
# to a small mtime-polling loop that needs nothing beyond python3.
# Ctrl+C stops the watcher and shuts the panel down cleanly.

set -u
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
APP_PID=""

start_app() {
    "$PY" sonus_deck.py &
    APP_PID=$!
    echo "[dev] panel started (pid $APP_PID)"
}

stop_app() {
    [ -n "$APP_PID" ] || return 0
    if kill -0 "$APP_PID" 2>/dev/null; then
        # --quit goes through the IPC socket so settings and the PipeWire
        # graph shut down cleanly; the kill below is just a safety net.
        "$PY" sonus_deck.py --quit >/dev/null 2>&1 || true
        for _ in $(seq 1 30); do
            kill -0 "$APP_PID" 2>/dev/null || break
            sleep 0.1
        done
        kill -9 "$APP_PID" 2>/dev/null || true
    fi
    wait "$APP_PID" 2>/dev/null || true
    APP_PID=""
}

cleanup() {
    echo
    echo "[dev] shutting down"
    stop_app
    exit 0
}
trap cleanup INT TERM

wait_for_change() {
    if command -v inotifywait >/dev/null 2>&1; then
        inotifywait -qq -r \
            -e modify -e create -e delete -e move \
            --exclude '(__pycache__|\.pyc$)' \
            sonusdeck sonus_deck.py
    else
        "$PY" - <<'EOF'
import os
import time

def snapshot():
    state = {}
    paths = ["sonus_deck.py"]
    for root, dirs, files in os.walk("sonusdeck"):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        paths.extend(os.path.join(root, f) for f in files if f.endswith(".py"))
    for path in paths:
        try:
            state[path] = os.stat(path).st_mtime_ns
        except OSError:
            pass
    return state

before = snapshot()
while snapshot() == before:
    time.sleep(0.5)
EOF
    fi
}

echo "[dev] watching sonusdeck/ and sonus_deck.py for changes (Ctrl+C quits)"
# An installed/autostarted panel would make our instance defer to it and
# exit, so take the socket over before starting the dev build.
"$PY" sonus_deck.py --quit >/dev/null 2>&1 || true
sleep 0.3
start_app
while wait_for_change; do
    sleep 0.3  # let editors finish multi-file saves
    echo "[dev] change detected, restarting"
    stop_app
    start_app
done
