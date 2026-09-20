"""Small durable quick-facts about the user, injected into every prompt.

Stored as data/profile.json — human-readable, no deps. Keep it tiny (it rides
in every prompt, so it's also a latency lever).
"""
import json
import threading

import settings

_PATH = settings.DATA_DIR / "profile.json"
_LOCK = threading.Lock()


class Profile:
    def __init__(self):
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict:
        if not _PATH.exists():
            return {}
        try:
            d = json.loads(_PATH.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        with _LOCK:
            _PATH.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def set(self, key: str, value: str) -> None:
        key, value = key.strip(), str(value).strip()
        if not key or not value:
            return
        if self._data.get(key) != value:
            self._data[key] = value
            self._save()

    def get(self, key: str) -> str:
        return self._data.get(key, "")

    def remove(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self._save()

    def all(self) -> dict:
        return dict(self._data)

    def as_prompt_block(self) -> str:
        """Compact context line for the system prompt, or '' if empty."""
        if not self._data:
            return ""
        facts = "; ".join(f"{k}: {v}" for k, v in self._data.items())
        return f"Known facts about the user: {facts}."


# standalone test:  python -m engine.memory.profile
if __name__ == "__main__":
    p = Profile()
    p.set("name", "Ashish")
    p.set("city", "Delhi")
    print(p.as_prompt_block())
