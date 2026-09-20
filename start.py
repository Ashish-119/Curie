"""Curie headless launcher — no GUI window needed.

Use this if main.py (PyQt6 window) doesn't open, or for voice-only testing.
Ctrl+C to quit.  Type commands directly into the terminal as well.
"""
import sys
import threading
import time

from engine.orchestrator import Orchestrator


def _stdin_loop(orch: Orchestrator):
    """Allow typed commands from terminal as a fallback / test mode."""
    print("[Curie] Type a message and press Enter to send (or just speak).")
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip():
            threading.Thread(target=orch.handle_text, args=(line.strip(),), daemon=True).start()


def _private_source_refresh_loop(stop: threading.Event):
    """Background thread that refreshes private source caches on schedule."""
    try:
        from actions.private_search import _load_config, _get_page_text
    except Exception:
        return

    while not stop.is_set():
        try:
            cfg = _load_config()
            for src in cfg.get("sources", []):
                url          = (src.get("url") or "").strip()
                refresh_h    = float(src.get("refresh_hours", 6))
                requires_auth = bool(src.get("requires_auth", False))
                if url:
                    # _get_page_text only re-scrapes if the cache is stale
                    _get_page_text(url, refresh_h, requires_auth)
        except Exception as e:
            print(f"[PrivateSearch] background refresh error: {e}")

        # Sleep in small intervals so stop event is checked promptly
        for _ in range(60):
            if stop.is_set():
                break
            time.sleep(1)


def main():
    print("[Curie] Starting headless mode (no window)...")
    try:
        orch = Orchestrator()
    except Exception as e:
        print(f"[Curie] Failed to load models: {e}")
        sys.exit(1)

    stop = threading.Event()

    # Typed input in parallel with voice loop
    threading.Thread(target=_stdin_loop, args=(orch,), daemon=True).start()

    # Background private-source cache refresher
    threading.Thread(target=_private_source_refresh_loop, args=(stop,), daemon=True).start()

    try:
        orch.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        orch.stop()
        print("\n[Curie] Stopped.")


if __name__ == "__main__":
    main()
