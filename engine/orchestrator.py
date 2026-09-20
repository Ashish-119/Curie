"""Curie orchestrator — the local voice loop.

Boot sequence:
    load models → speak startup briefing → (wake word) → VAD → STT → Router
    → (tool dispatch | conv reply) → TTS → persist memory

Barge-in: sample mic ONLY in the silent gap between sentences (zero TTS bleed).
Memory: structured long-term store (identity/preferences/relationships/wishes/
        projects/notes) + semantic ChromaDB recall.
"""
import random
import re
import threading
import webbrowser
from pathlib import Path

import numpy as np

import settings

# Spoken only while web search / slow tool is running (NOT for conversational replies)
_SEARCH_FILLERS = [
    "On it.",
    "Give me a sec.",
    "Checking.",
    "One moment.",
]

# ── shutdown: regex catches STT variants ("turn yourself off", "curie stop", etc.)
_SHUTDOWN_RE = re.compile(
    r"("
    r"(turn|switch)\s+.{0,15}\s+off\b|"           # "turn yourself off", "turn it off"
    r"shut(down|)\s*(curie|yourself|it|her)?\b|"
    r"shut\s+(down|yourself|curie|it|her)\b|"
    r"curie\s*(stop|off|quit|exit|sleep|bye|goodbye|shutdown|shut\s*down|turn\s*off)|"
    r"(stop|exit|quit)\s+curie|"
    r"(goodbye|good\s*night|good\s*bye)\s+curie|"
    r"go\s+to\s+sleep\s*curie|"
    r"stop\s+listening|"
    r"power\s+off|"
    r"go\s+offline|"
    r"only\s+yourself\s+off|"                      # STT variant of "turn yourself off"
    r"(turn|switch)\s+(yourself|curie|her|it)\s+off"
    r")",
    re.IGNORECASE,
)

# ── live-data bypass: any query about current real-world facts → force web_search ──
# This prevents the LLM from hallucinating news, prices, or events from training data.
_LIVE_DATA_RE = re.compile(
    r"("
    # News / current events
    r"happening\s+around\s+the\s+world|"
    r"what.{0,10}happening\s+in\s+the\s+world|"
    r"what.{0,10}going\s+on\s+in\s+the\s+world|"
    r"what.{0,15}happening\s+in\s+(the\s+)?(middle\s+east|ukraine|russia|gaza|china|india|"
    r"pakistan|africa|europe|us\b|usa|uk\b|japan|iran|israel|brazil|france|germany)|"
    r"(latest|breaking|today.s|morning|evening|top)\s+news|"
    r"(world|global|international)\s+news|"
    r"news\s+(today|update|headlines?)|"
    r"top\s+(stories|headlines?)|"
    r"(tell|read|give)\s+me\s+(the\s+)?news|"
    r"(tell|read|give)\s+me\s+(the\s+)?top\s+(\d+|[a-z]+)\b|"
    r"top\s+(\d+|[a-z]+)\s+(news|stories|headlines?)|"
    r"(latest|top|give\s+me)\s+(\d+|[a-z]+)\s+(news|stories|headlines?)|"
    r"what\s+happened\s+today|"
    r"current\s+events|"
    r"\bbriefing\b|brief\s+me\b|"
    r"read\s+(out\s+)?(the\s+)?news|news\s+for\s+me|"
    r"(live|latest|today.?s)\s+scores?\b|scores?\b.{0,20}(football|cricket|match)|"
    r"(football|cricket|soccer|tennis).{0,20}(scores?|results?|match(es)?\s+today)|"
    # Financial — prices must always be fetched, never from training data
    r"(current|today.s|live|latest|real.time)\s+(price|rate|value|cost)\s+of\s+\w+|"
    r"(price|cost|rate|value)\s+of\s+(gold|silver|bitcoin|btc|ethereum|eth|crypto|oil|platinum|copper)|"
    r"(gold|silver|bitcoin|btc|ethereum|eth|crypto|oil|stock|market|nasdaq|dow|nifty|sensex)"
    r".{0,20}(price|rate|value|today|now|going|doing|performance|worth)|"
    r"(how.{0,5}s|what.{0,5}s)\s+.{0,10}(market|stocks?|gold|bitcoin|btc|nasdaq|dow|sensex|nifty|crypto|oil)"
    r".{0,20}\?|"
    r"(stock\s+market|crypto\s+market|market).{0,20}(going|doing|up|down|today|now)|"
    # Multi-day weather — weather_report only handles today/tomorrow
    r"next\s+\d+\s+days?|"
    r"\d+\s*-?\s*day\s+(weather|forecast)|"
    r"weather\s+(this\s+week|next\s+week|for\s+the\s+week|forecast\s+for)|"
    r"(week|fortnight|monthly)\s+(weather|forecast)|"
    r"forecast\s+for\s+next|"
    # Plain today/tomorrow weather — verified live: the small local router
    # sometimes says "Let me check the weather..." as CONVERSATION instead of
    # actually calling weather_report, which either gets filtered to nothing
    # or (worse) lets the model guess at weather from stale training data.
    # Force the real fetch every time, same as prices and news already are.
    r"\bweather\b.{0,20}\b(today|tomorrow|now|right\s+now|outside)\b|"
    r"\b(today|tomorrow).{0,20}\bweather\b|"
    r"what.?s\s+(the\s+)?weather\s+(like\s+)?in\b|"
    r"how.?s\s+the\s+weather\b"
    r")",
    re.IGNORECASE,
)

# City extraction for the weather-bypass path above ("weather in Delhi today")
_WEATHER_CITY_RE = re.compile(
    r"\bweather\s+(?:like\s+)?in\s+(?P<city>[a-zA-Z][a-zA-Z .'-]{1,30}?)"
    r"(?:\s+(?:today|tomorrow|now|right\s+now)\b|[?.!,]|$)",
    re.IGNORECASE,
)

# ── open-app bypass: router LLM hallucinates "I opened it" without dispatching ──
# Only match known installable app names — never catches "open the weather" etc.
_OPEN_APP_KNOWN = (
    r"safari|chrome|google\s+chrome|firefox|edge|brave|opera|"
    r"spotify|slack|discord|whatsapp|zoom|telegram|"
    r"vs\s+code|v\.?\s?s\.?\s+code|visual\s+studio(?:\s+code)?|vscode|code|"
    r"terminal|finder|calculator|preview|photos|calendar|mail|messages|"
    r"notes|maps|music|podcasts|facetime|imovie|garageband|xcode|steam|"
    r"activity\s+monitor|system\s+settings|system\s+preferences|settings|"
    r"textedit|word|excel|powerpoint|vlc|notion|obsidian|figma|capcut|"
    r"browser"
)
_OPEN_APP_RE = re.compile(
    rf"\b(open|launch|start)\s+(?:the\s+)?(?P<app>{_OPEN_APP_KNOWN})\b",
    re.IGNORECASE,
)

# ── new-tab bypass: "open/make a new tab (in Chrome)" ─────────────────────────
_NEW_TAB_RE = re.compile(
    r"\b(open|make|create)\s+(a\s+)?new\s+tab\b",
    re.IGNORECASE,
)

