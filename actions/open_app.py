import time
import subprocess
import platform
import shutil

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

# macOS 13+ (Ventura/Sonoma/Sequoia) uses "System Settings"; older uses "System Preferences"
def _mac_settings_app() -> str:
    try:
        ver = tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
        return "System Settings" if ver >= (13, 0) else "System Preferences"
    except Exception:
        return "System Settings"

_SETTINGS_APP = _mac_settings_app()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "browser":            {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "vs code":            {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "v s code":           {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio":      {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "music":              {"Windows": "Spotify",                 "Darwin": "Music",                "Linux": "spotify"},
    "apple music":        {"Windows": "Spotify",                 "Darwin": "Music",                "Linux": "spotify"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": _SETTINGS_APP,          "Linux": "gnome-control-center"},
    "system settings":    {"Windows": "ms-settings:",            "Darwin": _SETTINGS_APP,          "Linux": "gnome-control-center"},
    "system preferences": {"Windows": "ms-settings:",            "Darwin": _SETTINGS_APP,          "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


# macOS System Settings pane URL schemes (works on Ventura / Sonoma / Sequoia)
_MAC_SETTINGS_PANES: dict[str, str] = {
    "battery":     "x-apple.systempreferences:com.apple.Battery-Settings.extension",
    "display":     "x-apple.systempreferences:com.apple.Displays-Settings.extension",
    "displays":    "x-apple.systempreferences:com.apple.Displays-Settings.extension",
    "wifi":        "x-apple.systempreferences:com.apple.wifi-settings-extension",
    "network":     "x-apple.systempreferences:com.apple.Network-Settings.extension",
    "bluetooth":   "x-apple.systempreferences:com.apple.BluetoothSettings",
    "sound":       "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "volume":      "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "privacy":     "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
    "security":    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
    "notification": "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
    "notifications": "x-apple.systempreferences:com.apple.Notifications-Settings.extension",
    "accessibility": "x-apple.systempreferences:com.apple.Accessibility-Settings.extension",
    "storage":     "x-apple.systempreferences:com.apple.settings.Storage",
    "keyboard":    "x-apple.systempreferences:com.apple.Keyboard-Settings.extension",
    "trackpad":    "x-apple.systempreferences:com.apple.Trackpad-Settings.extension",
    "mouse":       "x-apple.systempreferences:com.apple.Mouse-Settings.extension",
    "users":       "x-apple.systempreferences:com.apple.Users-Groups-Settings.extension",
    "general":     "x-apple.systempreferences:com.apple.General-Settings.extension",
    "appearance":  "x-apple.systempreferences:com.apple.Appearance-Settings.extension",
    "focus":       "x-apple.systempreferences:com.apple.Focus-Settings.extension",
    "screen time": "x-apple.systempreferences:com.apple.Screen-Time-Settings.extension",
    "touch id":    "x-apple.systempreferences:com.apple.Touch-ID-Settings.extension",
    "wallpaper":   "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension",
    "lock screen": "x-apple.systempreferences:com.apple.Lock-Screen-Settings.extension",
    "date":        "x-apple.systempreferences:com.apple.Date-Time-Settings.extension",
    "time":        "x-apple.systempreferences:com.apple.Date-Time-Settings.extension",
    "software update": "x-apple.systempreferences:com.apple.Software-Update-Settings.extension",
    "update":      "x-apple.systempreferences:com.apple.Software-Update-Settings.extension",
}


def _match_settings_pane(app_name: str) -> str | None:
    """Return a macOS Settings URL scheme if app_name refers to a settings pane."""
    lower = app_name.lower()
    for keyword, url in _MAC_SETTINGS_PANES.items():
        if keyword in lower:
            return url
    return None


def _open_new_tab_macos(browser: str = "Google Chrome") -> bool:
    """Open a new tab in an already-running browser, or launch it to a new tab."""
    # Try AppleScript first (works if browser is already running)
    script = f'tell application "{browser}" to make new tab at end of tabs of front window'
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            subprocess.run(["osascript", "-e", f'tell application "{browser}" to activate'],
                           capture_output=True, timeout=3)
            return True
    except Exception:
        pass
    # Fallback: open browser with a blank URL (forces new tab)
    try:
        subprocess.Popen(["open", "-a", browser, "about:newtab"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.15)   # just enough for the OS to accept the launch request
        return True
    except Exception:
        pass
    return False


def _launch_macos(app_name: str) -> bool:
    # New-tab shortcut
    if "new tab" in app_name.lower():
        browser = "Google Chrome"
        if "safari" in app_name.lower():
            browser = "Safari"
        elif "firefox" in app_name.lower():
            browser = "Firefox"
        return _open_new_tab_macos(browser)

    # Check for Settings pane shortcuts first
    pane_url = _match_settings_pane(app_name)
    if pane_url:
        try:
            result = subprocess.run(["open", pane_url], capture_output=True, timeout=8)
            if result.returncode == 0:
                # `open` already blocked until launch was dispatched — the returncode
                # IS the success confirmation. This is no longer a "wait and hope"
                # buffer, just a hair of settle time before Curie speaks (Day 3:
                # "parallel speak+act" — the reply shouldn't wait on the app's own
                # cold-start time, only on our own confirmed dispatch).
                time.sleep(0.15)
                return True
        except Exception:
            pass

    # Standard app launch
    for candidate in [app_name, f"{app_name}.app"]:
        try:
            result = subprocess.run(
                ["open", "-a", candidate],
                capture_output=True, timeout=8
            )
            if result.returncode == 0:
                time.sleep(0.15)
                return True
        except Exception:
            pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(0.15)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


def _launch_linux(app_name: str) -> bool:

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"