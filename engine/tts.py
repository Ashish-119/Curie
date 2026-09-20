"""Kokoro TTS — natural female voice, gapless playback, sentence-level prosody."""
import re
import threading
from math import gcd

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

import settings

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Sentences are now played one at a time (see KokoroTTS.speak) — the pause
# between them is however long the post-sentence interrupt-listening window
# takes (settings.INTERRUPT_MS + margin), which doubles as a real human-like
# breathing gap instead of the old ~10ms crossfade that glued everything into
# one flat run-on.

# Kokoro outputs at SAMPLE_RATE_OUT (24 kHz); resample to device native rate.
#
# NOTE: the device rate is queried FRESH on every speak() call, not cached at
# import time. Caching it here used to bite hard: at Python startup, macOS's
# CoreAudio HAL is often not fully warmed up yet (see the "!obj" AUHAL warnings
# that print right at launch), so an import-time query could freeze in a
# stale/bad state — and ~15s later, when Kokoro/Whisper finished loading and
# Curie tried to actually speak, sd.play() would fail outright and kill the
# whole turn. A live query plus one auto-recover retry (below) fixes both the
# staleness and the occasional cold-start glitch.

# Silence at the edges of each Kokoro chunk causes word-level gaps when
# chunks are concatenated. Trim anything below this amplitude threshold.
_SILENCE_THRESHOLD = 0.004
# Short crossfade (samples at device rate) to smooth chunk boundaries.
_CROSSFADE_SAMPLES = 256


def _device_rate() -> int:
    return int(sd.query_devices(kind="output")["default_samplerate"])


def _resample(audio: np.ndarray, device_rate: int) -> np.ndarray:
    g = gcd(settings.SAMPLE_RATE_OUT, device_rate)
    up, down = device_rate // g, settings.SAMPLE_RATE_OUT // g
    if up == down:
        return audio
    return resample_poly(audio, up, down).astype(np.float32)


def _pitch_shift(audio: np.ndarray, pitch: float) -> np.ndarray:
    """Shift pitch by resampling (pitch > 1.0 = higher). Small shifts only (±5%).

    Resampling couples pitch and tempo; callers compensate tempo via the
    Kokoro speed parameter so the net pace stays natural.
    """
    if abs(pitch - 1.0) < 0.005:
        return audio
    down = max(1, int(round(1000 * pitch)))
    return resample_poly(audio, 1000, down).astype(np.float32)


def _trim_silence(audio: np.ndarray) -> np.ndarray:
    """Remove leading/trailing near-silence from a Kokoro chunk."""
    mask = np.abs(audio) > _SILENCE_THRESHOLD
    if not mask.any():
        return audio
    start = int(np.argmax(mask))
    end   = len(mask) - int(np.argmax(mask[::-1]))
    return audio[start:end]


def _crossfade_join(chunks: list[np.ndarray]) -> np.ndarray:
    """Join chunks with a short linear crossfade to prevent click artifacts."""
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    if len(chunks) == 1:
        return chunks[0]

    n = _CROSSFADE_SAMPLES
    parts = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            parts.append(chunk)
        else:
            prev = parts[-1]
            if len(prev) < n or len(chunk) < n:
                parts.append(chunk)
                continue
            # Blend: last n samples of prev + first n samples of chunk
            fade_out = np.linspace(1.0, 0.0, n, dtype=np.float32)
            fade_in  = np.linspace(0.0, 1.0, n, dtype=np.float32)
            overlap  = prev[-n:] * fade_out + chunk[:n] * fade_in
            parts[-1] = np.concatenate([prev[:-n], overlap])
            parts.append(chunk[n:])

    return np.concatenate(parts)