# ── browser search: "search for X" fires a REAL search (Google tab + spoken
#    answer) — no "in the browser" suffix needed. Covers "I just want you to…".
_BROWSER_SEARCH_RE = re.compile(
    r"^(?:okay,?\s+|now\s+|just\s+|please\s+|hey\s+|can\s+you\s+|could\s+you\s+|"
    r"will\s+you\s+|go\s+ahead\s+and\s+|i\s+(?:just\s+)?(?:want|need)\s+you\s+to\s+)*"
    r"(?P<verb>search|google|look)\s+(?:for\s+|up\s+)?"
    r"(?P<q>.+?)"
    r"(?P<intab>\s+(?:(?:in|on|inside)\s+(?:the\s+|a\s+|my\s+|this\s+|that\s+)?"
    r"(?:browser|(?:new\s+)?tab|chrome|safari|google|internet|web)|online))?"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# ── net search anywhere in the sentence: "just search his name on the internet
#    and you will find…" — unanchored, so trailing chatter doesn't break it
_NET_SEARCH_RE = re.compile(
    r"\b(?:search|google|look\s+up|find)\b\s*(?:for\s+|about\s+)?"
    r"(?P<q>.{2,60}?)\s+"
    r"(?:(?:in|on|inside)\s+(?:the\s+|my\s+|this\s+|that\s+)?"
    r"(?:internet|web|google|browser|chrome|safari|(?:new\s+)?tab)|online)\b",
    re.IGNORECASE,
)

# Pronoun-ish queries need resolving from recent conversation
_PRONOUN_Q_RE = re.compile(
    r"^(him|her|it|them|that|this|the\s+same|his\s+name|her\s+name|"
    r"about\s+him|about\s+her|more\s+details\s+about\s+him)$",
    re.IGNORECASE,
)

# Filler that makes terrible search queries ("Can you search the current price…?")
_QUERY_LEAD_RE = re.compile(
    r"^(?:hey\s+|okay\s+|curie[,\s]+|please\s+|can\s+you\s+|could\s+you\s+|"
    r"will\s+you\s+|i\s+want\s+you\s+to\s+|just\s+)*"
    r"(?:search|google|look\s+up|find(?:\s+out)?|tell\s+me|check)?\s*"
    r"(?:for\s+|about\s+)?",
    re.IGNORECASE,
)
_QUERY_TAIL_RE = re.compile(
    r"\s*(?:(?:in|on|inside)\s+(?:the\s+|my\s+|this\s+|that\s+)?"
    r"(?:internet|web|google|browser|chrome|safari|(?:new\s+)?tab)|"
    r"online|for\s+me|please)?\s*[.!?,]*\s*$",
    re.IGNORECASE,
)


def _clean_search_query(text: str) -> str:
    """'Can you search the current price of iPhone 17 on the internet?'
    → 'the current price of iPhone 17'. Falls back to the original text."""
    q = _QUERY_LEAD_RE.sub("", text.strip(), count=1)
    q = _QUERY_TAIL_RE.sub("", q, count=1).strip()
    # drop trailing conversational clauses ("…that you have been talking about")
    q = re.sub(r"\s+(?:that|which)\s+(?:you|we|i)\b.*$", "", q, flags=re.IGNORECASE).strip()
    return q if len(q) >= 3 else text.strip()

# ── Gmail read bypass — "check my email / read me the top 5 emails" ──────────
# Verb and 'email(s)' may be separated by filler ("read ME THE TOP 5 emails").
_GMAIL_READ_RE = re.compile(
    r"\b(check|read|show|get|open|tell|see|what.{0,5}s\s+in)\b.{0,30}?\b(emails?|inbox|mails?|messages?)\b|"
    r"\b(any\s+)?(new|unread)\s+(email|mail|messages?)\b|"
    r"\b(my\s+)?inbox\b|"
    r"\b(my\s+|the\s+)?(latest|recent|new|top)\s+(\d+\s+)?(email|mail)s?\b",
    re.IGNORECASE,
)

# ── Gmail sender search — "email from Rahul", "tell me the email from Nitesh" ─
# Fires on its own (needs a request verb) — no longer requires _GMAIL_READ_RE too.
_GMAIL_FROM_RE = re.compile(
    r"\b(?:check|read|tell|show|get|open|find|search|see|any|is\s+there|what)\b.{0,40}?"
    r"\b(?:email|mail|message)s?\b.{0,30}?\bfrom\s+"
    r"(?P<sender>[a-zA-Z][a-zA-Z0-9 .@_-]{1,40}?)(?:\s*[?.!,]|$)",
    re.IGNORECASE,
)

# ── Gmail contacts — "who have I been emailing", "my email contacts" ─────────
_GMAIL_CONTACTS_RE = re.compile(
    r"who\s+(have\s+i|am\s+i|was\s+i)\s+.{0,15}(email|mail|touch|contact)|"
    r"(my\s+)?(email|mail)\s+contacts|"
    r"who\s+do\s+i\s+(email|mail|talk\s+to\s+on\s+email)",
    re.IGNORECASE,
)

# ── bare stop/no — user cutting Curie off mid-answer (NOT a shutdown) ─────────
_STOP_WORD_RE = re.compile(
    r"^\s*(stop|no|nope|okay\s+stop|ok\s+stop|stop\s+it|stop\s+talking|"
    r"that.?s\s+enough|enough|shut\s+up|be\s+quiet|quiet|silence|never\s*mind)"
    r"\s*[.!,]*\s*$",
    re.IGNORECASE,
)

# ── farewell — short goodbye back, keep listening (no shutdown, no rambling) ──
_FAREWELL_RE = re.compile(
    r"\b(bye+|bye.?bye|good\s*bye|good\s*night|talk\s+to\s+you\s+later|"
    r"see\s+you(\s+later)?|see\s+ya|catch\s+you\s+later|ttyl|later\s*!)\b",
    re.IGNORECASE,
)

# ── time/date — answer from the laptop clock directly, never from the LLM ─────
_TIME_DATE_RE = re.compile(
    r"what\s+time\s+is\s+it|what.?s\s+the\s+time|current\s+time|time\s+right\s+now|"
    r"what\s+(day|date)\s+is\s+(it|today)|what.?s\s+(the\s+date|today.?s\s+date)|"
    r"today.?s\s+date|which\s+day\s+is\s+(it|today)",
    re.IGNORECASE,
)

# ── website bypass — "open youtube.com", "go to github.com", "play youtube" ──
_WEBSITE_RE = re.compile(
    r"\b(?:open|go\s+to|visit|take\s+me\s+to)\s+(?:the\s+)?"
    r"(?P<domain>[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|in|co|ai|dev))\b",
    re.IGNORECASE,
)
_SITE_KEYWORDS = {   # "play some youtube songs" → youtube.com even without ".com"
    "youtube":   "https://www.youtube.com",
    "gmail":     "https://mail.google.com",
    "instagram": "https://www.instagram.com",
    "twitter":   "https://twitter.com",
    "netflix":   "https://www.netflix.com",
    "sitdeck":   "https://app.sitdeck.com",
}

# ── DEV MODE — "create a website by the name casamotori.sales" ───────────────
# Routed to a strong AI model (Claude/ChatGPT/Gemini) via actions/dev_builder.
_DEV_RE = re.compile(
    r"\b(?:create|creating|build|building|make|making|develop|developing|code)\s+"
    r"(?:me\s+)?(?:a\s+|an\s+)?"
    r"(?:new\s+)?(?:simple\s+|small\s+|full\s+)?"
    r"(?P<what>website|web\s?site|webpage|web\s+app|landing\s+page|portfolio|app|application|game|script|tool)\b",
    re.IGNORECASE,
)
# Dots INSIDE a name survive ("Casamotori.sales"); sentence-ending dots don't.
# Matches "name is X", "spelling is X" and STT's spoken "dot".
_DEV_NAME_KEY_RE = re.compile(
    r"\b(?:by\s+the\s+name(?:\s+of)?|called|named|name\s+is|spell\w*\s+is)\s+",
    re.IGNORECASE,
)
_DEV_NAME_RE = re.compile(
    r"[\"']?(?P<name>[A-Za-z0-9][\w.\- ]{1,60}?)[\"']?"
    r"(?=[?!,]|\.(?:\s|$)|\s+(?:with|that|which|and|what|who|he|she|it|so|for|from"
    r"|based|located|then|please)\b|\s*$)",
    re.IGNORECASE,
)


def _dev_name_from(text: str) -> str | None:
    """Extract a project name from natural speech.

    Handles: spoken "dot" → "." ; spelled-out letters "C-A-S-A-M-O-T-O-R-I" →
    "CASAMOTORI" ; several name mentions → prefer the one with a dot (the
    domain), else the last (a spelling usually comes after the rough name).
    """
    normalized = re.sub(r"\b((?:[A-Za-z][\-. ]){2,}[A-Za-z])\b",
                        lambda m: re.sub(r"[\-. ]", "", m.group(1)), text)
    normalized = re.sub(r"\s+dot\s+", ".", normalized, flags=re.IGNORECASE)
    # Anchor a name match right after EVERY keyword occurrence — a later
    # "spelling is …" must not be swallowed by an earlier "by the name …"
    matches = []
    for key_m in _DEV_NAME_KEY_RE.finditer(normalized):
        m = _DEV_NAME_RE.match(normalized, key_m.end())
        if m:
            matches.append(m.group("name").strip())
    if not matches:
        return None
    dotted = [n for n in matches if "." in n]
    return (dotted or matches)[-1]

# ── file creation — "create a file by the name Ashish.txt" ───────────────────
_FILE_RE = re.compile(
    r"\b(?:create|make|creating|making)\s+(?:a\s+|the\s+|this\s+)?(?:new\s+)?"
    r"(?:text\s+)?file\b",
    re.IGNORECASE,
)
# File names keep their extension ("Ashish.txt") — the folder-name regex stops at dots
_FILE_NAME_RE = re.compile(
    r"\b(?:named|called|name\s+it|by\s+the\s+name(?:\s+of)?|with\s+the\s+name)\s+"
    r"(?P<name>[\w\-]+(?:\.[A-Za-z0-9]{1,6})?)",
    re.IGNORECASE,
)

# ── in-page navigation — "play the third video", "click the second result" ───
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
             "last": -1, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
_NTH_ITEM_RE = re.compile(
    r"\b(?:play|click|open|select)\s+(?:on\s+)?(?:the\s+)?"
    r"(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"1st|2nd|3rd|4th|5th|\d+(?:st|nd|rd|th)?)\s+"
    r"(?P<kind>video|result|link|song|item)\b",
    re.IGNORECASE,
)
_SCROLL_RE = re.compile(r"\bscroll\s+(?P<dir>down|up)\b", re.IGNORECASE)

# ── VS Code quick-open — "navigate to app.js in vs code", any file, any project
# Was missing: quotes around a spoken/typed filename ('launch.sh'), and "MY vs
# code" / "MY editor" (only "the" was accepted) — either gap alone made this
# fail to match, silently falling through to the router, which then guessed
# wrong (once literally offering to CREATE the file instead of opening it).
_VSCODE_NAV_RE = re.compile(
    r"\b(?:navigate\s+to|go\s+to|find|search\s+for)\s+(?:the\s+|my\s+)?"
    r"[\"'‘’]?(?P<file>[\w\-]+(?:\.[A-Za-z0-9]{1,6})?)[\"'‘’]?\s+"
    r"(?:file\s+)?in\s+(?:the\s+|my\s+)?(?:vs\s?code|visual\s+studio(?:\s+code)?|code|editor)\b",
    re.IGNORECASE,
)

# ── menu navigation — "click new private window in safari", "select export" ──
# Clicks a menu-bar item of the named (or frontmost) app via Accessibility.
_MENU_CLICK_RE = re.compile(
    r"\b(?:click|select|choose|press)\s+(?:on\s+)?(?:the\s+)?"
    r"(?P<item>[a-z0-9][a-z0-9 '&\-]{2,45}?)"
    r"(?:\s+(?:option|button|menu\s+item|item|menu))?"
    r"(?:\s+(?:in|from|of)\s+(?:the\s+)?(?P<app>[a-z][a-z ]{1,25}?))?"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)
# words that mean this is NOT a menu click (handled elsewhere or dangerous)
_MENU_CLICK_BLOCK = re.compile(
    r"\b(video|result|link|song|tab|email|mail|folder|file|website|url|"
    r"chrome|browser|youtube|google)\b",
    re.IGNORECASE,
)

