"""faster-whisper STT — local, CPU, no torch path. Loaded once, kept warm."""
import re

import numpy as np

import settings

# STT often mishears "Curie" as these. Strip or correct them so downstream
# logic gets clean text regardless of how the user pronounces Curie's name.
_CURIE_VARIANTS = re.compile(
    r"\b(q\.?d\.?|curie|kuri|keri|curry|yuri|yury|high.?cure|high.?curi|hey\s+cure|"
    r"hey\s+kuri|hey\s+curi|hey\s+yuri|hey\s+q\.?d\.?|okay\s+q\.?d\.?|ok\s+q\.?d\.?)\b[,.]?\s*",
    re.IGNORECASE,
)

# Common word-level STT errors specific to this domain
_WORD_FIXES = [
    (re.compile(r"\bwhether\b", re.IGNORECASE), "weather"),
    (re.compile(r"\btalk\s+five\b", re.IGNORECASE), "top five"),
    (re.compile(r"\btalk\s+(\d+)\b", re.IGNORECASE), r"top \1"),
    (re.compile(r"\btill\s+me\b", re.IGNORECASE), "tell me"),
    (re.compile(r"\bset\s+deck\b", re.IGNORECASE), "sitdeck"),
    (re.compile(r"\bsit\s+deck\b", re.IGNORECASE), "sitdeck"),
]


def _normalize(text: str) -> str:
    """Strip Curie-prefix mishearings and fix common domain errors."""
    text = _CURIE_VARIANTS.sub("", text).strip()
    for pattern, replacement in _WORD_FIXES:
        text = pattern.sub(replacement, text)
    # Clean up punctuation left after stripping prefix
    text = re.sub(r"^[,.\s]+", "", text).strip()
    return text


class WhisperSTT:
    def __init__(self):
        from faster_whisper import WhisperModel
        self._model = WhisperModel(
            settings.WHISPER_MODEL,
            device="cpu",
            compute_type=settings.WHISPER_COMPUTE,
            cpu_threads=settings.WHISPER_CPU_THREADS,
            num_workers=1,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32 mono @ 16 kHz, range [-1, 1]. Returns cleaned text."""
        audio = np.asarray(audio, dtype=np.float32).flatten()
        if audio.size == 0:
            return ""
        segments, _ = self._model.transcribe(
            audio,
            language=settings.WHISPER_LANG,
            beam_size=3,       # was 1 (tiny-optimal); base can afford 3 for accuracy
            vad_filter=True,   # drop silence segments
        )
        raw = " ".join(s.text.strip() for s in segments).strip()
        return _normalize(raw)


# standalone test:  python -m engine.stt   (records 5s, prints transcript)
if __name__ == "__main__":
    import sounddevice as sd

    secs = 5
    print(f"Recording {secs}s — speak now...")
    rec = sd.rec(int(secs * settings.SAMPLE_RATE_IN),
                 samplerate=settings.SAMPLE_RATE_IN,
                 channels=1, dtype="float32")
    sd.wait()
    print("Transcribing...")
    print("You said:", WhisperSTT().transcribe(rec))
