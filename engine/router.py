"""Single-pass router — one Ollama REST call per turn (think: false enforced).

Decides 'tool call vs conversation' AND fills parameters in one generation.
Uses prompt-based routing (ACTION: prefix) for compatibility with all models —
no Ollama native tools/function-calling protocol needed.

Returns (tool_name, params, reply_text):
  - tool_name + params set  → dispatch that tool
  - reply_text set          → conversational reply, speak directly
  - all empty               → orchestrator falls back to stream_reply
"""
from __future__ import annotations
import json
import requests

import settings


_TOOL_PROMPT_SUFFIX = """
TOOLS AVAILABLE:
  open_app(app_name)              — launch a desktop app OR navigate to a system settings pane
  close_app(app_name)             — close/quit a running app or browser
  open_url(url)                   — open a website in the browser
  web_search(query)               — search the web for any fact, price, spec, or news
  weather_report(city, time)      — get live weather for a city (today or tomorrow ONLY)
  gmail_read(count)               — read N most recent emails from inbox (default 5)
  gmail_send(to, subject, body)   — send an email to someone
  gmail_search(query, count)      — search emails by keyword, sender, or subject
  gmail_contacts()                — who the user has been emailing with recently, and about what
  file_control(action, path, name) — files/folders: action one of create_folder, create_file,
                                     list, delete, move, copy, rename, find; path is a place
                                     like "desktop", "documents", "downloads"
  shutdown()                      — turn Curie herself off / stop listening

WHEN TO USE EACH TOOL:
  open_app   : "open X", "launch X", "start X", "go to X settings".
               Examples: "open spotify", "open battery settings", "go to wifi settings".
  close_app  : "close X", "quit X", "shut X", "kill X" where X is an app or browser.
               Examples: "close the browser", "close chrome", "close spotify", "quit the app".
               Use app_name = the thing to close (e.g. "browser", "chrome", "spotify").
               NEVER confuse close_app with shutdown — shutdown turns off CURIE, not apps.
  open_url   : "go to [website]", "open the website", gives a URL.
  web_search : factual question (specs, prices, current events, people, multi-day weather).
  weather_report : ONLY for today/tomorrow weather. "What's the weather in Delhi?"
                   For "next X days" or "X-day forecast" → use web_search instead.
  gmail_read : "check my emails", "read my inbox", "any new emails", "what's in my email?",
               "read my mail", "show me my emails". Use count = number user mentions (default 5).
  gmail_send : "send email to X", "email X saying Y", "compose email to X about Y".
               Extract: to = email address or name, subject = topic, body = message content.
  gmail_search : "search my email for X", "find emails from X", "any emails about X",
                 "do I have an email from X". Use query = what to search for.
                 For a PERSON/SENDER, use Gmail syntax: query = "from:rahul".
  gmail_contacts : "who have I been emailing", "who am I in touch with over email",
                   "what's going on in my emails with X".
  file_control : "create a folder", "make a new folder on my desktop", "list my downloads",
                 "delete the file X", "rename X to Y". NEVER claim a file/folder action was
                 done without calling this tool.
                 Example: ACTION: {"tool": "file_control", "params": {"action": "create_folder", "path": "desktop", "name": "Projects"}}
  shutdown   : turns off CURIE HERSELF. Only when user says "turn yourself off", "stop curie",
               "curie stop", "goodbye curie", "shut down curie", "exit curie".

PEOPLE RULE:
  If the user asks about a PERSON you have no fetched information about
  → use web_search(query="<person's name>"). NEVER invent where someone lives,
  what they do, or their social media. NEVER claim you remember things about a
  person that are not in the provided context.

NEVER call a tool for personal statements ("I feel…", "my name is…") → reply conversationally.

PRONOUN RESOLUTION RULE:
  When user says "the same", "it", "that", "them", "there" — resolve from the MOST RECENT
  conversation turns (shown in context as "Recent conversation:"), NOT from long-term memory.
  Example: if recent conversation is about transport companies, "search for the same" means
  search for transport companies — not something unrelated from long-term memory.

CRITICAL — LIVE DATA RULE:
  If the user asks about current prices (gold, bitcoin, stocks), news, or real-time events:
  → ALWAYS use web_search. NEVER answer from your training data — it is outdated and wrong.
  Examples that MUST use web_search:
    "What is the price of gold?"       → web_search(query="current gold price today")
    "How is the market doing?"         → web_search(query="stock market today")
    "What's happening in the Middle East?" → web_search(query="Middle East news today")

Tool call format — ENTIRE response must be exactly one line:
ACTION: {"tool": "tool_name", "params": {"key": "value"}}

If no tool needed, reply in plain text (no JSON, no ACTION prefix). Keep replies short and casual.
"""


