"""Check + test the cloud AI key for Curie's dev mode.

Run after adding a key to .env:
    .venv-mac/bin/python setup_dev_ai.py

It reports which provider Curie will use and makes one tiny (cheap) test call
to prove the key actually works before you try a full website build.
"""
import sys

import settings  # loads .env into the environment  # noqa: F401
from actions.dev_builder import detect_provider, provider_label, _OPENAI_MODELS


def main() -> int:
    provider, key = detect_provider()
    print(f"Provider Curie will use : {provider_label(provider)} ({provider})")

    if provider == "local":
        print("\n❌ No cloud key found. Open the .env file in this folder and add ONE of:")
        print("   OPENAI_API_KEY=sk-...        (platform.openai.com/api-keys)")
        print("   ANTHROPIC_API_KEY=sk-ant-... (console.anthropic.com/settings/keys)")
        print("   GEMINI_API_KEY=...           (aistudio.google.com/apikey — free tier)")
        print("   (remove the leading '#' from the line, no spaces around '=')")
        return 1

    print("Testing the key with a tiny request …")
    try:
        if provider == "openai":
            import requests
            last = None
            for model in _OPENAI_MODELS:
                r = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "max_completion_tokens": 10,
                          "messages": [{"role": "user", "content": "Say OK"}]},
                    timeout=60,
                )
                if r.status_code in (400, 404) and "model" in r.text.lower():
                    print(f"   (model {model} not on this account — trying next)")
                    last = r
                    continue
                r.raise_for_status()
                print(f"✅ Key works! Model available: {model}")
                return 0
            print(f"❌ No configured OpenAI model available: {last.text[:200] if last is not None else ''}")
            return 1

        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            client.messages.create(model="claude-opus-4-8", max_tokens=10,
                                   messages=[{"role": "user", "content": "Say OK"}])
            print("✅ Key works! Model available: claude-opus-4-8")
            return 0

        if provider == "gemini":
            import requests
            from actions.dev_builder import _GEMINI_MODELS
            last = None
            for model in _GEMINI_MODELS:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": key},
                    json={"contents": [{"parts": [{"text": "Say OK"}]}]},
                    timeout=60,
                )
                if r.status_code in (404, 429):
                    print(f"   (model {model}: {'no free quota' if r.status_code == 429 else 'not found'} — trying next)")
                    last = r
                    continue
                r.raise_for_status()
                print(f"✅ Key works! Model available: {model}")
                return 0
            print(f"❌ No configured Gemini model available: {last.text[:200] if last is not None else ''}")
            return 1

    except Exception as e:
        print(f"❌ Key found but the test call FAILED: {str(e)[:300]}")
        print("   Common causes: no billing set up (OpenAI/Anthropic), key pasted with a")
        print("   typo or extra space, or the key was revoked. Fix and rerun this script.")
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