# ── tab navigation — "next tab", "switch to tab 2" (frontmost app) ────────────
_TAB_NAV_RE = re.compile(
    r"\b(?:go\s+to\s+|switch\s+to\s+|move\s+to\s+)?(?P<dir>next|previous|prev|last)\s+tab\b|"
    r"\b(?:go\s+to\s+|switch\s+to\s+|open\s+)tab\s+(?:number\s+)?(?P<n>\d)\b",
    re.IGNORECASE,
)

# ── open a project file (VS Code) — "open index.html", "open the style.css file"
_OPEN_FILE_RE = re.compile(
    r"\b(?:open|show\s+me|go\s+to)\s+(?:the\s+)?"
    r"(?P<file>[\w\-]+\.(?:html|css|js|ts|py|json|txt|md|jsx|tsx|svg))"
    r"(?:\s+file)?\b",
    re.IGNORECASE,
)

# ── folder creation — "create a new folder called X on my desktop" ───────────
_FOLDER_RE = re.compile(
    r"\b(?:create|make|creating|making)\s+(?:a\s+|the\s+|this\s+|another\s+)?(?:new\s+)?folder\b",
    re.IGNORECASE,
)
# "called X", "named X", "by the name (of) X", "with the name X", "name it X"
_FOLDER_NAME_RE = re.compile(
    r"\b(?:named|called|name\s+it|by\s+the\s+name(?:\s+of)?|with\s+the\s+name)\s+"
    r"(?P<name>[\w'’ .-]{1,40}?)(?:\s+(?:on|in|inside)\b|[.!?,]|$)",
    re.IGNORECASE,
)
_FOLDER_LOC_RE = re.compile(
    r"\b(?:on|in)\s+(?:the\s+|my\s+)?(?P<loc>desktop|documents|downloads|pictures|music|videos|home)\b",
    re.IGNORECASE,
)
# "inside the (new) folder (we just created)" → nest under the last created folder
_FOLDER_PARENT_RE = re.compile(
    r"\b(?:inside|into|within)\s+(?:the\s+|that\s+|this\s+)?(?:new\s+|same\s+|last\s+)?folder\b",
    re.IGNORECASE,
)
# "in the Ashish folder" → find that folder by name on Desktop/Documents/Downloads
_NAMED_PARENT_RE = re.compile(
    r"\b(?:in|inside|into|under)\s+(?:the\s+|my\s+)?(?P<pname>[\w'’ -]{1,30}?)\s+folder\b",
    re.IGNORECASE,
)


def _find_named_folder(name: str):
    """Locate an existing folder by (case-insensitive) name in common places."""
    want = name.strip().lower()
    if want in ("new", "same", "last", "that", "this", "a"):
        return None
    for base in (Path.home() / "Desktop", Path.home() / "Documents",
                 Path.home() / "Downloads", Path.home()):
        try:
            for child in base.iterdir():
                if child.is_dir() and child.name.lower() == want:
                    return child
        except Exception:
            continue
    return None
# "rename the folder (to / by the name) X", "rename it X"
_RENAME_RE = re.compile(
    r"\brename\s+(?:the\s+|that\s+|this\s+)?(?:folder|it)\b",
    re.IGNORECASE,
)
_RENAME_TO_RE = re.compile(
    r"\b(?:to|as)\s+(?P<name>[\w'’ .-]{1,40}?)(?:[.!?,]|$)",
    re.IGNORECASE,
)

# ── read screen — "read this page", "what's on my screen", "read the tab" ────
_READ_SCREEN_RE = re.compile(
    r"\b(?:read|summari[sz]e)\s+(?:me\s+)?(?:out\s+)?(?:the\s+|this\s+|that\s+|my\s+|what.?s\s+on\s+)?"
    r"(?:screen|page|tab|website|browser)\b|"
    r"\bwhat.?s\s+(?:on|in)\s+(?:the\s+|my\s+|this\s+)?(?:screen|page|tab|browser)\b",
    re.IGNORECASE,
)

# ── music — "play some music", "play a song" (YouTube/Spotify handled elsewhere)
_MUSIC_RE = re.compile(
    r"\bplay\s+(?:me\s+)?(?:some\s+)?(?:music|songs?|a\s+song)\b",
    re.IGNORECASE,
)

# ── macOS settings-pane bypass — "go to display", "open battery settings" ────
_SETTINGS_PANES = (
    r"display|displays|battery|wifi|wi-fi|bluetooth|sound|volume|network|privacy|"
    r"security|notifications?|accessibility|storage|keyboard|trackpad|mouse|"
    r"wallpaper|appearance|general|screen\s+time|software\s+update|lock\s+screen|"
    r"touch\s+id|focus|users|date\s+and\s+time"
)
_SETTINGS_PANE_RE = re.compile(
    rf"\b(?:go\s+to|open|navigate\s+to|take\s+me\s+to|show\s+me)\s+(?:the\s+)?"
    rf"(?P<pane>{_SETTINGS_PANES})(?:\s+(?:settings?|section|pane|panel|options?|tab))?\b",
    re.IGNORECASE,
)

# ── close-app bypass: catch "close browser/app" before router confuses with shutdown ──
# Handles explicit: "close the Chrome browser", "close Chrome", "close the browser"
# Handles contextual: "close it", "can you close it", "close that"
# Close works for EVERY app Curie can open (same known-app list), plus
# contextual forms ("close this application" → last app she opened).
_CLOSE_APP_RE = re.compile(
    rf"(re.?close|close|quit|kill|force.quit|force-quit)\s+(the\s+|this\s+|that\s+|my\s+)?(browser|app(?:lication)?|window|tab)\b|"
    rf"(close|quit|kill|force.quit|force-quit)\s+(the\s+|my\s+)?"
    rf"({_OPEN_APP_KNOWN})"
    rf"(\s+(browser|app(?:lication)?|window|tab))?\b|"
    rf"(browser|chrome|safari|firefox|spotify|slack)\s+(close|quit)|"
    rf"can\s+you\s+(close|quit)\s+(it|that|this|the\s+browser|the\s+app)\b|"
    rf"(close|quit)\s+(it|that|this)\s*(for\s+me)?\s*$",
    re.IGNORECASE,
)

# ── news-shaped queries get a clean canonical query ───────────────────────────
# Raw STT text ("Thank you Ray, give me the daily briefing.") makes a terrible
# search query AND can miss Sitdeck's topic keywords. Canonicalising guarantees
# the Sitdeck topic match and keeps junk words out of any search.
_NEWSY_RE = re.compile(
    r"\bbriefing\b|brief\s+me\b|\bnews\b|headlines?|top\s+stories|"
    r"happening\s+(around|in)\s+the\s+world|current\s+events|what\s+happened\s+today",
    re.IGNORECASE,
)
_MD_HEADER    = re.compile(r"^#+\s*", re.MULTILINE)
_MD_BOLD_IT   = re.compile(r"\*{1,3}([^*\n]+?)\*{1,3}")
_MD_BULLET    = re.compile(r"^\s*[-•*]\s+", re.MULTILINE)
_MD_NUMBERED  = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_MD_LINK      = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CODE      = re.compile(r"`[^`]*`")
_MD_SPECIAL   = re.compile(r"[`~|#]")
_MULTI_SPACE  = re.compile(r"\s{2,}")
_MULTI_NL     = re.compile(r"\n+")