def _load_system_prompt() -> str:
    p = settings.BASE_DIR / "core" / "prompt.txt"
    try:
        base = p.read_text(encoding="utf-8").strip()
    except Exception:
        base = "You are Curie, a concise local voice assistant."
    return base + "\n" + _TOOL_PROMPT_SUFFIX


def _get_content(msg: dict) -> str:
    """Extract reply text, falling back to thinking field when content is empty (qwen3 quirk)."""
    txt = msg.get("content") or ""
    # qwen3: when think=True, the actual reply may be empty and thinking in a separate key
    if not txt.strip():
        txt = msg.get("thinking") or ""
        if txt:
            # thinking-only response — treat as conversational fallback
            # strip any internal think tags and return the last useful sentence
            if "</think>" in txt:
                txt = txt.split("</think>")[-1]
            # If thinking contains ACTION line, surface it
            for line in txt.splitlines():
                if line.strip().upper().startswith("ACTION:"):
                    txt = line.strip()
                    break
            else:
                # Fall through — treat as empty so orchestrator uses stream fallback
                return ""
    if "</think>" in txt:
        txt = txt.split("</think>")[-1]
    return txt.strip()


def _extract_first_json(s: str) -> dict | None:
    """Extract the first complete {...} object from a string, ignoring trailing junk."""
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(s[start : i + 1])
                except Exception:
                    return None
    return None


def _parse_action(text: str) -> tuple[str | None, dict | None]:
    """Parse  ACTION: {"tool": ..., "params": {...}}  from the model reply.

    Handles the case where the model outputs multiple ACTION calls on one line
    (e.g. user asks to do two things). We take only the FIRST valid one.
    """
    for line in text.splitlines():
        line = line.strip()
        if "ACTION:" not in line.upper():
            continue
        # Find ACTION: (case-insensitive) and grab everything after it
        idx = line.upper().find("ACTION:")
        rest = line[idx + len("ACTION:"):].strip()
        data = _extract_first_json(rest)
        if data is None:
            continue
        tool = data.get("tool", "").strip()
        params = data.get("params", {})
        if tool and isinstance(params, dict):
            return tool, params
    return None, None


class Router:
    def __init__(self):
        self._url    = settings.OLLAMA_HOST.rstrip("/") + "/api/chat"
        self._system = _load_system_prompt()

    def route(
        self, user_text: str, context: str = ""
    ) -> tuple[str | None, dict | None, str]:
        """
        One REST call. Returns (tool_name, params, conv_reply).
        Raises requests.HTTPError / ConnectionError on failure — caller must handle.
        """
        system = self._system + (f"\n\n{context}" if context else "")
        payload = {
            "model":      settings.LLM_MODEL,
            "messages":   [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_text},
            ],
            "stream":     False,
            "keep_alive": settings.LLM_KEEP_ALIVE,
            "think":      False,
            "options": {
                "num_predict": 120,
                "num_ctx":     settings.LLM_NUM_CTX,
                "temperature": 0.3,   # low temp for reliable tool routing
            },
        }

        r = requests.post(self._url, json=payload, timeout=120)
        r.raise_for_status()
        content = _get_content(r.json().get("message", {}))

        tool_name, params = _parse_action(content)
        if tool_name:
            return tool_name, params, ""

        return None, None, content


# standalone test:  python -m engine.router
if __name__ == "__main__":
    r = Router()
    tests = [
        "Open Spotify",
        "What's the weather in Delhi today?",
        "Search for latest AI news",
        "Hello, how are you?",
        "What is a BMW M8?",
        "What time is it?",
        "My name is Ashish",
    ]
    for t in tests:
        name, params, reply = r.route(t)
        if name:
            print(f"[TOOL]  {t!r:50s} → {name}({params})")
        else:
            print(f"[CHAT]  {t!r:50s} → {reply[:80]!r}")
