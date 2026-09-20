"""Curie entrypoint — local voice assistant with PyQt6 HUD.

Boots the visual HUD on the main thread and the local orchestrator
(VAD → Whisper → qwen → Kokoro) on a daemon thread. No cloud.
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Ensure we always run from the project root regardless of where the user
# invoked Python from, so relative imports and data paths work.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

from face import JarvisUI   # new minimal futuristic face (old HUD: ui.py)
from engine.orchestrator import Orchestrator

_CDP_PORT       = 9222
_CHROME_DATA    = Path(_PROJECT_ROOT) / "data" / "chrome-debug-profile"
_CHROME_BINS    = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
_LOGIN_FLAG     = _CHROME_DATA / ".logged_in"


def _chrome_alive() -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{_CDP_PORT}/json", timeout=2)
        return True
    except Exception:
        return False


def _ensure_chrome():
    """Auto-launch Chrome with debug port so Sitdeck scraping always works.

    - If Chrome is already on port 9222, does nothing.
    - Saves login session in data/chrome-debug-profile — only one-time login
      is ever needed; subsequent runs reuse saved cookies automatically.
    """
    if _chrome_alive():
        print("[Chrome] Debug port active — reusing session.")
        return

    chrome_bin = next((b for b in _CHROME_BINS if Path(b).exists()), None)
    if not chrome_bin:
        print("[Chrome] Not found on this machine — Sitdeck will use headless fallback.")
        return

    _CHROME_DATA.mkdir(parents=True, exist_ok=True)

    subprocess.Popen(
        [chrome_bin,
         f"--remote-debugging-port={_CDP_PORT}",
         f"--user-data-dir={_CHROME_DATA}",
         "--no-first-run",
         "--disable-session-crashed-bubble",
         "--disable-popup-blocking"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait up to 20 s for the debug port to open
    for i in range(20):
        time.sleep(1)
        if _chrome_alive():
            print(f"[Chrome] Ready on port {_CDP_PORT}.")
            _handle_first_login()
            return

    print("[Chrome] Timed out — Sitdeck will use headless fallback this session.")


def _handle_first_login():
    """One-time only: open Sitdeck and wait for user to log in."""
    if _LOGIN_FLAG.exists():
        return  # already logged in, session saved in profile

    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  FIRST-TIME SETUP (one time only)           │")
    print("  │  Sitdeck is opening in Chrome.              │")
    print("  │  Log in, then press Enter to continue.      │")
    print("  └─────────────────────────────────────────────┘")

    # Open sitdeck so the user can see where to log in
    try:
        subprocess.Popen(
            [next(b for b in _CHROME_BINS if Path(b).exists()),
             "--remote-debugging-port=9222",
             f"--user-data-dir={_CHROME_DATA}",
             "https://app.sitdeck.com/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    input("  → Press Enter once you're logged in: ")
    _LOGIN_FLAG.touch()
    print("  Login saved. Curie will use this session automatically from now on.")


def main():
    # Launch Chrome in background (non-blocking) so Curie UI starts immediately
    threading.Thread(target=_ensure_chrome, daemon=True).start()

    ui = JarvisUI()

    def runner():
        ui.wait_for_api_key()           # returns immediately (local build)
        orch = Orchestrator(ui=ui)
        ui.on_window_closed = orch.stop   # closing the window stops her too
        try:
            orch.run()
        except Exception as e:
            print(f"[Curie] orchestrator stopped: {e}")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
