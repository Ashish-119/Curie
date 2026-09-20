"""Read what's on screen — currently the active browser tab via Chrome CDP.

Uses the same debug Chrome (port 9222) as private_search. Returns the visible
text of the most recently active http(s) tab so the LLM can read/summarise it
aloud. Honest failure messages when no browser is reachable.
"""
from __future__ import annotations

import re

import requests

_CDP_PORT  = 9222
_MAX_CHARS = 8000   # fits the LLM context alongside the system prompt


def read_browser_page() -> tuple[str | None, str]:
    """Return (page_text, message). page_text is None when nothing was readable."""
    base = f"http://127.0.0.1:{_CDP_PORT}"
    try:
        targets = requests.get(f"{base}/json/list", timeout=3).json()
    except Exception:
        return None, ("I can't see your screen directly yet — I can only read browser "
                      "tabs, and my connection to Chrome isn't running right now.")

    # /json/list is ordered most-recently-active first
    tab = next((t for t in targets
                if t.get("type") == "page" and t.get("url", "").startswith("http")), None)
    if tab is None:
        return None, "There's no web page open in Chrome for me to read."

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"{base}", timeout=5_000)
            try:
                page = None
                for ctx in browser.contexts:
                    for p in ctx.pages:
                        if p.url == tab["url"]:
                            page = p
                            break
                    if page:
                        break
                if page is None:
                    return None, "I couldn't attach to the active tab."
                raw = page.inner_text("body")
            finally:
                browser.close()   # disconnect only — Chrome stays open
        text = re.sub(r"\n{3,}", "\n\n", raw).strip()[:_MAX_CHARS]
        if len(text) < 40:
            return None, "The current tab looks empty — nothing to read there."
        title = tab.get("title", "") or tab["url"].split("/")[2]
        return text, f"Reading the tab: {title}"
    except Exception as e:
        print(f"[read_screen] failed: {e}")
        return None, ("I couldn't read the tab — my Chrome connection glitched. "
                      "Try again in a moment.")
