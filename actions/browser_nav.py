"""In-page browser navigation via the debug Chrome (CDP port 9222).

Curie can act INSIDE the page she opened: "play the third video" on YouTube,
"click the second result" on Google. Uses the most recently active http tab.
"""
from __future__ import annotations

import requests

_CDP = "http://127.0.0.1:9222"

# CSS selectors for "the Nth clickable item" per site
_SITE_ITEMS = {
    "youtube.com": (
        "ytd-rich-item-renderer a#thumbnail, "          # home grid
        "ytd-video-renderer a#thumbnail, "              # search results
        "ytd-compact-video-renderer a#thumbnail"        # sidebar
    ),
    "google.com": "#search a h3",                        # search results
}


def _active_tab():
    try:
        targets = requests.get(f"{_CDP}/json/list", timeout=3).json()
    except Exception:
        return None
    return next((t for t in targets
                 if t.get("type") == "page" and t.get("url", "").startswith("http")), None)


def click_nth_item(n: int, kind: str = "video") -> str:
    """Click the Nth video/result on the active tab. n is 1-based."""
    tab = _active_tab()
    if tab is None:
        return ("I can only click inside pages in my Chrome — and it isn't "
                "running right now.")
    url = tab.get("url", "")
    domain = next((d for d in _SITE_ITEMS if d in url), None)
    if domain is None:
        return f"I don't know how to pick {kind}s on this page yet — I can do YouTube and Google."

    selector = _SITE_ITEMS[domain]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(_CDP, timeout=5_000)
            try:
                page = None
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        if pg.url == url:
                            page = pg
                            break
                    if page:
                        break
                if page is None:
                    return "I couldn't attach to the tab."
                page.bring_to_front()
                items = page.locator(selector)
                count = items.count()
                if count == 0:
                    return f"I don't see any {kind}s on this page."
                if n > count:
                    return f"There are only {count} {kind}s visible — say a smaller number."
                items.nth(n - 1).click()
                page.wait_for_timeout(1_500)
                title = page.title()
            finally:
                browser.close()   # disconnect only
        return f"Playing it — {title[:60]}." if domain == "youtube.com" else f"Opened it — {title[:60]}."
    except Exception as e:
        print(f"[browser-nav] failed: {e}")
        return f"I couldn't click {kind} number {n} — the page may still be loading."


def scroll_page(direction: str = "down") -> str:
    """Scroll the active tab up/down one screen."""
    tab = _active_tab()
    if tab is None:
        return "My Chrome isn't running, so I can't scroll it."
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(_CDP, timeout=5_000)
            try:
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        if pg.url == tab["url"]:
                            delta = 700 if direction == "down" else -700
                            pg.mouse.wheel(0, delta)
                            return f"Scrolled {direction}."
            finally:
                browser.close()
        return "I couldn't attach to the tab."
    except Exception as e:
        print(f"[browser-nav] scroll failed: {e}")
        return "Couldn't scroll there."
