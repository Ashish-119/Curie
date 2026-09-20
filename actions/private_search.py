"""Private-source scraper — checked BEFORE internet search.

Flow:
  1. Load hardcoded URLs from config/private_sources.json
  2. Match the query against each source's user-defined 'topics' list
  3. If a source matches:
       - Plain sites  : requests + BeautifulSoup (fast, no JS)
       - Auth/SPA sites: Playwright headless + Chrome cookies (handles login + JS)
  4. Return the scraped content for the LLM to summarise
  5. Return None if no source matches → fall back to internet DDG search

Privacy:
  - Only the configured URLs are ever contacted — no random internet traffic.
  - No credentials are stored. Auth uses your LIVE Chrome browser cookies.
  - Cache lives in data/scrape_cache/ (local disk, never uploaded).

How to add a private URL (edit config/private_sources.json):
  {
    "url": "https://yoursite.com/dashboard",
    "description": "Human label for logs",
    "topics": ["topic phrase", "another phrase"],   ← controls when this URL is used
    "requires_auth": true,                          ← set true for login-protected sites
    "refresh_hours": 0.1,                           ← re-scrape interval (0.1 = 6 min)
    "max_content_chars": 2000
  }
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import settings

_SOURCES_FILE = settings.BASE_DIR / "config" / "private_sources.json"
_CACHE_DIR    = settings.DATA_DIR / "scrape_cache"
_UA           = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Words too generic to use as topic-matching signals
_TOPIC_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "what", "which",
    "have", "been", "will", "more", "some", "about", "into", "their",
    "news", "tell", "find", "show", "know", "current", "latest", "today",
    "events", "make", "give", "said", "says", "also", "there", "here",
}


# ── config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": []}


# ── disk cache ────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(url.encode()).hexdigest()[:14]
    return _CACHE_DIR / f"{key}.txt"


def _cache_age_hours(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 3600
    except Exception:
        return float("inf")


# ── plain scraping (no auth) ──────────────────────────────────────────────────

def _scrape_plain(url: str) -> str:
    """Fetch URL with requests + BeautifulSoup. Works for public pages."""
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "aside", "form", "button", "iframe", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception as e:
        print(f"[PrivateSearch] plain scrape failed ({url}): {e}")
        return ""


# ── authenticated SPA scraping via Playwright ─────────────────────────────────

_CDP_PORT        = 9222   # Chrome remote debugging port
_CHROME_PROFILE  = settings.BASE_DIR / "data" / "chrome-debug-profile"


def _ensure_cdp_tab(url: str) -> bool:
    """Return True if debug Chrome is reachable; make sure it has ≥1 real tab.

    A windowless Chrome (user closed the window, process lingering) makes
    Playwright's connect_over_cdp fail with a cryptic 'Browser context
    management is not supported' error — creating a tab via the CDP HTTP API
    first fixes it.
    """
    base = f"http://127.0.0.1:{_CDP_PORT}"
    try:
        targets = requests.get(f"{base}/json/list", timeout=3).json()
        real_tabs = [t for t in targets
                     if t.get("type") == "page" and t.get("url", "").startswith("http")]
        if not real_tabs:
            print("[PrivateSearch] debug Chrome has no tabs — opening one via CDP")
            try:
                requests.put(f"{base}/json/new?{url}", timeout=5)
            except Exception:
                requests.get(f"{base}/json/new?{url}", timeout=5)   # pre-111 Chrome
            time.sleep(1.5)
        return True
    except Exception:
        return False


def _goto_and_poll(page, url: str, max_wait_s: float, label: str) -> str:
    """Navigate and poll until real article text renders (SPAs load slowly)."""
    from playwright.sync_api import TimeoutError as PWTimeout
    print(f"[PrivateSearch] ({label}) navigating to {url} …")
    page.goto(url, timeout=60_000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    waited = 0.0
    text = ""
    while True:
        try:
            raw  = page.inner_text("body")
            text = re.sub(r"\n{3,}", "\n\n", raw).strip()
        except Exception:
            text = ""
        if _looks_like_real_content(text):
            print(f"[PrivateSearch] ({label}) content rendered after {waited:.0f}s")
            break
        if waited >= max_wait_s:
            print(f"[PrivateSearch] ({label}) gave up after {waited:.0f}s "
                  f"(page still thin — login page or very slow load)")
            break
        page.wait_for_timeout(2_500)
        waited += 2.5
    return text


def _scrape_spa(url: str) -> str:
    """Render a JavaScript SPA using the user's Chrome session.

    Strategy (tried in order):
      1. Connect to Chrome via CDP (port 9222) — uses the live authenticated
         session. Requires Chrome running with --remote-debugging-port=9222.
      2. Headless persistent context with the saved Chrome profile —
         reuses cookies/localStorage from the last login.
      3. VISIBLE (headed) window with the same profile — the user can watch it
         load and, if a login page appears, log in right there. The session is
         saved to the profile, so future runs work headlessly again.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[PrivateSearch] Playwright not installed; falling back to plain scrape")
        return _scrape_plain(url)

    load_wait  = float(getattr(settings, "PRIVATE_LOAD_WAIT_S", 30))
    login_wait = float(getattr(settings, "PRIVATE_LOGIN_WAIT_S", 60))
    text = ""
    try:
        with sync_playwright() as pw:
            # ── Strategy 1: attach to the running Chrome instance ─────────
            # NOTE: must be 127.0.0.1, NOT localhost — Chrome binds the debug
            # port on IPv4 only, and localhost resolves to IPv6 first on macOS,
            # which made this connect fail even while Chrome was running.
            try:
                if not _ensure_cdp_tab(url):
                    raise ConnectionError("debug Chrome not reachable")
                browser = pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{_CDP_PORT}", timeout=5_000
                )
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                # Reuse an already-open tab on this site instead of piling up tabs
                domain = url.split("//")[-1].split("/")[0]
                page = next((p for p in ctx.pages if domain in (p.url or "")), None)
                if page is None:
                    page = ctx.new_page()
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                print(f"[PrivateSearch] connected to live Chrome (CDP port {_CDP_PORT})")
                try:
                    text = _goto_and_poll(page, url, load_wait, "CDP")
                finally:
                    browser.close()   # disconnects only — user's Chrome stays open
                if _looks_like_real_content(text):
                    return text
            except Exception:
                print(f"[PrivateSearch] Chrome not on port {_CDP_PORT}")

            _profile_args = ["--no-first-run", "--disable-popup-blocking",
                             "--disable-blink-features=AutomationControlled"]

            # ── Strategy 2: saved profile, headless ───────────────────────
            if _CHROME_PROFILE.exists():
                try:
                    persistent = pw.chromium.launch_persistent_context(
                        str(_CHROME_PROFILE), headless=True,
                        args=_profile_args, timeout=15_000,
                    )
                    try:
                        page = persistent.pages[0] if persistent.pages else persistent.new_page()
                        print("[PrivateSearch] using saved Chrome profile (headless)")
                        text = _goto_and_poll(page, url, load_wait, "headless profile")
                    finally:
                        persistent.close()
                    if _looks_like_real_content(text):
                        return text
                except Exception as e2:
                    print(f"[PrivateSearch] headless profile failed ({e2})")

            # ── Strategy 3: saved profile, VISIBLE window ─────────────────
            # Headless saw a login/blank page. Open a real window the user can
            # see — if it's a login page they can sign in NOW; we keep polling
            # and read the content the moment it appears.
            # Chrome releases the profile lock a moment after the headless
            # context closes — wait for it, and retry once if it's still held.
            print("[PrivateSearch] opening a VISIBLE browser window — "
                  "if it shows a login page, log in there; I'm watching for content…")
            for attempt in (1, 2):
                time.sleep(2.0 * attempt)
                try:
                    persistent = pw.chromium.launch_persistent_context(
                        str(_CHROME_PROFILE), headless=False,
                        args=_profile_args, timeout=20_000,
                    )
                    try:
                        page = persistent.pages[0] if persistent.pages else persistent.new_page()
                        text = _goto_and_poll(page, url, login_wait, "visible window")
                    finally:
                        persistent.close()
                    break
                except Exception as e3:
                    print(f"[PrivateSearch] visible window failed "
                          f"(attempt {attempt}/2: {e3})")

    except Exception as e:
        print(f"[PrivateSearch] Playwright scrape failed ({url}): {e}")

    print(f"[PrivateSearch] scraped {len(text)} chars")
    return text


