"""webrtcvad mic capture — records one utterance, ends on trailing silence."""
import collections

import numpy as np
import sounddevice as sd
import webrtcvad
from scipy.signal import resample_poly
from math import gcd

import settings

_FRAME_MS      = 20                                         # webrtcvad: 10/20/30 only
_TARGET_RATE   = settings.SAMPLE_RATE_IN                    # 16000 Hz (whisper/webrtcvad)
_CAPTURE_RATE  = int(sd.query_devices(kind="input")["default_samplerate"])  # e.g. 44100
_FRAME_LEN_16k = _TARGET_RATE * _FRAME_MS // 1000          # 320 samples at 16 kHz

# Resample ratio: up/down factors for resample_poly
_GCD  = gcd(_TARGET_RATE, _CAPTURE_RATE)
_UP   = _TARGET_RATE  // _GCD
_DOWN = _CAPTURE_RATE // _GCD
# Capture blocksize that yields exactly _FRAME_LEN_16k samples after resampling
_CAPTURE_BLOCK = _FRAME_LEN_16k * _CAPTURE_RATE // _TARGET_RATE


def _to_16k(raw_bytes: bytes) -> bytes:
    """Convert int16 PCM from _CAPTURE_RATE → 16000 Hz."""
    arr = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    resampled = resample_poly(arr, _UP, _DOWN)
    return resampled.astype(np.int16).tobytes()


class VAD:
    def __init__(self):
        self._vad = webrtcvad.Vad(settings.VAD_AGGRESSIVENESS)

    def listen(self, max_seconds: float | None = None) -> np.ndarray:
        """Block until the user speaks and then stops. Returns float32 @16k."""
        if max_seconds is None:
            max_seconds = float(getattr(settings, "VAD_MAX_SECONDS", 30))
        silence_frames = settings.VAD_SILENCE_MS // _FRAME_MS
        max_frames     = int(max_seconds * 1000 / _FRAME_MS)
        ring           = collections.deque(maxlen=8)   # pre-speech padding
        voiced         = []
        triggered      = False
        num_silent     = 0

        with sd.RawInputStream(
            samplerate=_CAPTURE_RATE, channels=1,
            dtype="int16", blocksize=_CAPTURE_BLOCK,
        ) as stream:
            for _ in range(max_frames):
                buf, _ = stream.read(_CAPTURE_BLOCK)
                frame = _to_16k(bytes(buf))           # resample → 16 kHz
                is_speech = self._vad.is_speech(frame, _TARGET_RATE)

                if not triggered:
                    ring.append(frame)
                    if is_speech:
                        triggered = True
                        voiced.extend(ring)
                        ring.clear()
                else:
                    voiced.append(frame)
                    num_silent = num_silent + 1 if not is_speech else 0
                    if num_silent >= silence_frames:
                        break

        if not voiced:
            return np.zeros(0, dtype=np.float32)
        pcm = np.frombuffer(b"".join(voiced), dtype=np.int16)
        return (pcm.astype(np.float32) / 32768.0)


# standalone test:  python -m engine.vad
if __name__ == "__main__":
    print("Speak — recording stops when you go quiet...")
    audio = VAD().listen()
    print(f"Captured {len(audio)/settings.SAMPLE_RATE_IN:.1f}s of audio.")
