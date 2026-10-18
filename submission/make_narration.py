"""Generate the demo-video narration with ElevenLabs text-to-speech.

    python submission/make_narration.py            # writes submission/video/narration_*.mp3

Reads ELEVENLABS_API_KEY from .env (never committed). Produces one MP3 per segment
(for aligning with the screen recording) and one full-length track. Picks the newest
text-to-speech model on the account (v4 if available, then v3, then multilingual v2);
override with ELEVENLABS_MODEL.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "submission" / "video"
sys.path.insert(0, str(ROOT))
from dev import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not API_KEY:
    raise SystemExit("ELEVENLABS_API_KEY missing in .env")

PREFERRED_VOICES = ["Sarah", "Matilda", "Alice", "Charlotte", "Lily", "Jessica", "Laura", "Rachel"]

# Conversational script. Segment timings drive submission/video/make_video.py.
SEGMENTS = [
    ("01_intro", "Hi, we're team TL from Mongolia. This is Kervan Credit Passport, an AI underwriting agent for small businesses that keep their books the informal way. Let me show you why we built it, and how it works."),
    ("01b_why", "So, why this? Picture a cashmere workshop in Erdenet. Four years in business, loyal customers, busy every winter. But the books live in a notebook, a bank app, and a Q R wallet. When the owner asks for a loan, the bank can't verify any of it. So the answer is usually no, or a rate that makes no sense. It takes weeks of paperwork, and nobody explains the decision. That's the gap. Kervan turns those everyday records into a credit passport, in minutes, with every number traced back to its source."),
    ("02_request", "Here's how it works. The owner tells us what they declare: their revenue, how long they've been in business, the amount, and the term. Then they drop in their records: a photo of the handwritten ledger, in Mongolian, the bank statement, and a payment export. Nothing gets typed from the documents."),
    ("03_pipeline", "Now the agent runs, live. First, Read: Gemini reads each document and returns every figure with a page or row pointer. Then Verify: Python re-adds the numbers and checks continuity and ratios, and Gemini adds its own checks. Look at that warning: the declared revenue is forty-five percent above what the bank statement shows. Then Score. This is the part we care about most: a deterministic scorecard, no language model involved. And finally Explain and Write, where Gemini turns the result into words."),
    ("04_passport", "And here's the passport. Grade K3, with the points, an estimated probability of default, and a rate band. Every factor bar shows how well it was verified: solid means verified, dashed means self-declared, so it only counts half, and hatched means inconsistent, which counts zero. Below, the improvement plan tells the owner what would actually move the grade, and each gain was computed by re-running the scorecard, not guessed. And yes, the owner can read all of it in Mongolian."),
    ("05_bank", "On the bank side, the passport arrives anonymised: just the sector, the request, and the verified numbers. Gemini has already drafted the credit memo: cash-flow analysis, risks, mitigants, the scorecard table, and an evidence appendix. The bank makes an offer. The business accepts, and only then is the identity revealed."),
    ("06_trust", "Everything here is auditable. The step trace shows the prompt versions, the model, latency, and schema validation. The evidence trail links every figure to its document and row, with a confidence. And the guardrails are simple: the model can't change the score, invented numbers get blocked, people approve the edge cases, and no protected attributes are ever used."),
    ("07_outro", "Under the hood, it's Fast A P I on Cloud Run, Gemini through the google-genai SDK with structured output, Secret Manager, and Cloud Build. Same pipeline, a new scorecard and language notes, and it travels across Central Asia. Thanks for watching."),
]


def api(path: str, data: dict | None = None, raw: bool = False):
    req = urllib.request.Request(
        "https://api.elevenlabs.io" + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers={"xi-api-key": API_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg" if raw else "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        body = resp.read()
    return body if raw else json.loads(body.decode("utf-8"))


def pick_voice() -> tuple[str, str]:
    voices = api("/v1/voices").get("voices", [])
    by_name = {v["name"].split(" - ")[0].strip(): v["voice_id"] for v in voices}
    for name in PREFERRED_VOICES:
        if name in by_name:
            return name, by_name[name]
    v = voices[0]
    return v["name"], v["voice_id"]


def list_tts_models() -> list[str]:
    try:
        models = api("/v1/models")
    except Exception as exc:  # the models endpoint is optional
        print("  could not list models:", exc)
        return []
    ids = [m.get("model_id", "") for m in models if m.get("can_do_text_to_speech", True)]
    print("  text-to-speech models on this account:", ", ".join(ids))
    return ids


def pick_model(voice_id: str) -> str:
    override = os.getenv("ELEVENLABS_MODEL")
    ids = list_tts_models()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    # newest generation first: v4 (full quality before flash/turbo variants), then v3, then multilingual v2
    for gen in ("v4", "v3"):
        full = sorted(i for i in ids if i.startswith(f"eleven_{gen}") and not any(x in i for x in ("flash", "turbo")))
        fast = sorted(i for i in ids if i.startswith(f"eleven_{gen}") and any(x in i for x in ("flash", "turbo")))
        candidates += list(reversed(full)) + list(reversed(fast))
    candidates += [f"eleven_{g}" for g in ("v4", "v3") if f"eleven_{g}" not in candidates]
    candidates.append("eleven_multilingual_v2")
    for model in candidates:
        try:
            tts(voice_id, "Hello.", model)
            return model
        except urllib.error.HTTPError as exc:
            print(f"  model {model} unavailable ({exc.code}); trying next")
    raise SystemExit("no usable ElevenLabs model")


def tts(voice_id: str, text: str, model: str) -> bytes:
    if model.startswith(("eleven_v3", "eleven_v4")):
        settings = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.4, "use_speaker_boost": True}
    else:
        settings = {"stability": 0.38, "similarity_boost": 0.75, "style": 0.45, "use_speaker_boost": True, "speed": 0.97}
    return api(f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128", {"text": text, "model_id": model, "voice_settings": settings}, raw=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    name, voice_id = pick_voice()
    model = pick_model(voice_id)
    print(f"voice: {name} ({voice_id}) model: {model}")
    for key, text in SEGMENTS:
        audio = tts(voice_id, text, model)
        (OUT / f"narration_{key}.mp3").write_bytes(audio)
        print(f"  {key}: {len(audio) / 1024:.0f} KB ≈ {len(audio) / 16000:.0f} s")
        time.sleep(0.4)
    full = tts(voice_id, " ".join(t for _, t in SEGMENTS), model)
    (OUT / "narration_full.mp3").write_bytes(full)
    print(f"full track: {len(full) / 1024:.0f} KB ≈ {len(full) / 16000:.0f} s (128 kbps estimate)")
    (OUT / "narration_segments.json").write_text(json.dumps([{"file": f"narration_{k}.mp3", "text": t, "model": model, "voice": name} for k, t in SEGMENTS], indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