# ── topic matching ────────────────────────────────────────────────────────────

def _topic_keywords(topic: str) -> list[str]:
    """Extract distinctive keywords (≥5 chars, non-generic) from a topic phrase."""
    return [w for w in re.findall(r"[a-z]{5,}", topic.lower())
            if w not in _TOPIC_STOP]


def _query_matches_topics(query: str, topics: list[str]) -> bool:
    """Return True if any distinctive topic keyword appears whole-word in the query."""
    query_lower = query.lower()
    for topic in topics:
        for kw in _topic_keywords(topic):
            if re.search(r"\b" + re.escape(kw) + r"\b", query_lower):
                return True
    return False


# ── content quality check ─────────────────────────────────────────────────────

def _looks_like_real_content(text: str) -> bool:
    """True when the page has real article text (login/loading pages are thin)."""
    real_lines = [l for l in text.splitlines() if len(l.strip()) > 30]
    return len(real_lines) >= 5


# ── excerpt extraction ────────────────────────────────────────────────────────

def _best_content(text: str, max_chars: int) -> str:
    """Return the most content-rich portion (skip short boilerplate lines)."""
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
    if not paragraphs:
        return text[:max_chars]
    buf = ""
    for p in paragraphs:
        candidate = (buf + "\n\n" + p).strip()
        if len(candidate) > max_chars:
            break
        buf = candidate
    return buf.strip() or text[:max_chars]


