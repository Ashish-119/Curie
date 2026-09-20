# Project Curie — Architecture, Requirements & Execution Plan

**Version:** 1.1 (supersedes 1.0)
**Base project:** Jarvis "Mark XXXIX" SDK
**Deployment target (prototype):** Windows, Ryzen 5, 8 GB RAM, CPU-only
**Future target:** Apple MacBook (M5) — macOS port
**Owner:** (you) — built evenings, ~5 hrs/day, 4-day demo sprint

### What changed since v1.0
- **LLM switched:** Phi-4-mini → **Qwen3 4B** (fallbacks: Phi-4-mini, Llama 3.2 3B). Rationale in §9.
- **Privacy model deepened:** two data categories with distinct guarantees, the cloud-synced-folder leak, local-model-only condition, query hygiene, the exact client-facing claim, and the live-network proof (§3.2, §6.5, §6.8).
- **Latency architecture added:** single-pass router, pre-computed briefing, extractive pre-filtering, CPU knobs, light-vs-heavy budget (§6.7).
- **Ambient lifecycle added:** auto-boot on lid-open via a background service + voice shutdown (§6.9).
- **New §9 (model rationale & upgrade path) and §10 (detailed code-change map).**

---

## 1. Executive Summary

Curie is a **fully local, privacy-first ambient AI assistant** built by transplanting a local brain into the existing Jarvis codebase. It auto-starts when the client opens the laptop, greets them and delivers a personalized briefing, holds natural spoken conversations, remembers everything said (locally, indefinitely), and can act on the machine (open apps, browse whitelisted sites) — while guaranteeing that **no conversation, memory, or content is ever sent to any AI model or third party.** The internet is used only to *pull* the client's own data from a fixed whitelist (email, Slack, approved sites, maps).

The headline engineering challenge is replacing Jarvis's cloud brain (Google Gemini) with a local pipeline — **Ollama (Qwen3 4B) + faster-whisper + Kokoro + MemPalace** — and engineering it to *feel* responsive on modest CPU-only hardware, while running as an always-on background service.

---

## 2. Context & Relationship to Jarvis

Jarvis "Mark XXXIX" is **100% Google Gemini cloud**: `requirements.txt` lists `google-genai`/`google-generativeai` and nothing local; `main.py` opens a Gemini native-audio live session and reads a `gemini_api_key`. Even listening and speaking happen on Google's servers.

**Curie is therefore a brain transplant, not a config change.** The *chassis* carries over (tool/action modules, PyQt UI, Windows OS-control code, the planner/executor pattern). The *engine* (the entire Gemini path) is removed and replaced with a local STT → router/LLM → TTS pipeline plus a local memory system. Exact file-level impact is in §10.

> **License caveat (must verify):** Jarvis Mark XXXIX appears to ship under **CC BY-NC 4.0 (non-commercial)**. Building a *paid* client deliverable on it may violate that license. Resolve before delivery (§13).

---

## 3. Guiding Principles

### 3.1 Local-First — precise definition
- **Absolute rule:** No conversation, memory, or user content is ever fed to an AI model or any third party off-machine. LLM, STT, TTS, and memory all run on the client's machine.
- **Permitted:** Pulling the client's *own* data inward from a fixed whitelist.
- **Honest nuance:** Integration calls themselves *do* leave the box — Gmail/Slack/Teams/Maps APIs each send an authenticated request (token + query) to that provider. That is "pulling," which the rule allows, but it is not zero packets. For literally zero Google traffic, OpenStreetMap can replace the Maps API.
- **Integrity, not intent:** "We don't leak" must become "we *can't* leak, and can prove it" — via an outbound allowlist + disk encryption (§6.8).

### 3.2 Two data categories — and their different guarantees
This distinction is the heart of the privacy story; build and explain it explicitly.

