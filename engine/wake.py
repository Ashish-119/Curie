"""openWakeWord — free, fully local hands-free trigger. No key, no account.

Listens for the wake word ("hey jarvis") and returns when detected.
"""
import numpy as np
import sounddevice as sd

import settings

_FRAME = 1280   # openWakeWord wants 80 ms @ 16 kHz (1280 samples)


class WakeWord:
    def __init__(self):
        from openwakeword.model import Model
        try:
            self._model = Model(wakeword_models=[settings.WAKE_MODEL])
        except Exception:
            import openwakeword.utils
            openwakeword.utils.download_models()
            self._model = Model(wakeword_models=[settings.WAKE_MODEL])

    def wait(self, stop=None) -> bool:
        """Block until the wake word is heard. Returns False if `stop` is set."""
        self._model.reset()
        with sd.RawInputStream(
            samplerate=settings.SAMPLE_RATE_IN, channels=1,
            dtype="int16", blocksize=_FRAME,
        ) as stream:
            while not (stop and stop.is_set()):
                buf, _ = stream.read(_FRAME)
                pcm = np.frombuffer(bytes(buf), dtype=np.int16)
                scores = self._model.predict(pcm)
                if any(s >= settings.WAKE_THRESHOLD for s in scores.values()):
                    return True
        return False


# standalone test:  python -m engine.wake
if __name__ == "__main__":
    print(f"Say '{settings.WAKE_MODEL.replace('_', ' ')}'...")
    if WakeWord().wait():
        print("Wake word detected!")
