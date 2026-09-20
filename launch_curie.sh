#!/bin/bash
# Watchdog launcher: auto-restarts Curie if she dies unexpectedly (e.g. a rare
# native-library crash deep in PortAudio/CoreAudio — outside Python's own
# exception handling, so this is the safety net for it). A NORMAL exit
# (closing the window, exit code 0) is respected and does NOT restart.
cd /Users/viveksagar/Desktop/Jarvis
if pgrep -f "Desktop/Jarvis/main.py" > /dev/null; then
    echo "Curie is already running."
    exit 0
fi

while true; do
    echo "★ Starting Curie…"
    ./.venv-mac/bin/python main.py
    code=$?
    if [ "$code" -eq 0 ]; then
        echo "Curie closed normally — not restarting."
        break
    fi
    echo "⚠️  Curie stopped unexpectedly (exit code $code) — restarting in 2s…"
    sleep 2
done