# ── cache-aware fetch ─────────────────────────────────────────────────────────

def _get_page_text(url: str, refresh_hours: float, requires_auth: bool) -> str:
    """Return page text from cache if fresh, otherwise (re-)scrape."""
    cp = _cache_path(url)
    if cp.exists() and _cache_age_hours(cp) < refresh_hours:
        age_min = _cache_age_hours(cp) * 60
        print(f"[PrivateSearch] using cache ({age_min:.1f} min old)")
        return cp.read_text(encoding="utf-8", errors="ignore")

    print(f"[PrivateSearch] cache stale or missing — scraping …")
    text = _scrape_spa(url) if requires_auth else _scrape_plain(url)
    # Only cache REAL content — caching a login/loading page would make every
    # retry within refresh_hours return the same useless page.
    if text and _looks_like_real_content(text):
        cp.write_text(text, encoding="utf-8")
        print(f"[PrivateSearch] cached {len(text)} chars to {cp.name}")
    return text


# ── public API ────────────────────────────────────────────────────────────────

def search_private(query: str) -> tuple[str, str] | tuple[None, str] | None:
    """Check private sources first.

    Returns:
      (content, url)   — topic matched and content scraped successfully
      (None, url)      — topic matched but content thin (login page / no Chrome CDP)
                         caller should open the URL in browser then fall back to internet
      None             — no topic matched at all
    """
    cfg     = _load_config()
    sources = cfg.get("sources", [])
    if not sources:
        return None

    for src in sources:
        url          = (src.get("url") or "").strip()
        topics       = src.get("topics") or []
        refresh_h    = float(src.get("refresh_hours", 6))
        max_chars    = int(src.get("max_content_chars", 2000))
        requires_auth = bool(src.get("requires_auth", False))
        description  = src.get("description", url)

        if not url:
            continue

        if not _query_matches_topics(query, topics):
            print(f"[PrivateSearch] no topic match for '{description}'")
            continue

        print(f"[PrivateSearch] topic match → fetching: {description}")
        text = _get_page_text(url, refresh_h, requires_auth)
        if not text:
            # Scrape totally failed — still return (None, url) so browser opens
            return (None, url)

        # Reject login/redirect pages — real content has far more lines
        if not _looks_like_real_content(text):
            print(f"[PrivateSearch] ⚠️  Sitdeck shows a login page — session expired or not logged in.")
            print(f"[PrivateSearch]    Open Chrome with:  data/chrome-debug-profile")
            print(f"[PrivateSearch]    Log into {url.split('/')[2]}, then restart Curie.")
            return (None, url)  # open browser so user can log in

        excerpt = _best_content(text, max_chars)
        if excerpt:
            domain = url.split("//")[-1].split("/")[0]
            return (f"[Source: {domain}]\n{excerpt}", url)

    return None


# standalone test:  python -m actions.private_search
if __name__ == "__main__":
    cases = [
        ("→ should HIT private source", "what is happening around the world"),
        ("→ should HIT private source", "latest breaking news today"),
        ("→ should use internet",        "Lamborghini Revuelto specifications"),
        ("→ should use internet",        "what is the weather in Delhi"),
    ]
    for label, q in cases:
        print(f"\n{'='*60}\n[{label}]\nQuery: {q!r}")
        result = search_private(q)
        if result:
            print(f"PRIVATE SOURCE:\n{result[:600]}…")
        else:
            print("→ falls back to internet search")
