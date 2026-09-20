# Curie Setup Guide (macOS & Windows)

Curie is a fully local, privacy-first voice assistant. Everything below gets you
from a clean checkout to a running assistant on either platform.

## 1. Prerequisites (both platforms)

| Requirement | Notes |
|---|---|
| Python 3.12 (3.11 also works) | https://www.python.org/downloads/ |
| Ollama | https://ollama.com/download — runs the local LLM |
| Git | to clone this repo |

## 2. Pull a local LLM

```bash
ollama pull qwen2.5:3b     # lighter model — good for 8GB RAM machines
# or, on a machine with more RAM/VRAM:
ollama pull qwen3:4b
```

By default `settings.py` uses `qwen2.5:3b`. To use a different model without
editing code, set an environment variable before launching:

```bash
# macOS/Linux
export CURIE_MODEL=qwen3:4b
# Windows (PowerShell)
$env:CURIE_MODEL = "qwen3:4b"
```

Note: `settings.py` also defines `EMBED_MODEL = "nomic-embed-text"`, but nothing in
the codebase actually uses it — MemPalace (the memory backend) ships its own
bundled MiniLM embedder. You do **not** need to `ollama pull` an embedding model.

## 3. macOS setup

```bash
xcode-select --install        # native build tools (if not already installed)

cd Curie
python3.12 -m venv .venv-mac
source .venv-mac/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

Grant Microphone permission the first time macOS prompts for it (System
Settings → Privacy & Security → Microphone → allow your terminal / Curie).

Run:

```bash
python main.py     # GUI (galaxy face UI)
python start.py    # headless
```

### macOS auto-start (optional)

`launch_curie.sh` is a watchdog script that restarts Curie if it crashes but
respects a normal quit. To run it at login, wrap it in an AppleScript app and add
that app as a macOS Login Item (System Settings → General → Login Items &
Extensions):

```bash
osacompile -o ~/Desktop/Curie.app -e 'tell application "Terminal"
    do script "/path/to/Curie/launch_curie.sh"
    delay 1
    try
        set miniaturized of front window to true
    end try
end tell'
codesign --force --deep -s - ~/Desktop/Curie.app
```

## 4. Windows setup

```powershell
cd Curie
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

`requirements.txt` uses environment markers (`; sys_platform == "win32"`) for
Windows-only packages, so the same file installs the right things on both
platforms automatically:

- `win10toast` — desktop toast notifications (`actions/reminder.py`)
- `pycaw` + `comtypes` — system volume control (`actions/computer_settings.py`,
  falls back to a keypress simulation if this fails to load)
- `pywin32` — reserved for a planned feature (not used by current code yet)

Grant Microphone permission the first time Windows prompts for it (Settings →
Privacy & Security → Microphone).

Run:

```powershell
python main.py     # GUI
python start.py    # headless
```

### Windows auto-start (optional)

Create a shortcut to a `.bat` file that runs `python main.py` (or an
equivalent watchdog loop to `launch_curie.sh`) and place the shortcut in:

```
shell:startup
```

(paste that into File Explorer's address bar to open the Startup folder).

## 5. Configure secrets (both platforms)

```bash
cp .env.example .env
```

Open `.env` and uncomment **one** dev-mode API key if you want the "build me a
website/app" cloud-assist mode (Gemini has a free tier). This is optional — the
core voice assistant runs fully local without any key.

For Gmail integration (read/search/send), run:

```bash
python setup_gmail.py
```

This walks you through a one-time OAuth flow and writes `data/gmail_credentials.json`
and `data/gmail_token.json` locally — these never leave your machine and are
already gitignored.

## 6. First run

On first launch, Curie creates `data/` fresh (MemPalace memory store, profile,
logs, scrape cache). It starts with no memory of past conversations — that data
is intentionally never committed to this repo.

## Dependency summary

**Core (all platforms):** faster-whisper, sounddevice, numpy, scipy,
webrtcvad-wheels, ollama, kokoro, soundfile, mempalace, chromadb, openwakeword,
ddgs/duckduckgo-search, pyqt6, pillow, playwright, pyautogui, pyperclip, psutil,
requests, beautifulsoup4, feedparser, send2trash, python-pptx, plyer.

**Optional (only if the corresponding `.env` key is set):** anthropic (dev-mode
website building), google-api-python-client + google-auth family (Gmail).

**Windows-only:** win10toast, pycaw, comtypes, pywin32 (see above).

See `requirements.txt` for the authoritative, version-pinned list.
