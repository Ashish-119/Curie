"""Curie central config. Tweak everything here.

Named `settings` (not `config`) to avoid the existing `config/` package.
"""
import os
from pathlib import Path

# Cap CPU threads BEFORE torch / ctranslate2 / MKL load — keeps memory low on 8 GB
# and avoids the duplicate-libiomp crash when Kokoro(torch) + Whisper(MKL) coexist.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# ChromaDB (MemPalace's storage backend) defaults to sending anonymous
# usage telemetry to PostHog. Never content, but Curie's privacy rule is
# zero network unless explicitly needed — disable it before chromadb loads.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _load_env() -> None:
    """Minimal .env loader (no extra deps)."""
    env = BASE_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        # strip inline comments (e.g.  VAR=value  # comment)
        v = v.strip().strip('"').strip("'")
        if "  #" in v:
            v = v[:v.index("  #")].rstrip()
        os.environ.setdefault(k.strip(), v)


_load_env()

# ── LLM (Ollama) ──────────────────────────────────────────────
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# qwen2.5:3b for Mac dev (no thinking mode, ~120 tok/s on M2 Metal)
# qwen3:4b for Windows demo (thinking mode, needs Ollama >= 0.6.x)
LLM_MODEL   = os.environ.get("CURIE_MODEL", "qwen2.5:3b")
EMBED_MODEL = "nomic-embed-text"
LLM_KEEP_ALIVE = "30m"      # keep model warm between turns

# ── Audio ─────────────────────────────────────────────────────
SAMPLE_RATE_IN  = 16000     # mic / whisper
SAMPLE_RATE_OUT = 24000     # kokoro
CHANNELS        = 1

# ── STT (faster-whisper) ──────────────────────────────────────
WHISPER_MODEL       = "base"   # much better accuracy than tiny; auto-downloads on first run
WHISPER_COMPUTE     = "int8"
WHISPER_LANG        = "en"
WHISPER_CPU_THREADS = 3     # base model benefits from one extra thread

# ── TTS (Kokoro) ──────────────────────────────────────────────
TTS_VOICE     = "af_heart"  # most expressive Kokoro voice — shifts pitch/tone with emotion
TTS_LANG_CODE = "a"         # 'a' = American English
TTS_SPEED     = 1.05        # slightly above 1.0 — natural conversational pace, more fluent

# ── VAD ───────────────────────────────────────────────────────
VAD_AGGRESSIVENESS = 1      # 0-3; 1 works better on quiet laptop mics (2 can miss speech)
VAD_SILENCE_MS     = 3000   # wait 3s of silence — let him finish the whole thought before acting
VAD_MAX_SECONDS    = 45     # was 15 — long commands were getting cut off mid-sentence
BARGE_IN_THRESHOLD  = 9000  # int16 amplitude — raised so small noises/background voices don't interrupt
BARGE_IN_GAP_FRAMES = 5    # frames to sample in inter-sentence gap (5×20ms = 100ms — keep short for natural pace)
BARGE_IN_FRAMES_REQ = 4    # consecutive frames needed to confirm real voice (4×20ms = 80ms)

# ── Mid-answer interrupt ("stop" while Curie is talking) ──────
# Curie monitors the mic WHILE speaking. Only sustained loud voice (e.g. a firm
# "stop" / "no") cuts her off — quiet background sounds never will.
INTERRUPT_ENABLED   = True
# Was 12000/500ms — required near-shouting, so a normal-volume follow-up
# question never interrupted her. Lowered to catch ordinary talking-over-her
# volume; the sustained-duration requirement still filters out coughs/bangs/
# background noise (a stray loud blip essentially never holds for 350ms).
INTERRUPT_THRESHOLD = 6000    # int16 amplitude — a clearly deliberate voice, not a whisper
INTERRUPT_MS        = 350     # sustained speech needed before playback stops

# ── LLM reply length / context ────────────────────────────────
LLM_MAX_TOKENS = 1200   # was 700 — still cut off mid-explanation on longer answers
# Ollama's default num_ctx is 4096 tokens — Sitdeck content (8k+ chars) plus the
# system prompt silently overflowed it, so the model never saw the real news and
# recited training data instead. qwen2.5:3b supports much more; 8192 fits in 8 GB.
LLM_NUM_CTX    = 8192

# ── Greeting on startup / wake ─────────────────────────────────
# Greeting only, by deliberate design — never weather or news unprompted.
# Curie greets, then waits silently for the next command.
BRIEFING_ENABLED = True

# ── Private sources (Sitdeck) ─────────────────────────────────
PRIVATE_LOAD_WAIT_S  = 30  # max seconds to wait for a slow SPA (Sitdeck) to render real content
PRIVATE_LOGIN_WAIT_S = 60  # extra wait in the VISIBLE window so the user can log in live

# ── Wake word ─────────────────────────────────────────────────
# "keyword"      = custom name via transcript match — say "Curie" to trigger
# "openwakeword" = prebuilt neural model (no "curie" model exists; uses "hey_jarvis")
# "off"          = always-listen, respond to everything (good for testing)
WAKE_MODE = "off"           # set to "keyword" once tested end-to-end
WAKE_WORD = "curie"         # spoken trigger in keyword mode
# openWakeWord (only used when WAKE_MODE == "openwakeword"):
WAKE_MODEL     = "hey_jarvis"
WAKE_THRESHOLD = 0.5
