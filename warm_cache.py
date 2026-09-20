"""One-time cache warmer for private authenticated sources.

STEP 1 — Quit Chrome fully (Cmd+Q), then relaunch it with the debug port:
    open -a "Google Chrome" --args --remote-debugging-port=9222

STEP 2 — In Chrome: log in to all your private sites as normal.

STEP 3 — Run this script:
    .venv-mac/bin/python warm_cache.py

After that, Curie reads from the cache and auto-refreshes in the background
using the same Chrome debug connection (Chrome must stay open).
"""
import sys
import subprocess
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings
from actions.private_search import _load_config, _get_page_text, _CDP_PORT


def _check_chrome_debug():
    """Return True if Chrome is reachable on the debug port."""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://localhost:{_CDP_PORT}/json", timeout=2)
        return True
    except Exception:
        return False


def main():
    print("=" * 60)
    print("Curie — Private Source Cache Warmer")
    print("=" * 60)
    print()

    if not _check_chrome_debug():
        print("Chrome remote debugging is NOT active on port", _CDP_PORT)
        print()
        print("Do these steps in order, then re-run this script:")
        print()
        print("  1. Quit Chrome:  osascript -e 'quit app \"Google Chrome\"'")
        print(f"  2. Relaunch:     open -a 'Google Chrome' --args --remote-debugging-port={_CDP_PORT}")
        print("  3. Log into your private sites in the Chrome window that opens")
        print("  4. Verify port:  curl -s http://localhost:9222/json | python3 -c \"import sys,json; print([t['title'] for t in json.load(sys.stdin)])\"")
        print("  5. Re-run:       .venv-mac/bin/python warm_cache.py")
        sys.exit(1)
    else:
        print(f"Chrome debug port {_CDP_PORT} is ACTIVE — using your live session.")
        print()

    cfg     = _load_config()
    sources = cfg.get("sources", [])
    if not sources:
        print("No sources configured in config/private_sources.json")
        return

    for src in sources:
        url          = (src.get("url") or "").strip()
        description  = src.get("description", url)
        requires_auth = bool(src.get("requires_auth", False))

        if not url:
            continue

        print(f"Scraping: {description}")
        print(f"  URL         : {url}")
        print(f"  Needs auth  : {'yes' if requires_auth else 'no'}")

        # Force fresh scrape (refresh_hours=0 bypasses cache)
        text = _get_page_text(url, refresh_hours=0, requires_auth=requires_auth)

        if text:
            # Quick auth check: login pages are usually short
            line_count = len([l for l in text.splitlines() if l.strip()])
            preview    = text[:200].replace("\n", " ")
            print(f"  Got {len(text)} chars / {line_count} lines")
            print(f"  Preview: {preview!r}…")
            if line_count < 10:
                print("  WARNING: Very little content — page may be showing a login screen.")
                print(f"           Make sure you're logged into {url} in Chrome first.")
            else:
                print("  Cached OK.")
        else:
            print(f"  ERROR: Got no content — check network and Chrome session.")

        print()

    print("Done! To start Curie:")
    print("  .venv-mac/bin/python start.py")
    print()
    print("Keep Chrome running — Curie reconnects to it for cache refreshes.")


if __name__ == "__main__":
    main()
