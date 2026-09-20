"""Dev mode — Curie builds real software using a strong AI model.

"Hey Curie, create a website called casamotori.sales" →
  1. Create a project folder on the Desktop
  2. Generate the full project with a strong model:
       ANTHROPIC_API_KEY set → Claude Opus 4.8 (best; official SDK)
       OPENAI_API_KEY set   → ChatGPT
       GEMINI_API_KEY set   → Gemini
       no key               → local Ollama (honest about lower quality)
  3. Write every generated file to disk
  4. Open the project in VS Code + the site in the browser

Privacy: ONLY the spoken build request is sent to the cloud model — never
memory, conversation history, or any personal context (Category B stays local).
Set keys in .env (ANTHROPIC_API_KEY=sk-ant-...).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

import settings

# Strong-model choices (env-overridable)
_CLAUDE_MODEL  = os.environ.get("DEV_CLAUDE_MODEL", "claude-opus-4-8")
# Try OpenAI's strongest first, fall back if the account doesn't have it
_OPENAI_MODELS = [m.strip() for m in os.environ.get(
    "DEV_OPENAI_MODEL", "gpt-5,gpt-4o").split(",") if m.strip()]
# Strongest first; free-tier keys usually only have quota for flash tiers
_GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "DEV_GEMINI_MODEL",
    "gemini-3.1-pro-preview,gemini-pro-latest,gemini-3.5-flash,gemini-flash-latest",
).split(",") if m.strip()]

# Local 3B model can only make bare-bones pages — refuse dev builds by default.
ALLOW_LOCAL = os.environ.get("DEV_ALLOW_LOCAL", "0").lower() in ("1", "true", "yes")

_DEV_SYSTEM = """You are an expert full-stack developer. Build a COMPLETE, polished, \
production-quality project from the user's request. Requirements:
- A REAL multi-file project, not a single skeleton page: index.html PLUS separate \
styles.css and script.js, and additional pages (about.html, inventory/products, \
contact.html) whenever they fit the business. Every page fully designed and linked.
- Every file complete and working — no placeholders, no TODOs, no lorem ipsum walls. \
Write realistic, brand-appropriate copy, realistic product/service listings, a working \
contact form layout, testimonials, footer with details.
- Fully RESPONSIVE: mobile-first CSS, flexible grids, a working hamburger nav on small \
screens. It must look representable on a phone and a laptop.
- NEVER use generic AI-generated aesthetics: no overused fonts (Inter, Roboto, Arial, \
system fonts), no purple gradients, no cookie-cutter layouts. Use distinctive fonts, \
cohesive colors and themes matched to the brand, and tasteful animations/micro-interactions.
- Keep it self-contained: vanilla HTML/CSS/JS unless the request demands otherwise; \
no build steps, no external CDNs. Use CSS gradients/patterns/SVG for visuals instead \
of image files that don't exist."""

_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["files", "summary"],
    "additionalProperties": False,
}

_JSON_INSTRUCTION = (
    'Reply with ONLY a JSON object: {"files": [{"path": "relative/path", '
    '"content": "full file content"}], "summary": "1-2 spoken sentences about what you built"}'
)


# ── provider detection ─────────────────────────────────────────────────────────

_KEY_ENVS = {"anthropic": "ANTHROPIC_API_KEY",
             "openai": "OPENAI_API_KEY",
             "gemini": "GEMINI_API_KEY"}


def detect_provider() -> tuple[str, str]:
    """Return (provider, api_key). DEV_PROVIDER env forces one; else best available."""
    forced = os.environ.get("DEV_PROVIDER", "").strip().lower()
    if forced in _KEY_ENVS:
        key = os.environ.get(_KEY_ENVS[forced], "").strip()
        if key:
            return forced, key
    for name, env in _KEY_ENVS.items():
        key = os.environ.get(env, "").strip()
        if key:
            return name, key
    return "local", ""


def provider_label(provider: str) -> str:
    return {"anthropic": "Claude", "openai": "ChatGPT",
            "gemini": "Gemini", "local": "my local model"}.get(provider, provider)


