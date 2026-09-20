"""Navigate inside macOS applications — click menu items by voice.

Every app command lives in its menu bar (File/Edit/View/…), and macOS
Accessibility can click those by name. Curie dumps the app's menus, fuzzy-
matches what Ashish said, and clicks the best match:

    "click new private window in safari"  → Safari ▸ File ▸ New Private Window
    "select show hidden files"            → frontmost app's matching item

Needs the same Accessibility permission as tab switching (one-time grant).
"""
from __future__ import annotations

import re
import subprocess

_OSA_TIMEOUT = 20


def _osascript(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=_OSA_TIMEOUT, text=True)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def frontmost_app() -> str | None:
    ok, out = _osascript(
        'tell application "System Events" to get name of first process whose frontmost is true')
    return out if ok and out else None


def _dump_menus(app: str) -> list[tuple[str, str]]:
    """Return [(menu_name, item_name), …] for the app's top-level menu items."""
    # Probe first so a missing Accessibility grant surfaces as PermissionError
    # instead of silently returning zero menus (error -1719).
    ok, probe = _osascript(
        f'tell application "System Events" to tell process "{app}" to get name of menu bar 1')
    if not ok and ("assistive access" in probe.lower() or "-1719" in probe
                   or "not allowed" in probe.lower()):
        raise PermissionError(probe)
    script = f'''
    tell application "System Events" to tell process "{app}"
        set out to ""
        repeat with mb in menu bar items of menu bar 1
            set mbName to name of mb
            try
                repeat with mi in menu items of menu 1 of mb
                    set miName to name of mi
                    if miName is not missing value and miName is not "" then
                        set out to out & mbName & "|;|" & miName & linefeed
                    end if
                end repeat
            end try
        end repeat
        return out
    end tell'''
    ok, out = _osascript(script)
    if not ok:
        raise PermissionError(out)
    items = []
    for line in out.splitlines():
        if "|;|" in line:
            menu, item = line.split("|;|", 1)
            items.append((menu.strip(), item.strip()))
    return items


def _score(spoken: str, item: str) -> float:
    """Fuzzy match score between the spoken phrase and a menu item name."""
    s = re.sub(r"[^a-z0-9 ]", "", spoken.lower()).strip()
    i = re.sub(r"[^a-z0-9 ]", "", item.lower()).strip()
    if not s or not i:
        return 0.0
    if s == i:
        return 100.0
    if s in i:
        return 80.0 + 10.0 * len(s) / len(i)
    s_words = set(s.split())
    i_words = set(i.split())
    overlap = len(s_words & i_words)
    if overlap == 0:
        return 0.0
    return 60.0 * overlap / max(len(s_words), len(i_words))


def click_menu_item(spoken: str, app: str | None = None) -> str:
    """Find + click the best-matching menu item. Returns a spoken message."""
    app = app or frontmost_app()
    if not app:
        return "I couldn't tell which app is in front."
    _osascript(f'tell application "{app}" to activate')   # menus load when frontmost
    try:
        items = _dump_menus(app)
    except PermissionError as e:
        err = str(e).lower()
        if "assistive" in err or "not allowed" in err or "-1719" in err or "1002" in err:
            return ("I need Accessibility permission to click menus — open Privacy "
                    "and Security, then Accessibility, and switch on Terminal. "
                    "Then ask me again.")
        return f"I couldn't read {app}'s menus."
    if not items:
        return f"{app} doesn't show me any menus I can click."

    best = max(items, key=lambda mi: _score(spoken, mi[1]))
    if _score(spoken, best[1]) < 45:
        return (f"I couldn't find anything like '{spoken}' in {app}'s menus — "
                f"say the option's name as it appears in the menu.")

    menu, item = best
    safe_menu = menu.replace('"', '\\"')
    safe_item = item.replace('"', '\\"')
    script = f'''
    tell application "{app}" to activate
    delay 0.3
    tell application "System Events" to tell process "{app}"
        click menu item "{safe_item}" of menu 1 of menu bar item "{safe_menu}" of menu bar 1
    end tell'''
    ok, out = _osascript(script)
    if ok:
        return f"Done — {item}, in {app}'s {menu} menu."
    if "not allowed" in out.lower() or "1002" in out:
        return ("I need Accessibility permission for that — Privacy and Security, "
                "Accessibility, then ask me again.")
    return f"I found {item} in the {menu} menu but couldn't click it."


# standalone test:  python -m actions.app_nav   (read-only menu dump of Finder)
if __name__ == "__main__":
    print("frontmost:", frontmost_app())
    menus = _dump_menus("Finder")
    print(f"Finder menu items: {len(menus)}")
    for m in menus[:10]:
        print("  ", m)
    print("score('empty trash', 'Empty Trash…') =", _score("empty trash", "Empty Trash…"))
