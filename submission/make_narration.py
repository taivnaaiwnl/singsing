"""Generate the demo-video narration with ElevenLabs text-to-speech.

    python submission/make_narration.py            # writes submission/video/narration_*.mp3

Reads ELEVENLABS_API_KEY from .env (never committed). Produces one MP3 per segment
(for aligning with the screen recording) and one full-length track.
"""

from __future__ import annotations

import json
import os
import sys
import time
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

MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
PREFERRED_VOICES = ["Daniel", "Brian", "George", "Sarah", "Rachel", "Adam"]

# Segment timings follow submission/video_script.md.
SEGMENTS = [
    ("01_intro", "Kervan Credit Passport. In Mongolia, most small businesses keep their books in a notebook, a bank account, and a Q R payment app. Banks can't underwrite that, so they don't lend. Kervan turns those informal records into a verified, explainable credit passport."),
    ("02_request", "The owner fills in what they declare: revenue, years in business, the amount, and the tenor. Then they drop their records: a photo of the handwritten ledger in Mongolian, the bank statement, and a payment export. Nothing is typed from the documents."),
    ("03_pipeline", "Now the agent runs live. Read: Gemini reads each document with structured output and returns every figure with a page or row pointer. Verify: Python re-sums the C S V files and checks continuity and ratios, and Gemini adds qualitative checks. Notice the warning: declared revenue is forty-five percent above the bank inflows. Score: a deterministic scorecard, with no language model. That is our core principle. Explain and Write: Gemini turns the result into words."),
    ("04_passport", "Here is the passport. Grade K3, with points, a P D estimate, and a rate band. Each factor bar shows its verification status: solid is verified, dashed is self-declared and counts half, hatched is inconsistent and counts zero. The improvement plan shows what the owner can do, and every expected gain was computed by re-running the scorecard, not guessed by the model. Switch to Mongolian for the owner's explanation."),
    ("05_bank", "The bank sees the passport anonymized: sector, request, and verified numbers only. Gemini drafted the credit memo: cash-flow analysis, risks, mitigants, the scorecard table, and an evidence appendix. The bank makes an offer. The business accepts, and only then is the identity revealed."),
    ("06_trust", "Every claim is auditable. The step trace shows prompt versions, model, latency, and schema validation. The evidence trail links every figure to its document and row, with a confidence. Guardrails: the model cannot change the score, invented numbers are blocked, humans approve edge cases, and no protected attributes are used."),
    ("07_outro", "Under the hood: Fast A P I on Cloud Run, Gemini through the google-genai SDK with structured output, Secret Manager for keys, and Cloud Build for deploys. Same pipeline, a new scorecard config and language notes, and it scales across Central Asia. Thank you."),
]


def api(path: str, data: dict | None = None, raw: bool = False):
    req = urllib.request.Request(
        "https://api.elevenlabs.io" + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers={"xi-api-key": API_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg" if raw else "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
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


def tts(voice_id: str, text: str) -> bytes:
    return api(
        f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
        {"text": text, "model_id": MODEL, "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.2, "speed": 1.0}},
        raw=True,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    name, voice_id = pick_voice()
    print(f"voice: {name} ({voice_id}) model: {MODEL}")
    total_bytes = 0
    parts: list[bytes] = []
    for key, text in SEGMENTS:
        audio = tts(voice_id, text)
        (OUT / f"narration_{key}.mp3").write_bytes(audio)
        parts.append(audio)
        total_bytes += len(audio)
        print(f"  {key}: {len(audio) / 1024:.0f} KB ≈ {len(audio) / 16000:.0f} s")
        time.sleep(0.5)
    full = tts(voice_id, " ".join(t for _, t in SEGMENTS))
    (OUT / "narration_full.mp3").write_bytes(full)
    print(f"full track: {len(full) / 1024:.0f} KB ≈ {len(full) / 16000:.0f} s (128 kbps estimate)")
    (OUT / "narration_segments.json").write_text(json.dumps([{"file": f"narration_{k}.mp3", "text": t} for k, t in SEGMENTS], indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