# ── generation backends ────────────────────────────────────────────────────────

def _generate_anthropic(spec: str, key: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    with client.messages.stream(
        model=_CLAUDE_MODEL,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": _FILES_SCHEMA}},
        system=_DEV_SYSTEM,
        messages=[{"role": "user", "content": spec}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined this build request.")
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def _generate_openai(spec: str, key: str) -> dict:
    """Try the strongest OpenAI model first; fall back if the account lacks it."""
    last_err: Exception | None = None
    for model in _OPENAI_MODELS:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _DEV_SYSTEM + "\n" + _JSON_INSTRUCTION},
                    {"role": "user", "content": spec},
                ],
            },
            timeout=600,
        )
        if r.status_code in (400, 404) and "model" in r.text.lower():
            last_err = RuntimeError(f"model {model} unavailable on this account")
            print(f"[dev] {last_err} — trying next")
            continue
        r.raise_for_status()
        print(f"[dev] OpenAI model used: {model}")
        return json.loads(r.json()["choices"][0]["message"]["content"])
    raise last_err or RuntimeError("no OpenAI model available")


def _generate_gemini(spec: str, key: str) -> dict:
    """Try Gemini models in order — free-tier keys often lack pro quota (429).

    Each model gets 2 attempts: replies are occasionally truncated (MAX_TOKENS)
    or wrapped in stray text, so we set an explicit output budget and retry once
    before falling to the next model.
    """
    last_err: Exception | None = None
    for model in _GEMINI_MODELS:
        for attempt in range(3):
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": key},
                json={
                    "systemInstruction": {"parts": [{"text": _DEV_SYSTEM + "\n" + _JSON_INSTRUCTION}]},
                    "contents": [{"parts": [{"text": spec}]}],
                    "generationConfig": {"responseMimeType": "application/json",
                                         "maxOutputTokens": 65536},
                },
                timeout=600,
            )
            if r.status_code in (404, 429):
                last_err = RuntimeError(f"{model}: {'no quota (429)' if r.status_code == 429 else 'not found'}")
                print(f"[dev] {last_err} — trying next Gemini model")
                break   # no point retrying the same model
            if r.status_code >= 500:
                # Google-side blip (503 overloaded) — back off and retry same model
                last_err = RuntimeError(f"{model}: server error {r.status_code}")
                print(f"[dev] {last_err} — retrying in 5s ({attempt + 1}/3)")
                time.sleep(5)
                continue
            if r.status_code == 400 and "maxoutputtokens" in r.text.lower():
                # model has a lower output cap — retry without the explicit budget
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": key},
                    json={
                        "systemInstruction": {"parts": [{"text": _DEV_SYSTEM + "\n" + _JSON_INSTRUCTION}]},
                        "contents": [{"parts": [{"text": spec}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                    timeout=600,
                )
            r.raise_for_status()
            try:
                cand = r.json()["candidates"][0]
                finish = cand.get("finishReason", "?")
                text = cand["content"]["parts"][0]["text"]
                print(f"[dev] Gemini {model}: {len(text)} chars, finish={finish}")
                return json.loads(_extract_json(text))
            except Exception as e:
                last_err = e
                body = json.dumps(r.json())[:400]
                print(f"[dev] {model} attempt {attempt + 1}/2 unparseable ({e}); raw: {body}")
                continue   # retry same model once
    raise last_err or RuntimeError("no Gemini model available")


def _generate_local(spec: str) -> dict:
    """Fallback: local Ollama. Honest about quality — small model, simple sites only.

    The 3B model truncates long multi-file JSON, so bias it to ONE compact file
    and retry once on a parse failure.
    """
    local_rules = (
        "\nLOCAL MODEL RULES: produce EXACTLY ONE file, index.html, with all CSS "
        "and JS inline. Keep it compact — under 250 lines. Output only the JSON."
    )
    last_err: Exception | None = None
    for attempt in range(2):
        r = requests.post(
            settings.OLLAMA_HOST.rstrip("/") + "/api/chat",
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _DEV_SYSTEM + "\n" + _JSON_INSTRUCTION + local_rules},
                    {"role": "user", "content": spec},
                ],
                "stream": False,
                "think": False,
                "options": {"num_predict": 7000, "num_ctx": settings.LLM_NUM_CTX,
                            "temperature": 0.4},
            },
            timeout=600,
        )
        r.raise_for_status()
        text = (r.json().get("message") or {}).get("content") or ""
        try:
            return json.loads(_extract_json(text))
        except Exception as e:
            last_err = e
            print(f"[dev] local parse failed (attempt {attempt + 1}/2): {e}")
    raise RuntimeError(f"local model produced unparseable output: {last_err}")


