"""Top news headlines via RSS — no API key, fully local parsing.

Fetches via requests (custom UA to avoid bot-blocks), parses with feedparser.
"""
import feedparser
import requests

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_FEEDS = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://timesofindia.indiatimes.com/rss.cms",
    "https://feeds.reuters.com/reuters/topNews",
]


def _fetch_feed(url: str) -> list[str]:
    """Fetch and parse one RSS URL. Returns list of title strings."""
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=6)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        return [
            e.title.strip()
            for e in feed.entries
            if getattr(e, "title", "").strip()
        ]
    except Exception:
        return []


def top_headlines(n: int = 3) -> list[str]:
    """Return up to n headline strings from the first reachable feed."""
    for url in _FEEDS:
        titles = _fetch_feed(url)
        if titles:
            return titles[:n]
    return []


# standalone test:  python -m engine.integrations.news
if __name__ == "__main__":
    headlines = top_headlines(5)
    if headlines:
        for h in headlines:
            print("-", h)
    else:
        print("No headlines fetched (check network).")
