"""Startup spoken greeting — warm, time-of-day AND late-night aware.

Called once when Curie boots, and again on real wake-from-sleep (see
Orchestrator._wake_watcher). By deliberate, twice-confirmed design: this
NEVER reads news or weather unprompted. Curie greets, then goes silent and
waits for the next command — she does not push content.
"""
from __future__ import annotations
import datetime
import random


def _time_bucket(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 23:
        return "evening"
    return "late_night"   # 23:00–04:59 — awake unusually late (or early)


_GREETING_WORD = {
    "morning":    "Good morning",
    "afternoon":  "Good afternoon",
    "evening":    "Good evening",
    "late_night": "Still up",
}

_MORNING_LINES = [
    "Grabbed your coffee yet, boss?",
    "Slept well? What are we up to today?",
    "Fresh start — what's on the agenda?",
    "Hope the morning's treating you well. What's on your mind?",
]
_AFTERNOON_LINES = [
    "How's the day going so far?",
    "Had lunch yet, or running on fumes?",
    "What are we tackling this afternoon?",
    "Midday check-in — what's next?",
]
_EVENING_LINES = [
    "Long day? I'm all ears.",
    "How did the day go?",
    "Winding down, or still grinding, boss?",
    "What's the vibe tonight?",
]
_LATE_NIGHT_LINES = [
    "Burning the midnight oil again, boss?",
    "Couldn't sleep, or just deep in something?",
    "It's late — everything alright?",
    "Night owl mode. What are we working on?",
]

_LINE_POOL = {
    "morning":    _MORNING_LINES,
    "afternoon":  _AFTERNOON_LINES,
    "evening":    _EVENING_LINES,
    "late_night": _LATE_NIGHT_LINES,
}


def build_briefing(memory: dict | None = None) -> str:
    """Return a single spoken greeting string — greeting only, never raises."""
    now    = datetime.datetime.now()
    bucket = _time_bucket(now.hour)
    return f"{_GREETING_WORD[bucket]}, boss.  {random.choice(_LINE_POOL[bucket])}"


# standalone test:  python -m engine.briefing
if __name__ == "__main__":
    print(build_briefing())