def _strip_md(text: str) -> str:
    """Remove markdown so TTS doesn't read '#' '**' bullets etc. aloud."""
    text = _MD_HEADER.sub("", text)
    text = _MD_BOLD_IT.sub(r"\1", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_NUMBERED.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_CODE.sub("", text)
    text = _MD_SPECIAL.sub("", text)
    text = _MULTI_NL.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


# ── sentence-level reply cleaner — drops any sentence with AI-bot patterns ──
_BAD_SENTENCE_RE = re.compile(
    r"("
    # Fake action openers
    r"let\s+me\s+(check|look|find|search|fetch|get|grab|pull|close|open|do|try)\b|"
    r"let.{0,4}s\s+.{0,12}(fetch|look|find|search|get|check|try|close|open)\b|"
    r"i.{0,4}ll\s+(look|search|find|get|fetch|check|close|open|do)\s+(that|it|up|the|for)\b|"
    # Sycophantic openers
    r"^(of course|certainly|absolutely|sure thing|great|perfect|awesome|wonderful)[!,. ]|"
    r"^(sure|got it|okay so|alright so|oh,?\s+i\s+see)[!,. ]|"
    # Fake empathy
    r"that\s+(sounds|seems)\s+(really|very|quite)\s+\w+|"
    r"that\s+must\s+(really|very|be|have\s+been)\s+\w+|"
    r"i\s+(understand|can\s+see|can\s+imagine)\s+(how|that|what|the)\b|"
    r"i\s+can\s+understand\s+(how|why|what)\b|"
    # Clarification requests (ALL banned)
    r"what\s+(topics?|areas?|kinds?|types?|specific)\s+(are|do|would|is)\s+you\b|"
    r"what\s+(specific|particular)\s+\w+.{0,40}\?|"
    r"what\s+(kind|type|level)\s+of.+\?|"
    r"looking\s+for\s+(a\s+)?(specific|general|particular)\b|"
    r"(general|specific)\s+(overview|information|detail)\b|"
    r"do\s+you\s+(want|need)\s+(a\s+)?(general|specific|more|particular)\b.+\?|"
    r"what\s+would\s+you\s+like\s+(to\s+know|me\s+to|more|next)\b|"
    r"what\s+else\s+would\s+you\s+(like|want|need)\b|"
    r"anything\s+(specific|particular|else)\s+(you.{0,10}like|you\s+want|i\s+can)\b|"
    r"interested\s+in\s+(more\s+)?specifics?\b|"
    # Service-bot closers
    r"(is|are)\s+there\s+anything\s+(else|more|specific|particular|i\s+can)\b|"
    r"(can|may)\s+i\s+(help|assist|support)\s+(you|with)\b|"
    r"how\s+(can|may|would|else\s+can)\s+i\s+(help|assist|support)\b|"
    r"i.{0,3}m\s+here\s+to\s+(help|assist)\b|"
    r"feel\s+free\s+to\s+(ask|let\s+me\s+know|reach\s+out)\b|"
    r"how\s+would\s+you\s+like\s+to\s+proceed\b|"
    r"would\s+you\s+like\s+(more|me\s+to|to\s+know|some\s+more)\b|"
    r"let\s+me\s+know\s+if\b|"
    r"don.t\s+hesitate\s+to\b|"
    r"do\s+you\s+need\s+(any\s+)?(help|assistance|more\s+info)\b|"
    r"tell\s+me\s+(how|if)\s+you.{0,20}(like|want|need|prefer)\b|"
    # Fake social openers / closers
    r"(how\s+does|how.{0,3}s)\s+that\s+(sound|work|seem)\b|"
    r"how\s+about\s+(i|we)\s+(look|fetch|find|check|search|pull|grab)\b|"
    r"how.{0,5}s\s+your\s+(day|week|night|morning|evening|going)\b|"
    r"sounds\s+(good|great|interesting|perfect|awesome|nice)\s*[,!.]|"
    # Topic hijacks — offering to check weather/news/etc. out of nowhere
    r"is\s+it\s+ok(ay)?\s+if\s+i\b|"
    r"(should|shall|can|may)\s+i\s+check\b|"
    r"(do\s+you\s+)?want\s+me\s+to\s+(check|look|search|fetch)\b|"
    r"we\s+need\s+to\s+ensure\s+safety\b|"
    # Fake progress — the LLM pretending an action is happening
    r"checking\s+your\s+inbox|"
    r"just\s+a\s+moment|one\s+moment|here\s+we\s+go|"
    r"i.{0,4}ll\s+open\b|"
    r"i.{0,4}ll\s+navigate\b|"
    r"i.{0,4}ll\s+(?:find|search|look)\b|"
    r"let.{0,4}s\s+(dig|dive)\s+into\b|"
    r"let.{0,4}s\s+create\b|"
    r"from\s+what\s+i\s+remember\b|"
    # Clarification loops ("Do you have any specific location in mind?")
    r"do\s+you\s+have\s+any\s+specific\b|"
    r"(location|place|directory)\s+in\s+mind|"
    r"do\s+you\s+want\s+it\s+(on|in)\b"
    r")",
    re.IGNORECASE,
)


def _clean_reply(text: str) -> str:
    """Drop any sentence containing an AI-bot cliché; strip markdown."""
    text = _strip_md(text)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    good = [s.strip() for s in sentences if s.strip() and not _BAD_SENTENCE_RE.search(s)]
    return " ".join(good).strip()
from engine.vad    import VAD
from engine.stt    import WhisperSTT
from engine.llm    import LLM
from engine.tts    import KokoroTTS
from engine.router import Router
from engine.memory.palace import Memory

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# ── structured long-term memory helpers ───────────────────────
from memory.memory_manager import (
    load_memory   as _load_ltm,
    update_memory as _update_ltm,
    format_memory_for_prompt as _fmt_ltm,
)

_MINE_PROMPT = """\
From the user's message, extract ONE durable personal fact worth storing long-term.

STRICT RULES:
- ONLY extract facts the user EXPLICITLY stated ABOUT THEMSELVES. Do NOT infer, guess, or fill in missing details.
- Facts about OTHER PEOPLE (someone they follow, admire, mention) → category relationships with the person's name in the key, or NONE. NEVER store another person's details under identity.
- The user's name NEVER changes. If the message mentions someone's name, that is NOT the user's name → do not store identity/name.
- If the user asked a QUESTION or gave a COMMAND ("search X", "open Y", "can you tell me X?") → reply NONE.
- If the user said something vague or unclear → reply NONE.
- Never hallucinate names, numbers, or relationships that were not mentioned.

Reply in EXACTLY this format (three lines):
category: <one of: identity, preferences, projects, relationships, wishes, notes>
key: <short_snake_case_key>
value: <concise fact, ONLY from what the user said>

Or reply just: NONE

Categories:
- identity:       name, age, city, job, nationality, school, language
- preferences:    likes/dislikes (food, tools, habits, hobbies, sports)
- projects:       active projects or goals they are working on
- relationships:  people in their life (family, friends, colleagues)
- wishes:         things they want to do, buy, or achieve
- notes:          anything else important that does not fit above

Message: {text}"""


_SAD_WORDS = {
    "sucks", "awful", "terrible", "horrible", "sorry", "lost", "failed", "sad",
    "depressed", "hurt", "pain", "died", "death", "miss", "breakup", "fired",
    "rejected", "scared", "worried", "crying", "alone", "hopeless", "heartbreak",
    "disappointed", "devastated", "miserable", "suffering", "grief", "tragedy",
}
_EXCITED_WORDS = {
    "amazing", "awesome", "fantastic", "incredible", "won", "landed", "nailed",
    "love", "celebrate", "excited", "thrilled", "brilliant", "genius", "perfect",
    "finally", "promotion", "offer", "yes", "great news", "incredible", "best",
    "happy", "joy", "glad", "proud", "achieved", "success", "deal",
}


def _emotion_profile(text: str) -> tuple[float, float]:
    """(speed, pitch) shift based on detected emotion.

    Sad → slower AND lower-pitched (warm, heavier). Excited → faster AND
    brighter. Neutral → natural. Pitch shifts stay small (±4%) so the voice
    still sounds like Curie, just in a different mood.
    """
    words = set(re.sub(r"[^a-z\s]", "", text.lower()).split())
    sad  = len(words & _SAD_WORDS)
    buzz = len(words & _EXCITED_WORDS)
    if sad > buzz and sad > 0:
        return settings.TTS_SPEED * 0.92, 0.96   # slower + lower for sad moments
    if buzz > sad and buzz > 0:
        return settings.TTS_SPEED * 1.06, 1.04   # faster + brighter for excitement
    return settings.TTS_SPEED, 1.0


class Orchestrator:
    def __init__(self, ui=None):
        import collections
        self.ui    = ui
        self._stop = threading.Event()
        self._recent: collections.deque = collections.deque(maxlen=8)  # last 4 exchanges
        self._last_browser: str | None = None   # last browser Ashish opened → URLs go there
        self._last_folder = None                 # Path of last folder Curie created (rename/nesting)
        self._last_project = None                # Path of last dev-mode project (file navigation)
        self._last_opened_app: str | None = None # last app Curie launched ("close this app")
        self._await_browser_search = False      # True right after "open a new tab"
        try:
            entry = _load_ltm().get("identity", {}).get("name", {})
            self._user_name = (entry.get("value") if isinstance(entry, dict) else entry) or "Ashish"
        except Exception:
            self._user_name = "Ashish"
        self._log("Loading local models...")
        self.vad    = VAD()
        self.stt    = WhisperSTT()
        self.llm    = LLM()
        self.tts    = KokoroTTS()
        self.router = Router()
        self.memory = Memory()          # ChromaDB semantic recall

        # Wrap tts.speak so the face's SPEAKING animation is driven by the
        # instant real audio actually starts, not by whatever point in the
        # turn we happened to guess "she's about to talk". Every existing
        # `self.tts.speak(...)` call site in this file benefits automatically.
        self._real_tts_speak = self.tts.speak
        self.tts.speak = self._tts_speak_synced

        self._log("Curie online.")

    def _tts_speak_synced(self, text: str, *args, **kwargs):
        # Stay in whatever state we're already in (usually THINKING, i.e. still
        # synthesizing) and only flip to SPEAKING via the on_play_start callback,
        # which KokoroTTS.speak() fires the instant sd.play() actually starts.
        # Flipping here at call-time was the bug: Kokoro synthesis (per-sentence,
        # real CPU work) happens AFTER this line and BEFORE any sound plays, so
        # the face showed "speaking" for however long synthesis took, in silence.
        kwargs["on_play_start"] = lambda: self._state("SPEAKING")
        return self._real_tts_speak(text, *args, **kwargs)

    # ── UI helpers ────────────────────────────────────────────
    def _state(self, s: str):
        if self.ui:
            try: self.ui.set_state(s)
            except Exception: pass

    def _log(self, text: str):
        print(f"[Curie] {text}")
        if self.ui:
            try: self.ui.write_log(text)
            except Exception: pass

    # ── barge-in: inter-sentence gap check ───────────────────
    def _check_barge_gap(self) -> bool:
        """Sample mic for ~100ms after each sentence (speaker OFF = zero bleed)."""
        import sounddevice as sd
        # Use device native rate (cached — repeated CoreAudio queries can crash)
        if not hasattr(self, "_input_rate"):
            self._input_rate = int(sd.query_devices(kind="input")["default_samplerate"])
        native_rate = self._input_rate
        frame  = native_rate * 20 // 1000
        consec = 0
        try:
            with sd.RawInputStream(
                samplerate=native_rate, channels=1,
                dtype="int16", blocksize=frame,
            ) as stream:
                for _ in range(settings.BARGE_IN_GAP_FRAMES):
                    buf, _ = stream.read(frame)
                    amp = np.abs(np.frombuffer(bytes(buf), dtype=np.int16)).max()
                    if amp > settings.BARGE_IN_THRESHOLD:
                        consec += 1
                        if consec >= settings.BARGE_IN_FRAMES_REQ:
                            self._log("[barge-in] interrupted")
                            return True
                    else:
                        consec = 0
        except Exception:
            pass
        return False

    # ── speech helpers ────────────────────────────────────────
    def _speak_sentences(self, sentences, barge_in: bool = True, speed: float | None = None) -> bool:
        """Speak all sentences as a single TTS call — no mid-response gaps."""
        combined = " ".join(s.strip() for s in sentences if (s or "").strip())
        if not combined:
            return False
        spd, pitch = _emotion_profile(combined)
        interrupted = self.tts.speak(combined, speed=speed or spd, pitch=pitch)
        if interrupted:
            self._log("[stop] you interrupted — I'll stop there")
        elif barge_in:
            self._check_barge_gap()
        return True

    def _speak_stream(self, chunks, barge_in: bool = True, clean: bool = True, speed: float | None = None) -> bool:
        """Accumulate ALL streamed tokens, then speak as one TTS call.

        Collecting everything first eliminates per-sentence pauses. The user
        can cut playback at any point with a firm "stop" (mic is monitored
        during speech — see KokoroTTS._wait_or_interrupt).
        """
        buf = ""
        for tok in chunks:
            buf += tok
        text = buf.strip()
        if not text:
            return False
        # Strip any stray <think> blocks that leaked through
        if "</think>" in text:
            text = text.split("</think>")[-1].strip()
        if clean:
            text = _clean_reply(text)
        if not text:
            return False
        spd, pitch = _emotion_profile(text)
        interrupted = self.tts.speak(text, speed=speed or spd, pitch=pitch)
        if interrupted:
            self._log("[stop] you interrupted — I'll stop there")
        elif barge_in:
            self._check_barge_gap()
        return True

    _NAME_SEQ_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,2})\b")
    _NAME_STOP   = {"Curie", "Chrome", "Safari", "Google", "Gmail", "Sitdeck",
                    "Ashish", "The", "Can", "What", "Just", "His", "Her"}

    def _recent_name(self) -> str | None:
        """Last person name mentioned in recent user turns (for 'search HIM')."""
        for line in reversed(self._recent):
            if not line.startswith(f"{self._user_name}:"):
                continue
            for m in reversed(self._NAME_SEQ_RE.findall(line)):
                words = m.split()
                if not any(w in self._NAME_STOP for w in words):
                    return m
        return None

    def _vscode_quick_open(self, fname: str) -> str:
        """Jump to a file in VS Code via Cmd+P — works in whatever project is open."""
        import subprocess as _sp
        safe = re.sub(r'["\\\\]', "", fname)
        script = f'''
        tell application "Visual Studio Code" to activate
        delay 0.4
        tell application "System Events"
            keystroke "p" using command down
            delay 0.3
            keystroke "{safe}"
            delay 0.5
            key code 36
        end tell'''
        try:
            r = _sp.run(["osascript", "-e", script], capture_output=True, timeout=15, text=True)
            ok, err = r.returncode == 0, (r.stderr or "").strip()
        except Exception as e:
            ok, err = False, str(e)
        if ok:
            return f"Opened {fname} in VS Code."
        if "not allowed" in err.lower() or "1002" in err:
            return ("I need keyboard permission for that — allow it in Privacy and "
                    "Security, Accessibility, then ask me again.")
        return f"I couldn't jump to {fname} in VS Code."

    def _speak_gmail_result(self, user_text: str, raw: str):
        """Speak a Gmail tool result — errors verbatim, real content summarised."""
        if not raw:
            self.tts.speak("I couldn't get anything back from Gmail.")
            return
        error_starts = ("could not", "gmail credentials", "gmail isn't",
                        "my gmail login", "your inbox is empty",
                        "no emails found", "i haven't tracked")
        if raw.lower().startswith(error_starts):
            self._log(f"Curie: {raw}")
            self.tts.speak(raw)
            return
        summary_ctx = (
            "Summarise these emails for a voice assistant. "
            "For each one say: who it's from and what it's about in one sentence. "
            "Use ONLY the emails below — never invent emails. "
            "Be casual and concise. No markdown, no bullet points, no questions at the end.\n\n"
            + raw
        )
        spoken: list[str] = []

        def _tap(gen):
            for tok in gen:
                spoken.append(tok)
                yield tok

        self._speak_stream(_tap(self.llm.stream_reply(user_text, summary_ctx)), clean=True)
        full = "".join(spoken).strip()
        if full:
            self._log(f"Curie: {full}")

    # ── context helpers ───────────────────────────────────────
    def _build_context(self, text: str) -> str:
        import datetime as _dt
        now = _dt.datetime.now()
        # Real laptop date/time — the LLM must never guess these
        parts = [now.strftime("Current date and time: %A, %B %-d, %Y, %-I:%M %p.")]
        # Recent conversation turns — most important for resolving "the same", "it", etc.
        if self._recent:
            parts.append("Recent conversation:\n" + "\n".join(self._recent))
        # Structured long-term memory (name, city, preferences, relationships…)
        ltm = _fmt_ltm(_load_ltm())
        if ltm:
            parts.append(ltm)
        # Semantic recall from ChromaDB (relevant past conversation turns)
        rc = self.memory.recall(text)
        if rc:
            parts.append(rc)
        return "\n".join(parts)

    _SHUTDOWN_SENTINEL = "__CURIE_SHUTDOWN__"
    _DIRECT_PREFIX     = "__CURIE_DIRECT__"   # speak result verbatim, skip LLM summary

    # ── browser helper — open URLs where Curie can also ACT on them ───────────
    def _open_url(self, url: str):
        """Prefer Curie's debug Chrome (CDP): tabs opened there can be clicked,
        read, and scrolled afterward ("open the first link"). Falls back to the
        last browser Ashish asked for, then the system default."""
        def _open():
            import platform, subprocess
            import requests as _rq
            try:   # 1) Curie's own Chrome — makes follow-up navigation possible
                _rq.put(f"http://127.0.0.1:9222/json/new?{url}", timeout=4)
                subprocess.run(["open", "-a", "Google Chrome"],
                               capture_output=True, timeout=5)   # bring to front
                return
            except Exception:
                pass
            if platform.system() == "Darwin" and self._last_browser:
                try:   # 2) the browser he last asked Curie to open
                    r = subprocess.run(["open", "-a", self._last_browser, url],
                                       capture_output=True, timeout=8)
                    if r.returncode == 0:
                        return
                except Exception:
                    pass
            webbrowser.open_new_tab(url)   # 3) system default
        threading.Thread(target=_open, daemon=True).start()
        self._log(f"[browser] {url}")

    _BROWSER_APPS = {"safari": "Safari", "chrome": "Google Chrome",
                     "google chrome": "Google Chrome", "firefox": "Firefox",
                     "edge": "Microsoft Edge", "brave": "Brave Browser",
                     "opera": "Opera", "browser": "Google Chrome"}

    # ── tool dispatch ─────────────────────────────────────────
    def _dispatch(self, tool_name: str, params: dict) -> tuple[str, str | None]:
        """Run a tool. Returns (result_text, optional_url)."""
        try:
            if tool_name == "shutdown":
                return self._SHUTDOWN_SENTINEL, None

            if tool_name == "open_app":
                from actions.open_app import open_app
                app = (params.get("app_name") or "").lower().strip()
                if app in self._BROWSER_APPS:
                    self._last_browser = self._BROWSER_APPS[app]
                return open_app(parameters=params), None

            if tool_name == "close_app":
                from actions.close_app import close_app
                return close_app(parameters=params), None

            if tool_name == "open_url":
                url = params.get("url", "").strip()
                if url and not url.startswith("http"):
                    url = "https://" + url
                if url:
                    self._open_url(url)
                from urllib.parse import urlparse
                domain = urlparse(url).netloc or url
                return f"Opening {domain}.", None

            if tool_name == "web_search":
                # 1. Check private (hardcoded) sources first — no random internet traffic
                from actions.private_search import search_private
                result = search_private(params.get("query", ""))
                if result is not None:
                    private_content, private_url = result
                    # Always open the source URL in browser when topic matched
                    if private_url:
                        self._open_url(private_url)
                    if private_content:
                        self._log("[PrivateSearch] answer found in private sources")
                        return private_content, None
                    # Topic matched but Sitdeck couldn't be read → NEVER swap in
                    # random internet sources. Say so honestly and stop.
                    self._log("[PrivateSearch] Sitdeck unreadable — NOT falling back to internet")
                    return (self._DIRECT_PREFIX +
                            "I couldn't read Sitdeck — it never finished loading, which "
                            "usually means my browser window needs a one-time login. "
                            "Ask me for the news again, and when my window pops up, "
                            "log into Sitdeck in it — I'll wait and read it from there. "
                            "I only read news from Sitdeck, so I won't make anything up."), None
                # 2. Query never matched a private source → internet DDG is allowed
                self._log("[WebSearch] not in private sources → using internet")
                from actions.web_search import web_search
                return web_search(parameters=params)      # (text, url)

            if tool_name == "weather_report":
                from actions.weather_report import weather_action
                return weather_action(parameters=params), None

            if tool_name == "gmail_read":
                from actions.gmail import gmail_read
                return gmail_read(parameters=params), None

            if tool_name == "gmail_send":
                from actions.gmail import gmail_send
                return gmail_send(parameters=params), None

            if tool_name == "gmail_search":
                from actions.gmail import gmail_search
                return gmail_search(parameters=params), None

            if tool_name == "gmail_contacts":
                from actions.gmail import gmail_contacts
                return gmail_contacts(parameters=params), None

            if tool_name == "file_control":
                from actions.file_controller import file_controller
                return file_controller(parameters=params), None

        except Exception as e:
            self._log(f"[dispatch error] {tool_name}: {e}")
            return "Sorry, the action failed.", None

        return f"No handler for: {tool_name}", None

    # ── main respond turn ─────────────────────────────────────
    def _respond(self, text: str):
        low = text.lower()

        # Hard shutdown — regex catches all STT variants, bypasses router
        if _SHUTDOWN_RE.search(low):
            self.tts.speak("Goodbye.")
            self._log("Curie: Goodbye.")
            self.stop()
            return

        # Bare "stop" / "no" — Ashish cutting Curie off. Acknowledge and drop it.
        if _STOP_WORD_RE.match(low):
            self._log("Curie: Okay.")
            self.tts.speak("Okay.")
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Farewell — one short goodbye back, keep listening. No rambling.
        if _FAREWELL_RE.search(low) and len(low.split()) <= 12:
            msg = random.choice([
                "Later! I'm here when you need me.",
                "Bye! Talk soon.",
                "See you. I'll be right here.",
                "Take care. Just call when you need me.",
            ])
            self._log(f"Curie: {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Time / date — answer straight from the laptop clock, never the LLM
        if _TIME_DATE_RE.search(low):
            import datetime as _dt
            now = _dt.datetime.now()
            msg = f"It's {now.strftime('%-I:%M %p')} on {now.strftime('%A, %B %-d, %Y')}."
            self._log(f"Curie: {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Close browser/app — must come BEFORE router (router confuses "close" with shutdown)
        close_m = _CLOSE_APP_RE.search(low)
        if close_m:
            from actions.close_app import close_app as _close_action
            raw = close_m.group(0)
            # Strip leading verb + optional article, then trailing generic noun
            app = re.sub(r"^(re.?close|close|quit|kill|force.?quit)\s+(the\s+|this\s+|that\s+|my\s+)?",
                         "", raw, flags=re.IGNORECASE).strip()
            app = re.sub(r"\s*(browser|app(?:lication)?|window|tab)$", "", app, flags=re.IGNORECASE).strip()
            app = re.sub(r"^(it|that|this)$", "", app, flags=re.IGNORECASE).strip()
            # "close this application" → the last app Curie opened
            if not app and self._last_opened_app:
                app = self._last_opened_app
            msg = _close_action({"app_name": app or "browser"})
            self._log(f"Curie: {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # A pending "search next in the tab" only survives one turn
        await_search, self._await_browser_search = self._await_browser_search, False

        # New-tab bypass — real AppleScript new tab in the browser he's using
        # (the old about:newtab trick errored with -10814 and opened nothing)
        if _NEW_TAB_RE.search(low):
            for alias, app in self._BROWSER_APPS.items():
                if alias != "browser" and alias in low:
                    self._last_browser = app
                    break
            browser = self._last_browser or "Google Chrome"
            from actions.open_app import _open_new_tab_macos
            import platform as _pf
            ok = _open_new_tab_macos(browser) if _pf.system() == "Darwin" else False
            if not ok:
                webbrowser.open_new_tab("https://www.google.com")
            self._await_browser_search = True
            self._log(f"[new-tab] opened in {browser}")
            self.tts.speak("Opened a new tab. What should I search?")
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Browser search — "search for X in the browser / on the internet",
        # or any "search X" right after he asked for a new tab
        q = None
        search_m = _BROWSER_SEARCH_RE.match(text.strip())
        if search_m and (search_m.group("intab") or await_search
                         or search_m.group("verb").lower() in ("search", "google")):
            q = search_m.group("q").strip()
        else:
            net_m = _NET_SEARCH_RE.search(text)
            if net_m:
                q = net_m.group("q").strip()
        # email searches belong to the Gmail handlers further down
        if q and re.search(r"\b(email|mail|inbox)\b", q, re.IGNORECASE):
            q = None
        if q:
            if _PRONOUN_Q_RE.match(q):
                resolved = self._recent_name()
                if resolved:
                    q = resolved
            q = _clean_search_query(q)
            from urllib.parse import quote_plus
            self._open_url(f"https://www.google.com/search?q={quote_plus(q)}")
            self._log(f"[browser-search] {q!r}")
            self.tts.speak(f"Searching for {q}. It's up in your browser.")
            # Also fetch a spoken answer so he doesn't have to read the screen
            raw, _u = self._dispatch("web_search", {"query": q})
            if raw and not raw.startswith(("Search failed", "No results", "Please provide",
                                           self._DIRECT_PREFIX)):
                summary_ctx = (
                    "Answer in 2-4 casual spoken sentences using ONLY the search results "
                    "below. If they don't answer the question, say you couldn't find it — "
                    "never invent. No markdown, no questions at the end.\n\n" + _strip_md(raw)
                )
                self._speak_stream(self.llm.stream_reply(q, summary_ctx), clean=True)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Gmail contacts — "who have I been emailing with?"
        if _GMAIL_CONTACTS_RE.search(low):
            self._log("[gmail bypass] contacts")
            raw, _ = self._dispatch("gmail_contacts", {})
            self._speak_gmail_result(low, raw)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Gmail sender search — "check for the latest email from Rahul"
        from_m = _GMAIL_FROM_RE.search(text)
        if from_m:
            sender = from_m.group("sender").strip()
            # Trim trailing filler ("from john about the deal" → "john")
            sender = re.split(r"\s+(?:about|regarding|saying|on|here|please|now|today)\b",
                              sender)[0].strip()
            sender = " ".join(sender.split()[:3])
            if sender:
                self._log(f"[gmail bypass] searching emails from '{sender}'")
                self.tts.speak(f"Looking for emails from {sender}.")
                if re.search(r"\b(open|pull|browser)\b", low):
                    self._open_url("https://mail.google.com")
                raw, _ = self._dispatch("gmail_search", {"query": f"from:{sender}", "count": 5})
                self._speak_gmail_result(low, raw)
                if not self._stop.is_set():
                    self._state("LISTENING")
                return

        # Gmail read bypass — "check my email / read my inbox / any new emails"
        if _GMAIL_READ_RE.search(low):
            # Extract count if user said "read my last 3 emails" etc.
            count_m = re.search(r"\b(\d+)\s+(email|mail|message)", low)
            count = int(count_m.group(1)) if count_m else 5
            self._log(f"[gmail bypass] reading {count} emails")
            self.tts.speak("Checking your inbox.")
            # "OPEN my email" means show it too — bring Gmail up in the browser
            if re.search(r"\b(open|pull|browser)\b", low):
                self._open_url("https://mail.google.com")
            raw, _ = self._dispatch("gmail_read", {"count": count})
            self._speak_gmail_result(low, raw)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Settings-pane bypass — "go to display", "open battery settings"
        pane_m = _SETTINGS_PANE_RE.search(low)
        if pane_m:
            remainder = low[pane_m.end():].strip(" .!?,")
            if not remainder or "setting" in low or "section" in low or "pane" in low:
                from actions.open_app import open_app as _open_action
                pane = pane_m.group("pane").strip()
                msg = _open_action({"app_name": f"{pane} settings"})
                self._last_opened_app = "settings"
                self._log(f"[settings bypass] {pane} → {msg}")
                self.tts.speak(msg)
                if not self._stop.is_set():
                    self._state("LISTENING")
                return

        # Read screen — "read this page / what's on my screen" (browser tab via CDP)
        if _READ_SCREEN_RE.search(low):
            self._log("[read-screen] reading active browser tab")
            from actions.read_screen import read_browser_page
            page_text, note = read_browser_page()
            if page_text is None:
                self._log(f"Curie: {note}")
                self.tts.speak(note)
            else:
                self.tts.speak(note)
                summary_ctx = (
                    "Read this web page aloud for your friend: give the important content "
                    "in casual spoken sentences, using ONLY the page text below. Cover "
                    "everything substantial; skip menus, buttons, and boilerplate. "
                    "No markdown, no questions at the end.\n\n" + page_text
                )
                self._speak_stream(self.llm.stream_reply(text, summary_ctx), clean=True)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # DEV MODE — "build a website called casamotori.sales" → strong AI model
        # Skip ONLY when the ask is really about opening an app or is a statement
        # about ongoing work AND no project name was given ("open VS Code and
        # build a website called X" must still build X).
        dev_m = _DEV_RE.search(low)
        dev_name = _dev_name_from(text) if dev_m else None
        if dev_m and dev_name is None and (_OPEN_APP_RE.search(low) or re.search(
                r"\bwe(?:'re|\s+are)\s+(?:working|creating|building|making)\b", low)):
            dev_m = None
        if dev_m:
            from actions.dev_builder import (detect_provider, provider_label,
                                             build_project, ALLOW_LOCAL)
            name = dev_name or f"curie-{dev_m.group('what').replace(' ', '-')}"
            provider, _k = detect_provider()
            label = provider_label(provider)
            if provider == "local" and not ALLOW_LOCAL:
                msg = ("I need a cloud AI to build a proper website — my small local model "
                       "would only make a skeleton, and I won't waste your time with that. "
                       "Add an OpenAI, Anthropic, or Gemini API key to my dot-env file, "
                       "then ask me again and I'll build the full thing.")
                self._log(f"Curie: {msg}")
                self.tts.speak(msg)
                if not self._stop.is_set():
                    self._state("LISTENING")
                return
            self.tts.speak(
                f"On it — putting {label} to work on {name}. "
                f"This takes a minute or two; I'll tell you when it's ready.")
            self._state("THINKING")
            ok, msg, path = build_project(name, text, log=self._log)
            if ok and path:
                self._last_project = path
                self._last_folder = path
            self._log(f"Curie: {msg}")
            self._state("SPEAKING")
            self.tts.speak(_strip_md(msg))
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # In-page navigation — "play the third video" (YouTube), "click the 2nd result"
        nth_m = _NTH_ITEM_RE.search(low)
        if nth_m:
            raw_ord = nth_m.group("ord").lower()
            n = _ORDINALS.get(raw_ord) or int(re.sub(r"\D", "", raw_ord) or 1)
            from actions.browser_nav import click_nth_item
            self.tts.speak("On it.")
            msg = click_nth_item(max(n, 1), nth_m.group("kind"))
            self._log(f"[page-nav] {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Page scrolling — "scroll down", "scroll up"
        scroll_m = _SCROLL_RE.search(low)
        if scroll_m:
            from actions.browser_nav import scroll_page
            msg = scroll_page(scroll_m.group("dir").lower())
            self._log(f"[page-nav] {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Menu navigation — "click new private window in safari", "select export"
        menu_m = _MENU_CLICK_RE.search(text.strip())
        if menu_m and not _MENU_CLICK_BLOCK.search(low):
            from actions.app_nav import click_menu_item
            from actions.open_app import _APP_ALIASES
            item = menu_m.group("item").strip()
            app_spoken = (menu_m.group("app") or "").strip().lower()
            app = None
            if app_spoken:
                alias = _APP_ALIASES.get(app_spoken, {})
                app = alias.get("Darwin") or app_spoken.title()
            elif self._last_opened_app:
                alias = _APP_ALIASES.get(self._last_opened_app.lower(), {})
                app = alias.get("Darwin") or self._last_opened_app.title()
            self.tts.speak("On it.")
            msg = click_menu_item(item, app)
            self._log(f"[menu-nav] {item!r} in {app or 'frontmost'} → {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # VS Code quick-open — "navigate to app.js in vs code" (any open project)
        vsnav_m = _VSCODE_NAV_RE.search(text)
        if vsnav_m:
            msg = self._vscode_quick_open(vsnav_m.group("file"))
            self._log(f"[vscode-nav] {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Tab navigation — "next tab", "previous tab", "switch to tab 2"
        tab_m = _TAB_NAV_RE.search(low)
        if tab_m:
            import subprocess as _sp
            direction, n = tab_m.group("dir"), tab_m.group("n")
            if n:
                keystroke = f'keystroke "{n}" using command down'          # browser: tab N
            elif direction and direction.lower() == "last":
                keystroke = 'keystroke "9" using command down'
            elif direction and direction.lower().startswith("prev"):
                keystroke = 'keystroke "{" using {command down, shift down}'
            else:
                keystroke = 'keystroke "}" using {command down, shift down}'
            script = f'tell application "System Events" to {keystroke}'
            try:
                r = _sp.run(["osascript", "-e", script], capture_output=True, timeout=8, text=True)
                ok = r.returncode == 0
                err = (r.stderr or "").strip()
            except Exception as e:
                ok, err = False, str(e)
            if ok:
                msg = f"Switched to {'tab ' + n if n else (direction or 'next') + ' tab'}."
            elif "not allowed" in err.lower() or "1002" in err:
                msg = ("I need permission to control your keyboard — allow it in "
                       "Privacy and Security, Accessibility, then ask me again.")
            else:
                msg = "I couldn't switch tabs there."
            self._log(f"[tab-nav] {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Open a project file — "open index.html" (from the last built project)
        file_m = _OPEN_FILE_RE.search(text)
        if file_m:
            import subprocess as _sp
            fname = file_m.group("file")
            target = None
            for base in (self._last_project, self._last_folder):
                if base is not None:
                    hits = [p for p in Path(base).rglob(fname)] if Path(base).exists() else []
                    if hits:
                        target = hits[0]
                        break
            if target:
                try:
                    _sp.run(["open", "-a", "Visual Studio Code", str(target)],
                            capture_output=True, timeout=10)
                    msg = f"Opened {fname} in VS Code."
                except Exception:
                    msg = f"I found {fname} but couldn't open it."
            else:
                # Not in a tracked project — try VS Code's own Quick Open
                msg = self._vscode_quick_open(fname)
            self._log(f"[open-file] {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # File creation — "create a file by the name Ashish.txt"
        if _FILE_RE.search(low):
            name_m = _FILE_NAME_RE.search(text)
            loc_m  = _FOLDER_LOC_RE.search(low)
            name = (name_m.group("name").strip() if name_m else "untitled.txt")
            if "." not in name:
                name += ".txt"
            named_m = _NAMED_PARENT_RE.search(text)
            named_dir = _find_named_folder(named_m.group("pname")) if named_m else None
            if named_dir is not None:
                parent, where = str(named_dir), f"inside {named_dir.name}"
            elif _FOLDER_PARENT_RE.search(low) and self._last_folder is not None:
                parent, where = str(self._last_folder), f"inside {self._last_folder.name}"
            else:
                parent = (loc_m.group("loc").lower() if loc_m else "desktop")
                where = parent
            msg, _ = self._dispatch("file_control",
                                    {"action": "create_file", "path": parent, "name": name})
            if msg.startswith("File created"):
                msg = f"Created the file {name} {'on your ' + where if where == parent else where}."
            self._log(f"[file] {where}/{name} → {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Folder rename — "rename the folder to Ashish" (acts on last created folder)
        if _RENAME_RE.search(low):
            name_m = _FOLDER_NAME_RE.search(text) or _RENAME_TO_RE.search(text)
            new_name = name_m.group("name").strip() if name_m else ""
            if new_name and self._last_folder is not None:
                msg, _ = self._dispatch("file_control", {
                    "action": "rename",
                    "path": str(self._last_folder.parent),
                    "name": self._last_folder.name,
                    "new_name": new_name,
                })
                if msg.startswith("Renamed"):
                    self._last_folder = self._last_folder.parent / new_name
            elif not new_name:
                msg = "Tell me the new name and I'll rename it."
            else:
                msg = "I don't have a recent folder to rename — tell me which one."
            self._log(f"[folder] rename → {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Folder creation — do it for real via file_controller, never fake it
        if _FOLDER_RE.search(low):
            from actions.file_controller import _resolve_path
            name_m = _FOLDER_NAME_RE.search(text)
            loc_m  = _FOLDER_LOC_RE.search(low)
            name = (name_m.group("name").strip() if name_m else "New Folder")
            named_m = _NAMED_PARENT_RE.search(text)
            named_dir = _find_named_folder(named_m.group("pname")) if named_m else None
            if named_dir is not None and named_dir.name.lower() != name.lower():
                parent, where = str(named_dir), f"inside {named_dir.name}"
            # "inside the folder we just created" → nest under the last folder
            elif _FOLDER_PARENT_RE.search(low) and self._last_folder is not None:
                parent = str(self._last_folder)
                where  = f"inside {self._last_folder.name}"
            else:
                parent = (loc_m.group("loc").lower() if loc_m else "desktop")
                where  = parent
            msg, _ = self._dispatch("file_control",
                                    {"action": "create_folder", "path": parent, "name": name})
            if msg.startswith("Folder created"):
                self._last_folder = _resolve_path(parent) / name
                msg = f"Created the folder {name} {'on your ' + where if where == parent else where}."
            self._log(f"[folder] {where}/{name} → {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Music — "play some music" → Apple Music, for real (YouTube/Spotify below)
        if _MUSIC_RE.search(low) and not re.search(r"youtube|spotify", low):
            import platform as _pf, subprocess as _sp
            state = "unsupported"
            if _pf.system() == "Darwin":
                script = '''
                tell application "Music"
                    activate
                    try
                        if (count of tracks of library playlist 1) is 0 then return "empty"
                        set shuffle enabled to true
                        play library playlist 1
                        delay 1
                        return (player state as text)
                    on error errMsg
                        return "error: " & errMsg
                    end try
                end tell'''
                try:
                    r = _sp.run(["osascript", "-e", script],
                                capture_output=True, timeout=25, text=True)
                    state = (r.stdout or "").strip() or f"error: {r.stderr.strip()[:80]}"
                except Exception as e:
                    state = f"error: {e}"
            if state == "playing":
                msg = "Playing your music."
            elif state == "empty":
                msg = ("Your Apple Music library has no songs, so there's nothing I can play. "
                       "I've opened Music — add or pick something once and I'll handle it next time.")
            else:
                msg = ("I opened Apple Music but playback didn't start — "
                       "if macOS asked for permission to control Music, allow it and try again.")
            self._log(f"[music] state={state!r} → {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Website bypass — "open youtube.com", "play some youtube songs"
        site_m = _WEBSITE_RE.search(low)
        site_url = None
        if site_m:
            site_url = "https://" + site_m.group("domain").lower()
        else:
            for kw, kw_url in _SITE_KEYWORDS.items():
                if re.search(rf"\b(open|play|go\s+to|visit|put\s+on|watch)\b.{{0,25}}\b{kw}\b", low):
                    site_url = kw_url
                    break
        if site_url:
            self._open_url(site_url)
            from urllib.parse import urlparse
            domain = urlparse(site_url).netloc
            msg = f"Opening {domain}."
            self._log(f"[website bypass] {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        # Open-app bypass — router LLM often returns conversational text instead of ACTION
        open_app_m = _OPEN_APP_RE.search(low)
        if open_app_m:
            from actions.open_app import open_app as _open_action
            app = open_app_m.group("app").strip()
            if app.lower() in self._BROWSER_APPS:
                self._last_browser = self._BROWSER_APPS[app.lower()]
            msg = _open_action({"app_name": app})
            self._last_opened_app = app
            self._log(f"[open-app bypass] {app} → {msg}")
            self.tts.speak(msg)
            if not self._stop.is_set():
                self._state("LISTENING")
            return

        context = self._build_context(text)
        self._state("THINKING")

        # Live-data bypass — news/prices/regional events must never come from LLM training data
        if _LIVE_DATA_RE.search(low):
            city_m = _WEATHER_CITY_RE.search(low)
            city = city_m.group("city").strip() if city_m else ""
            if not city:
                entry = _load_ltm().get("identity", {}).get("city", {})
                city = (entry.get("value") if isinstance(entry, dict) else entry) or ""
            if city and re.search(r"\bweather\b", low):
                when = "tomorrow" if "tomorrow" in low else "today"
                self._log(f"[live-data bypass] forcing weather_report (city: {city!r}, when: {when!r})")
                tool_name, params, conv_reply = "weather_report", {"city": city, "time": when}, ""
            else:
                query = ("latest world news today" if _NEWSY_RE.search(low)
                         else _clean_search_query(text))
                self._log(f"[live-data bypass] forcing web_search (query: {query!r})")
                tool_name, params, conv_reply = "web_search", {"query": query}, ""
        else:
            try:
                tool_name, params, conv_reply = self.router.route(text, context)
            except Exception as e:
                self._log(f"[router] error ({e}) — using stream fallback")
                tool_name, params, conv_reply = None, None, ""

        # NOTE: state stays "THINKING" (set above) through dispatch/generation —
        # self.tts.speak() now flips it to SPEAKING itself, exactly when real
        # audio starts, so the face never shows "speaking" during a silent wait.
        reply_log: list[str] = []

        def _tap(gen):
            for tok in gen:
                reply_log.append(tok)
                yield tok

        if tool_name:
            self._log(f"[router→{tool_name}] {params}")

            # Speak filler only for slow external lookups, not app launches etc.
            if tool_name == "web_search":
                self.tts.speak(random.choice(_SEARCH_FILLERS))
                self._state("THINKING")   # back to thinking while the fetch runs

            raw, url = self._dispatch(tool_name, params)

            # Shutdown sentinel (from router path)
            if raw == self._SHUTDOWN_SENTINEL:
                self.tts.speak("Goodbye.")
                self._log("Curie: Goodbye.")
                self.stop()
                return

            if url:
                self._open_url(url)

            # Direct message (e.g. "Sitdeck unreadable") — speak as-is, no LLM
            if raw and raw.startswith(self._DIRECT_PREFIX):
                msg = raw[len(self._DIRECT_PREFIX):].strip()
                reply_log.append(msg)
                self._speak_sentences([msg])
                self._log(f"Curie: {msg}")
                self._recent.append(f"{self._user_name}: {text}")
                self._recent.append(f"Curie: {msg[:120]}")
                return

            if raw and tool_name in ("web_search", "weather_report", "gmail_read", "gmail_search"):
                if tool_name == "web_search":
                    summary_ctx = (
                        "You are talking aloud to your friend — this will be read by a voice system. "
                        "Give a FULL rundown: cover every distinct story or fact in the CONTENT below, "
                        "with names, numbers, countries, what happened, when. Don't skip stories to "
                        "be brief — he'll say 'stop' if he's heard enough. Talk like a friend "
                        "catching them up on news, not like a news anchor. Be casual and specific. "
                        "STRICT: use ONLY the content below. If it doesn't actually contain news or "
                        "an answer, say you couldn't get it — NEVER invent headlines, NEVER add "
                        "anything from your own knowledge or from what you know about him personally. "
                        "ZERO formatting: no bullet points, no headers, no asterisks, no pound signs, "
                        "no numbered lists, no markdown of any kind. Plain words only. "
                        "No URLs, no source names, no open-ended questions at the end.\n\nCONTENT:\n"
                        + _strip_md(raw)
                    )
                elif tool_name in ("gmail_read", "gmail_search"):
                    summary_ctx = (
                        "Summarise these emails for a voice assistant. "
                        "For each one say: who it's from and what it's about in one sentence. "
                        "Be casual and concise. No markdown, no bullet points, no questions at the end.\n\n"
                        + raw
                    )
                else:
                    summary_ctx = (
                        "Summarise in 2-3 casual spoken sentences like talking to a friend. "
                        "Specific facts only. No markdown, no formatting chars, no questions.\n\n"
                        + _strip_md(raw)
                    )
                self._speak_stream(_tap(self.llm.stream_reply(text, summary_ctx)), clean=True)
            else:
                self._speak_sentences([raw])

        elif conv_reply:
            cleaned = _clean_reply(conv_reply)
            if cleaned:
                sentences = [s for s in _SENTENCE_END.split(cleaned) if s.strip()]
                for s in sentences:
                    reply_log.append(s)
                self._speak_sentences(sentences)
            else:
                # Router reply was entirely clichés — fall through to stream LLM
                self._log("[clean] router reply fully filtered, using stream fallback")
                self._speak_stream(_tap(self.llm.stream_reply(text, context)))

        else:
            self._speak_stream(_tap(self.llm.stream_reply(text, context)))

        full = "".join(reply_log).strip()   # tokens already carry their own spacing
        if full:
            self._log(f"Curie: {full}")

        # Track recent turns so pronouns like "the same" / "it" resolve correctly
        self._recent.append(f"{self._user_name}: {text}")
        if full:
            self._recent.append(f"Curie: {full[:120]}")

        threading.Thread(
            target=self._persist, args=(text, full), daemon=True
        ).start()

    # ── memory persistence ────────────────────────────────────
    def _persist(self, user_text: str, reply_text: str):
        try:
            self.memory.remember("user", user_text)
            if reply_text:
                self.memory.remember("assistant", reply_text)
            self._mine(user_text)
        except Exception as e:
            print(f"[Curie] persist error: {e}")

    def _mine(self, user_text: str):
        """Extract one structured personal fact → long_term.json (background)."""
        try:
            out = self.llm.complete(_MINE_PROMPT.format(text=user_text))
        except Exception:
            return

        out = (out or "").strip()
        if not out or out.upper().startswith("NONE"):
            return

        data: dict[str, str] = {}
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                data[k.strip().lower()] = v.strip()

        category = data.get("category", "notes")
        key      = data.get("key",      "").replace(" ", "_").lower()
        value    = data.get("value",    "")

        valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
        if category not in valid:
            category = "notes"
        # The user's name is locked — never even attempt to mine it (the model
        # kept trying to store other people's names as the user's identity)
        if category == "identity" and key in ("name", "location_history"):
            return
        if key and value and len(value) < 300:
            _update_ltm({category: {key: value}})
            self._log(f"(remembered {category}/{key}: {value})")

    # ── typed command entry ───────────────────────────────────
    def handle_text(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        self._log(f"You: {text}")
        self._respond(text)
        if not self._stop.is_set():
            self._state("LISTENING")

    # ── wake-word gating ──────────────────────────────────────
    def _apply_wake(self, text: str):
        if settings.WAKE_MODE != "keyword":
            return text
        word = settings.WAKE_WORD.lower()
        low  = text.lower()
        if word not in low:
            return None
        idx = low.find(word)
        return text[idx + len(word):].lstrip(" ,.!?-").strip()

    # ── startup greeting ──────────────────────────────────────
    def _deliver_briefing(self):
        """Short human greeting every time Curie starts — no weather, no news."""
        if not settings.BRIEFING_ENABLED:
            return
        try:
            from engine.briefing import build_briefing
            brief = build_briefing(_load_ltm())
            if brief:
                self._state("SPEAKING")
                self._speak_sentences(
                    [s for s in _SENTENCE_END.split(brief) if s.strip()],
                    barge_in=False,
                )
        except Exception as e:
            self._log(f"[greeting] skipped: {e}")

    # ── wake-from-sleep detection ─────────────────────────────
    def _wake_watcher(self):
        """Detect the lid opening (wake from sleep) → greet again.

        macOS pauses time.monotonic() during sleep while time.time() keeps
        running, so a large gap between the two deltas means we slept.
        Sets a flag; the main loop greets between listens (never mid-capture,
        so Curie doesn't hear her own greeting).
        """
        import time
        last_wall, last_mono = time.time(), time.monotonic()
        while not self._stop.is_set():
            time.sleep(20)
            wall, mono = time.time(), time.monotonic()
            slept = (wall - last_wall) - (mono - last_mono)
            if slept > 300:   # asleep for more than 5 minutes
                self._log(f"[wake] system was asleep ~{int(slept / 60)} min — will greet")
                self._wake_pending = True
            last_wall, last_mono = wall, mono

    # ── main loop ─────────────────────────────────────────────
    def run(self):
        # Deliver briefing once before entering the listen loop
        self._deliver_briefing()
        self._wake_pending = False
        threading.Thread(target=self._wake_watcher, daemon=True).start()

        wake = None
        if settings.WAKE_MODE == "openwakeword":
            from engine.wake import WakeWord
            try:
                wake = WakeWord()
                self._log(f"Wake word active: say '{settings.WAKE_MODEL.replace('_', ' ')}'")
            except Exception as e:
                self._log(f"[wake] openWakeWord failed ({e}), falling back to always-listen")

        while not self._stop.is_set():
            # Lid was opened / Mac woke up → fresh greeting before listening
            if getattr(self, "_wake_pending", False):
                self._wake_pending = False
                self._deliver_briefing()

            self._state("LISTENING")

            if wake:
                if not wake.wait(stop=self._stop):
                    break

            # Everything below is one voice turn. NEVER let a single turn's
            # failure (a flaky audio device, a tool crash, a network hiccup)
            # take down the whole session — catch, log, and keep listening.
            # This is the fix for the "one bad sd.play() ended Curie forever"
            # bug: previously an uncaught exception here propagated all the
            # way out of run(), and main.py has no restart logic.
            try:
                audio = self.vad.listen()
                if self._stop.is_set():
                    break
                if audio.size == 0:
                    continue

                self._state("THINKING")
                text = self.stt.transcribe(audio)
                if not text:
                    continue
                self._log(f"[heard] {text}")

                command = self._apply_wake(text)
                if command is None:
                    continue
                if command == "":
                    self._state("SPEAKING")
                    self.tts.speak("Yes?")
                    self._state("LISTENING")
                    follow = self.stt.transcribe(self.vad.listen())
                    if not follow:
                        continue
                    command = follow

                self._log(f"You: {command}")
                self._respond(command)
            except Exception as e:
                self._log(f"[turn error] {e} — staying online")
                try:
                    self._state("LISTENING")
                except Exception:
                    pass
                continue

    def stop(self):
        self._stop.set()
        try: self.tts.stop()
        except Exception: pass
        # Voice shutdown should close the whole app, not just go quiet while
        # the window lingers on screen.
        if self.ui:
            try: self.ui.shutdown()
            except Exception: pass


# standalone test:  python -m engine.orchestrator
if __name__ == "__main__":
    orch = Orchestrator()
    print("Speak to Curie (Ctrl+C to quit)")
    try:
        orch.run()
    except KeyboardInterrupt:
        orch.stop()
        print("\nStopped.")