- **Category A — pulled data** (email, Slack, scraped sites, weather, maps): fetching sends *minimal neutral parameters* to the client's own providers (e.g., the city `Delhi` + `forecast` + `tomorrow`). Guarantee: *processed and stored locally; never sent to an AI model or a new third party.*
- **Category B — conversation data** (what the client says, Curie's replies, all memory): **fully local, end-to-end. It never touches the network at all.** This is the strongest claim and the one the client cares about most. Worked proof in §6.5.

The defensible client-facing claim (do **not** say "nothing ever leaves the machine"):
> *"All processing and storage happen locally. No client content is ever sent to any AI model or any third party beyond the client's own connected accounts, and all outbound traffic is restricted to an auditable allowlist that can be inspected or blocked."*

### 3.3 Responsiveness
- **Light tasks** (conversation, weather, a single short page): target ~1.5–4 s to first spoken word.
- **Heavy tasks** (news/world briefing, multi-article summarization): make them *feel* instant by **pre-computing in the background**, not by summarizing on demand (§6.7).
- **Live web search** is the wildcard: ~3–6 s, masked with a spoken filler.
- Achieved by perceived-latency engineering: stream tokens into speech, single-pass routing, tiny prompts, warm models. Not cloud-instant; the gap mostly closes on the M5.

### 3.4 Hardware honesty
8 GB RAM is the binding constraint: one model at a time, small models, short context, defer the Electron UI. The single highest-leverage upgrade is **16 GB RAM** (see §9).

---

## 4. End Goals & Success Criteria

### 4.1 Prototype (4-day demo) — "done" means:
1. **Ambient auto-start + proactive briefing:** opening the laptop (resume) auto-wakes Curie — no terminal, no manual launch — and it greets the client by name and delivers a personalized, business-relevant briefing unprompted.
2. **Memory recall:** "what did I say about X last week?" works from durable local memory, including temporal follow-ups ("five days ago you mentioned…").
3. **Email interaction:** summarize important unread email; read a chosen one aloud.
4. **Emotional female voice:** warm, natural, female TTS — not robotic.
5. **Simultaneous answer + action:** answer verbally *while* opening the relevant whitelisted site/app in parallel.

### 4.2 Acceptance gates (non-negotiable)
- Zero LLM/cloud egress, demonstrable live on a network monitor (a pure conversation triggers **zero** outbound calls).
- Memory persists across restarts; never silently forgets.
- Runs within 8 GB RAM without thrashing during the demo.
- Curie starts on lid-open and stops on voice command — no developer console involved.

### 4.3 Full vision (post-demo)
Slack/Teams + multi-source business-intelligence layer; Maps/navigation; macOS (M5) port with a more expressive voice; glassmorphism orb UI; custom "Curie" wake word.

---

## 5. Target Skills / Capabilities

| # | Capability | Source data | Latency class |
|---|------------|-------------|---------------|
| 1 | Wake-word activation | Mic (local) | instant |
| 2 | Conversational Q&A | Local model | light |
| 3 | Long-term memory (verbatim recall) | MemPalace (local) | light |
| 4 | Temporal/contextual follow-ups | MemPalace + KG | light |
| 5 | Proactive startup briefing | Resume event → integrations | pre-computed |
| 6 | Email summarize / read-aloud | Gmail (read-only) | medium |
| 7 | Slack/Teams summarize | Slack/Graph API (read) | medium |
| 8 | App launching | OS subprocess | light |
| 9 | Browser actions / scraping | Playwright | light–medium |
| 10 | Parallel answer + act | Orchestrator | light |
| 11 | Real-time data (weather, crypto) | Whitelisted web | light |
| 12 | Business intelligence / news filter | Whitelisted scraping | pre-computed |
| 13 | Maps / navigation | Maps API or OSM | medium |
| 14 | Emotional female voice | Kokoro (local) | n/a |

---

## 6. System Architecture

### 6.2 Component breakdown
- **Audio I/O** — `sounddevice` (16 kHz in, 24 kHz out).
- **VAD** — `webrtcvad-wheels`; detects start/stop of speech so STT is ready on the last syllable.
- **STT** — `faster-whisper` (`base`, `int8`), CPU, no torch.
- **Router** — a *single* function-calling LLM pass that decides "request vs conversation" **and** extracts parameters in one go (no separate classifier). Output is either a tool call (JSON) or plain conversational text.
- **LLM core** — **Ollama hosting `qwen3:4b`** (fallbacks `phi4-mini`, `llama3.2:3b`). Streaming on. Local server `127.0.0.1:11434`.
- **Embeddings** — `nomic-embed-text` via Ollama (local), for MemPalace.
- **TTS** — **Kokoro**, voice `af_heart` (warm female, Apache-2.0, CPU-realtime).
- **Wake word** — Porcupine (`pvporcupine`); commercial-license caveat (§8.6).
- **Memory** — §6.6.
- **Orchestrator** — async heart: streaming pipeline, parallel speak+act, tool dispatch (inherits Jarvis's asyncio pattern).
- **Tool/action layer** — reused Jarvis modules, re-pointed to local routing.
- **Integration layer** — pull-only connectors, each summarized by the *local* model.
- **Briefing service** — background job that pre-computes and caches the briefing (§6.7).
- **Platform layer** — `windows.py` (primary), `macos.py` (stub).
- **Lifecycle/service** — NSSM-wrapped Windows service + resume-event hook (§6.9).
- **Privacy/security layer** — §6.8.
- **UI** — Jarvis PyQt face retained but deferred.

### 6.6 Memory architecture
**Engine: MemPalace (MIT, local-first).** Verbatim storage + semantic retrieval with **no LLM and no cloud at recall time**. Structured index (wings = people/projects, rooms = topics, drawers = content) + a temporal knowledge graph in local SQLite (powers "five days ago…"). Backend: ChromaDB. Embeddings: local `nomic-embed-text`.
**Companion: `profile.json`** — small human-readable durable facts injected into every prompt (also a latency lever).
> Install MemPalace **only** from PyPI/official repo. The lookalike domain `mempalace.tech` is flagged by the maintainers as a malware impostor.

### 6.7 Latency strategy
**Levers:** Single-pass router; pre-compute heavy tasks; extractive pre-filter; cache with TTL; stream tokens → sentence TTS; keep models warm (Ollama `keep_alive`); CPU knobs (Q4_K_M, AVX-512, llama.cpp).

### 6.8 Privacy & security layer (enforcement — where the promise becomes real)
1. **Local model only.**
2. **Local STT/TTS.**
3. **Telemetry off.** Disable ChromaDB/Ollama usage telemetry explicitly.
4. **Memory store NOT in a cloud-synced folder.** Keep `data/` outside OneDrive/Dropbox/Google Drive or the verbatim DB auto-uploads. Place it on a BitLocker volume.
5. **Outbound allowlist.** Windows Firewall rules: only whitelisted domains reachable; everything else blocked. *This is the proof.*
6. **Query hygiene.**
7. **Encryption at rest** (BitLocker) + **local audit log** of every outbound call.
8. **Secrets** in a git-ignored `.env`; never committed.

### 6.9 Ambient lifecycle — auto-boot & shutdown
- **Always-on background service** wrapped with NSSM/pywin32.
- **"Boot on lid open" = greet on resume** via the OS power-resume event.
- **Voice shutdown.** "Curie, go to sleep" vs "Curie, shut down" are distinct.

---

## 7. Technology Stack

| Layer | Choice | License |
|-------|--------|---------|
| LLM runtime | Ollama (0.5.13+) | Open |
| LLM model | qwen3:4b (fb: phi4-mini, llama3.2:3b) | Apache-2.0 |
| Embeddings | nomic-embed-text | Open |
| STT | faster-whisper (base) | MIT |
| TTS | Kokoro (af_heart) | Apache-2.0 |
| Memory | MemPalace + JSON profile | MIT |
| VAD | webrtcvad-wheels | BSD/MIT |
| Wake word | Porcupine | Free tier ⚠ |
| Browser | Playwright | Apache-2.0 |
| Service | NSSM / pywin32 | Public/Open |
| UI | PyQt6 | GPL/commercial |

---

## 8. Requirements
(hardware, system software, one-time model pulls, python deps, accounts/keys, licensing matrix — see full text; key line: **"No cloud-LLM keys."**)

## 9. Model selection rationale & upgrade path
Qwen3 4B chosen over Phi-4-mini for tool-use reliability + context window. Real upgrade path is RAM (16GB), not a paid model — cloud model forbidden by the privacy guarantee.

## 10. Code-change map
DELETE the Gemini brain (`main.py`'s genai path, `gemini_api_key`, flat-JSON memory). MODIFY & reuse the chassis (planner/executor, actions/*, ui.py) re-pointed to local routing. NEW: `curie/` package — config, orchestrator, stt/vad/llm/tts/wake/router, memory/palace+profile, integrations (gmail/slack/web/news/maps), platform layer, lifecycle service, security/privacy module + allowlist.

## 11. Execution Plan
- **Day 1** — Brain transplant + single-pass router.
- **Day 2** — Memory (MemPalace + profile.json, telemetry off, data-dir safety check).
- **Day 3** — Integrations + proactive/parallel + briefing pre-compute.
- **Day 4** — Lifecycle (NSSM service, voice shutdown) + harden (BitLocker, outbound allowlist, live-traffic proof, RAM profiling) + rehearse.

## 12. Risks & Mitigations
8GB ceiling; CPU latency; tool-call misfire; cloud-synced data dir; accidental cloud model; library telemetry; Jarvis CC BY-NC license; Porcupine commercial terms; MemPalace impostor domain; demo fragility; payment-in-hardware deal.

## 13. Open Decisions to Confirm
1. Jarvis license — commercial grant or rebuild chassis?
2. Maps — Google API vs offline OSM?
3. Wake word — pay for Porcupine, or openWakeWord?
4. Hardware — can the client's box go to 16 GB?
5. Model — confirm Qwen3 4B (fallbacks Phi-4-mini/Llama 3.2 3B).
6. Voice — Kokoro af_heart for demo; Orpheus/Chatterbox on M5?
7. Slack/Teams — in the 4-day demo or post-demo?
8. Generated-code execution — disable the agent's code-exec fallback for the client build?

*End of document — v1.1. (Full original text retained by the user; this file is Claude's saved reference copy for the project.)*
