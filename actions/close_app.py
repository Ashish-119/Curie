"""Close a running application or browser on macOS / Windows / Linux."""
from __future__ import annotations

import platform
import subprocess

_OS = platform.system()

_ALIASES: dict[str, str] = {
    # browsers
    "browser":        "Google Chrome",
    "chrome":         "Google Chrome",
    "google chrome":  "Google Chrome",
    "safari":         "Safari",
    "firefox":        "Firefox",
    "edge":           "Microsoft Edge",
    "brave":          "Brave Browser",
    # media / comms
    "spotify":        "Spotify",
    "slack":          "Slack",
    "discord":        "Discord",
    "whatsapp":       "WhatsApp",
    "zoom":           "zoom.us",
    "telegram":       "Telegram",
    # dev
    "vscode":         "Visual Studio Code",
    "vs code":        "Visual Studio Code",
    "visual studio":  "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "code":           "Visual Studio Code",
    "terminal":       "Terminal",
    "finder":         "Finder",
    # system
    "settings":           "System Settings",
    "system settings":    "System Settings",
    "system preferences": "System Settings",
    "music":              "Music",
    "apple music":        "Music",
    "calculator":         "Calculator",
    "photos":             "Photos",
    "preview":            "Preview",
    "activity monitor":   "Activity Monitor",
    # productivity
    "mail":           "Mail",
    "calendar":       "Calendar",
    "messages":       "Messages",
    "notes":          "Notes",
    "word":           "Microsoft Word",
    "excel":          "Microsoft Excel",
    "powerpoint":     "Microsoft PowerPoint",
    # tabs / windows (treat as Chrome)
    "tab":            "Google Chrome",
    "window":         "Google Chrome",
    "app":            "Google Chrome",
    "application":    "Google Chrome",
}


def close_app(parameters: dict) -> str:
    raw = (parameters.get("app_name") or "browser").strip().lower()
    app_name = _ALIASES.get(raw, raw.title())

    if _OS == "Darwin":
        # AppleScript quit — cleanest on macOS
        try:
            r = subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to quit'],
                capture_output=True, text=True, timeout=6,
            )
            if r.returncode == 0:
                return f"Closed {app_name}."
        except Exception:
            pass
        # pkill fallback (catches apps that ignore AppleScript quit)
        try:
            subprocess.run(["pkill", "-ix", app_name], timeout=4, capture_output=True)
            return f"Closed {app_name}."
        except Exception:
            pass
        return f"Couldn't find {app_name} running."

    elif _OS == "Windows":
        exe = app_name.replace(" ", "").lower() + ".exe"
        try:
            subprocess.run(["taskkill", "/IM", exe, "/F"],
                           timeout=5, capture_output=True)
            return f"Closed {app_name}."
        except Exception:
            return f"Couldn't close {app_name}."

    else:  # Linux
        try:
            subprocess.run(["pkill", "-i", raw], timeout=4, capture_output=True)
            return f"Closed {app_name}."
        except Exception:
            return f"Couldn't close {app_name}."
