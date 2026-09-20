#!/bin/bash
# launch.sh — Start Curie with full Sitdeck integration
# Run this every time:  bash launch.sh

set -e
cd "$(dirname "$0")"

VENV=".venv-mac/bin/python"
PORT=9222
CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Dedicated Chrome profile for Curie — lives inside the project, keeps Sitdeck login
CHROME_DATA_DIR="$(pwd)/data/chrome-debug-profile"

echo ""
echo "╔══════════════════════════════════╗"
echo "║        CURIE  LAUNCHER           ║"
echo "╚══════════════════════════════════╝"
echo ""

# ── Step 1: Ensure Chrome is running with the debug port ─────────────────────
if curl -s "http://localhost:$PORT/json" > /dev/null 2>&1; then
    echo "[Chrome] Debug port $PORT is active — reusing existing session."
else
    echo "[Chrome] Starting Chrome with debug port..."

    # Kill any existing Chrome (ignore errors if not running)
    pkill -x "Google Chrome" 2>/dev/null || true
    sleep 2

    mkdir -p "$CHROME_DATA_DIR"

    # Chrome 115+ requires --user-data-dir to be non-default for remote debugging
    "$CHROME_BIN" \
        --remote-debugging-port=$PORT \
        --user-data-dir="$CHROME_DATA_DIR" \
        --no-first-run \
        --disable-session-crashed-bubble \
        > /tmp/curie-chrome.log 2>&1 &

    echo "[Chrome] Waiting for debug port (up to 30s)..."
    for i in $(seq 1 30); do
        sleep 1
        if curl -s "http://localhost:$PORT/json" > /dev/null 2>&1; then
            echo "[Chrome] Ready on port $PORT."
            break
        fi
        if [ $i -eq 30 ]; then
            echo ""
            echo "[Chrome] ERROR: Debug port did not open. Chrome log:"
            cat /tmp/curie-chrome.log | head -10
            exit 1
        fi
    done

    # First-time setup: let user log into private sites
    FIRST_RUN_FLAG="$CHROME_DATA_DIR/.logged_in"
    if [ ! -f "$FIRST_RUN_FLAG" ]; then
        echo ""
        echo "  ┌─────────────────────────────────────────────────────┐"
        echo "  │  FIRST-TIME SETUP                                   │"
        echo "  │  Chrome opened a fresh window.                      │"
        echo "  │  → Log into Sitdeck (and any other private sites)   │"
        echo "  │  → Then press Enter here to continue.               │"
        echo "  └─────────────────────────────────────────────────────┘"
        read -r
        touch "$FIRST_RUN_FLAG"
    fi
fi

# ── Step 2: Warm / refresh the private source cache ──────────────────────────
echo ""
echo "[Cache] Refreshing private sources..."
$VENV warm_cache.py

# ── Step 3: Start Curie ───────────────────────────────────────────────────────
echo ""
echo "[Curie] Starting..."
echo ""
$VENV start.py
