"""Ollama local LLM — streaming reply, kept warm.

Uses the Ollama REST API directly (via requests) so that `think: false` is
always sent at the API level — the SDK's think= param isn't reliable across
versions. requests is already a dependency so this adds nothing new.
"""
import json
import requests
import settings


def _load_system_prompt() -> str:
    p = settings.BASE_DIR / "core" / "prompt.txt"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ("You are Curie, a concise local voice assistant. "
                "Answer briefly and plainly, in a natural spoken style.")


def _chunk_content(chunk: dict) -> str:
    return (chunk.get("message") or {}).get("content") or ""


class LLM:
    def __init__(self):
        self._url    = settings.OLLAMA_HOST.rstrip("/") + "/api/chat"
        self._system = _load_system_prompt()

    def _payload(self, messages: list[dict], stream: bool, max_tokens: int | None = None) -> dict:
        if max_tokens is None:
            max_tokens = settings.LLM_MAX_TOKENS   # long enough for full news summaries
        return {
            "model":      settings.LLM_MODEL,
            "messages":   messages,
            "stream":     stream,
            "keep_alive": settings.LLM_KEEP_ALIVE,
            "think":      False,   # disable qwen3 thinking mode (harmless on other models)
            "options": {
                "num_predict": max_tokens,
                "num_ctx":     settings.LLM_NUM_CTX,
                "temperature": 0.75,
            },
        }

    def complete(self, prompt: str, system: str = "") -> str:
        """Non-streaming single response (used for fact-mining)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        r = requests.post(self._url, json=self._payload(messages, stream=False, max_tokens=120), timeout=60)
        r.raise_for_status()
        txt = (r.json().get("message") or {}).get("content") or ""
        if "</think>" in txt:
            txt = txt.split("</think>")[-1]
        return txt.strip()

    def stream_reply(self, user_text: str, context: str = ""):
        """Yields reply text chunks. <think> blocks stripped as a safety net."""
        system = self._system + (f"\n\n{context}" if context else "")
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_text},
        ]

        r = requests.post(
            self._url, json=self._payload(messages, stream=True),
            stream=True, timeout=120,
        )
        r.raise_for_status()

        in_think = False
        for line in r.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            piece = _chunk_content(chunk)
            if not piece:
                continue
            if "<think>" in piece:
                in_think = True
                piece = piece.split("<think>")[0]
            if "</think>" in piece:
                in_think = False
                piece = piece.split("</think>")[-1]
            if in_think:
                continue
            if piece:
                yield piece


# standalone test:  python -m engine.llm
if __name__ == "__main__":
    llm = LLM()
    print(f"[model: {settings.LLM_MODEL}]  Reply (no thinking):\n")
    for tok in llm.stream_reply("In one sentence, who are you?"):
        print(tok, end="", flush=True)
    print()