class KokoroTTS:
    def __init__(self):
        from kokoro import KPipeline
        self._pipe = KPipeline(lang_code=settings.TTS_LANG_CODE)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        sd.stop()

    def _listen_for_interrupt(self, duration_s: float) -> bool:
        """Sample the mic for `duration_s` seconds and return True if a
        sustained, deliberate voice was heard.

        Only ever called while the speaker is SILENT (the real gap between
        sentences) — never during active playback. Sampling the mic while
        her own voice is coming out of the speaker risks her own audio
        bleeding into the mic and self-triggering an interrupt; that got
        worse after the interrupt threshold was lowered for responsiveness.
        Checking only in a genuinely silent window removes that risk instead
        of trading it off against sensitivity — the gap approach can afford
        a lower, more responsive threshold precisely because there's nothing
        for it to falsely trigger on.
        """
        if not getattr(settings, "INTERRUPT_ENABLED", False) or duration_s <= 0:
            return False
        try:
            if not hasattr(self, "_input_rate"):
                self._input_rate = int(sd.query_devices(kind="input")["default_samplerate"])
            native_rate = self._input_rate
            frame   = native_rate * 20 // 1000
            n_frames = max(1, int(duration_s * 1000) // 20)
            needed   = max(1, settings.INTERRUPT_MS // 20)
            consec = 0
            with sd.RawInputStream(
                samplerate=native_rate, channels=1,
                dtype="int16", blocksize=frame,
            ) as stream:
                for _ in range(n_frames):
                    if self._stop.is_set():
                        return False
                    buf, _ = stream.read(frame)
                    amp = np.abs(np.frombuffer(bytes(buf), dtype=np.int16)).max()
                    if amp > settings.INTERRUPT_THRESHOLD:
                        consec += 1
                        if consec >= needed:
                            print("[TTS] interrupted by voice — stopping")
                            return True
                    else:
                        consec = 0
        except Exception:
            pass
        return False

    def speak(self, text: str, speed: float | None = None, pitch: float = 1.0,
              on_play_start=None) -> bool:
        """Synthesize and play text sentence-by-sentence with real prosody.

        Each sentence is synthesized and played individually so it can carry
        its own small pitch/speed nudge from its own punctuation (a question
        lifts at the end, an exclamation quickens and brightens). Between
        sentences — while the speaker is genuinely silent — the mic is
        sampled for a firm, sustained interrupt; this is what lets Ashish
        talk over her for a follow-up without her own voice ever being able
        to falsely trigger it (see _listen_for_interrupt). Within a
        sentence, Kokoro's own chunks are tightly crossfaded for smooth
        word-to-word flow. Returns True if the user interrupted playback.

        `on_play_start`, if given, fires the instant the FIRST sentence's
        real audio starts — i.e. after Kokoro synthesis finishes, not when
        speak() is first called (synthesis takes real wall-clock time; a
        caller that flips a "speaking" UI cue at speak()'s entry shows the
        cue before any sound plays, which reads as a lag).
        """
        text = (text or "").strip()
        if not text:
            return False
        self._stop.clear()
        base_speed = speed if speed is not None else settings.TTS_SPEED
        sentences = [s.strip() for s in _SENTENCE_END.split(text) if s.strip()] or [text]
        # Long enough to reliably catch a sustained interrupt (needs
        # INTERRUPT_MS worth of consecutive loud frames), plus a small margin.
        gap_s = settings.INTERRUPT_MS / 1000 + 0.10

        played_any = False
        for i, sent in enumerate(sentences):
            eff_pitch = pitch
            speed_mul = 1.0
            if sent.endswith("?"):
                eff_pitch *= 1.03          # curious lift at the end of a question
            elif sent.endswith("!"):
                eff_pitch *= 1.03          # brighter,
                speed_mul = 1.05           # and a touch quicker — genuine emphasis
            # Pitch shift also compresses tempo — pre-compensate so the net
            # pace still matches the requested speed.
            eff_speed = (base_speed / eff_pitch) * speed_mul

            chunks: list[np.ndarray] = []
            for _, _, audio in self._pipe(sent, voice=settings.TTS_VOICE, speed=eff_speed):
                if self._stop.is_set():
                    return False
                arr = _trim_silence(np.asarray(audio, dtype=np.float32))
                if arr.size:
                    chunks.append(_pitch_shift(arr, eff_pitch))
            if not chunks or self._stop.is_set():
                continue   # this one sentence failed/was empty — keep going, don't abort the reply

            sentence_audio = _crossfade_join(chunks)
            if not self._play_with_recovery(sentence_audio):
                continue   # both attempts failed for this sentence — try the next one anyway

            if not played_any and on_play_start is not None:
                try:
                    on_play_start()
                except Exception:
                    pass
            played_any = True

            sd.wait()   # block until THIS sentence finishes — speaker is silent again after
            if self._stop.is_set():
                return False

            is_last = (i == len(sentences) - 1)
            if not is_last and self._listen_for_interrupt(gap_s):
                return True

        return False

    def _play_with_recovery(self, combined_native: np.ndarray) -> bool:
        """Resample-to-device-rate + sd.play(), with one retry for the macOS
        cold-start glitch (CoreAudio HAL not fully warmed up yet).

        Deliberately does NOT call sd._terminate()/_initialize() — that resets
        PortAudio for the whole process, including any mic-capture stream
        running concurrently on another thread (VAD listen, the barge-in/
        interrupt monitor). Tearing down shared audio state from here could
        break or crash an unrelated thread. A short pause + a fresh device
        query is enough to ride out the transient and is thread-safe.
        """
        import time
        for attempt in (1, 2):
            try:
                device_rate = _device_rate()
                sd.play(_resample(combined_native, device_rate), device_rate)
                return True
            except Exception as e:
                if attempt == 2:
                    print(f"[TTS] playback failed twice ({e}) — skipping this reply's audio, staying online")
                    return False
                print(f"[TTS] playback failed ({e}) — retrying once")
                time.sleep(0.2)
        return False


# quick standalone test:  python -m engine.tts
if __name__ == "__main__":
    tts = KokoroTTS()
    tts.speak("You landed the 400-car deal? That's insane — nice work. How are you planning to deliver them all?")
