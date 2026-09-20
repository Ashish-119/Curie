"""Web search via DuckDuckGo — no cloud LLM, no API key.

Returns (text, top_url) so the orchestrator can open the browser in parallel.
"""
try:
    from ddgs import DDGS          # package renamed duckduckgo_search → ddgs
except ImportError:
    from duckduckgo_search import DDGS


def web_search(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> tuple[str, str | None]:
    """Returns (summary_text, top_result_url)."""
    query = (parameters or {}).get("query", "").strip()
    if not query:
        return "Please provide a search query.", None

    print(f"[WebSearch] {query!r}")

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("href",   ""),
                })

        if not results:
            return f"No results found for: {query}", None

        top_url = results[0].get("url") or None

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results[:3], 1):
            if r["title"]:   lines.append(f"{i}. {r['title']}")
            if r["snippet"]: lines.append(f"   {r['snippet']}")
            lines.append("")
        return "\n".join(lines).strip(), top_url

    except Exception as e:
        print(f"[WebSearch] failed: {e}")
        return f"Search failed: {e}", None