def _extract_json(s: str) -> str:
    """Pull the first complete {...} out of a possibly chatty/fenced reply."""
    s = re.sub(r"```(?:json)?", "", s)   # strip markdown fences
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return s[start:i + 1]
    raise ValueError("model reply contained no JSON object")


# ── project pipeline ───────────────────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ]", "", name).strip().strip(".")
    return name.replace(" ", "-") or "new-project"


def _safe_relpath(root: Path, rel: str) -> Path | None:
    """Resolve a model-supplied relative path; reject escapes outside the project."""
    target = (root / rel.lstrip("/")).resolve()
    return target if target.is_relative_to(root.resolve()) else None


def build_project(name: str, spec: str, log=print) -> tuple[bool, str, Path | None]:
    """Full pipeline: folder → strong-model generation → files → VS Code → browser.

    Returns (ok, spoken_message, project_path).
    """
    provider, key = detect_provider()
    label = provider_label(provider)
    if provider == "local" and not ALLOW_LOCAL:
        return False, (
            "I can't build a proper website without a cloud AI key — my local model "
            "only makes bare skeletons and you deserve better. Add an OpenAI, Anthropic, "
            "or Gemini API key to my dot-env file and ask me again."), None
    folder = _sanitize_name(name)
    root = Path.home() / "Desktop" / folder
    root.mkdir(parents=True, exist_ok=True)
    log(f"[dev] provider={provider} project={root}")

    t0 = time.time()
    try:
        if provider == "anthropic":
            data = _generate_anthropic(spec, key)
        elif provider == "openai":
            data = _generate_openai(spec, key)
        elif provider == "gemini":
            data = _generate_gemini(spec, key)
        else:
            data = _generate_local(spec)
    except Exception as e:
        log(f"[dev] generation failed: {e}")
        return False, (f"I set up the {folder} folder, but the {label} build failed — "
                       f"{str(e)[:120]}. Check the API key in my dot-env file and ask me again."), root

    files = data.get("files") or []
    if not files:
        return False, f"{label} came back with no files — try describing the project differently.", root

    written = 0
    for f in files:
        target = _safe_relpath(root, str(f.get("path", "")).strip())
        if target is None or not f.get("content"):
            log(f"[dev] skipped unsafe/empty file: {f.get('path')!r}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"], encoding="utf-8")
        written += 1
        log(f"[dev] wrote {target.relative_to(root)}")
    if not written:
        return False, f"{label} returned files I couldn't safely write. Nothing was created.", root

    # Open in VS Code
    try:
        subprocess.run(["open", "-a", "Visual Studio Code", str(root)],
                       capture_output=True, timeout=10)
    except Exception as e:
        log(f"[dev] VS Code open failed: {e}")

    # Open the site in the browser if there's an entry point
    index = root / "index.html"
    if index.exists():
        try:
            subprocess.run(["open", str(index)], capture_output=True, timeout=10)
        except Exception:
            pass

    took = int(time.time() - t0)
    summary = (data.get("summary") or "").strip()
    msg = (f"Done — {label} built {folder} with {written} files in about {took} seconds. "
           f"It's open in VS Code" + (" and in your browser. " if index.exists() else ". ")
           + summary)
    return True, msg, root


# standalone test:  python -m actions.dev_builder
if __name__ == "__main__":
    ok, message, path = build_project(
        "test-site", "Create a one-page website for a coffee shop called Beans")
    print(ok, "|", message, "|", path)
