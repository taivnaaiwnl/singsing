"""Record the Kervan demo video with ElevenLabs narration.

    python submission/video/make_video.py        # writes submission/video/kervan_demo.mp4

Needs: the app running live at http://localhost:8080, the narration MP3s from
make_narration.py, Playwright with Chromium, and ffmpeg (imageio-ffmpeg).

The browser session is recorded with Playwright; each narration segment is
started on screen at a recorded timestamp and the audio track is assembled so
every segment begins exactly where its screen action began.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import wave
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VID = ROOT / "submission" / "video"
FIX = ROOT / "fixtures" / "personas" / "persona_a"
BASE = "http://localhost:8080"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
OUT_MP4 = VID / "kervan_demo.mp4"
SEGS = ["01_intro", "02_request", "03_pipeline", "04_passport", "05_bank", "06_trust", "07_outro"]
W, H = 1920, 1080

CURSOR_JS = """
(() => {
  const c = document.createElement('div');
  Object.assign(c.style, {position:'fixed', left:'-100px', top:'-100px', width:'22px', height:'22px', borderRadius:'50%',
    background:'rgba(11,107,133,.85)', boxShadow:'0 0 0 5px rgba(11,107,133,.22)', pointerEvents:'none',
    zIndex:'2147483647', transform:'translate(-50%,-50%)', transition:'transform .1s'});
  const attach = () => document.body && document.body.appendChild(c);
  if (document.body) attach(); else document.addEventListener('DOMContentLoaded', attach);
  window.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
  window.addEventListener('mousedown', () => { c.style.transform = 'translate(-50%,-50%) scale(.6)'; }, true);
  window.addEventListener('mouseup', () => { c.style.transform = 'translate(-50%,-50%)'; }, true);
})();
"""


def duration(path: Path) -> float:
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def pcm(path: Path) -> bytes:
    return subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "2", "-ar", "44100", "pipe:1"], capture_output=True).stdout


class Recorder:
    def __init__(self, page):
        self.page = page
        self.t0 = time.monotonic()
        self.marks: list[tuple[str, float]] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    def begin(self, seg: str) -> float:
        t = self.now()
        self.marks.append((seg, t))
        print(f"  [{t:6.1f}s] {seg}")
        return t

    def hold_until(self, seg_start: float, seconds: float) -> None:
        remaining = seconds - (self.now() - seg_start)
        if remaining > 0:
            self.page.wait_for_timeout(remaining * 1000)

    def at(self, seg_start: float, offset: float) -> None:
        """Wait until `offset` seconds after the segment started."""
        remaining = offset - (self.now() - seg_start)
        if remaining > 0:
            self.page.wait_for_timeout(remaining * 1000)

    # ---- pointer helpers (visible cursor glide) ----
    def glide_click(self, selector: str, steps: int = 28) -> None:
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(cx, cy, steps=steps)
        self.page.wait_for_timeout(120)
        self.page.mouse.click(cx, cy)

    def type_into(self, name: str, text: str) -> None:
        loc = self.page.locator(f'[name="{name}"]')
        self.glide_click(f'[name="{name}"]', steps=14)
        loc.press_sequentially(text, delay=28)

    def smooth_scroll(self, y: int) -> None:
        self.page.evaluate("y => window.scrollTo({top: y, behavior: 'smooth'})", y)

    def scroll_to(self, selector: str, offset: int = -90) -> None:
        self.page.evaluate(
            "([sel, off]) => { const el = document.querySelector(sel); if (el) window.scrollTo({top: window.scrollY + el.getBoundingClientRect().top + off, behavior: 'smooth'}); }",
            [selector, offset],
        )


def record(durs: dict[str, float]) -> tuple[Path, list[tuple[str, float]]]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(VID / "raw"), record_video_size={"width": W, "height": H}, device_scale_factor=1)
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        r = Recorder(page)
        page.mouse.move(W / 2, H / 2)

        # 1 — title card
        s = r.begin("01_intro")
        page.goto((VID / "title.html").as_uri())
        r.hold_until(s, durs["01_intro"] + 0.6)

        # 2 — loan request
        s = r.begin("02_request")
        page.goto(BASE + "/?video=1", wait_until="networkidle")
        page.wait_for_selector("#runBtn")
        r.type_into("business_name", "Erdenet Cashmere Workshop")
        r.type_into("owner_name", "Oyunchimeg Batbold")
        r.type_into("sector", "Cashmere garment workshop")
        r.type_into("location", "Erdenet, Orkhon")
        r.type_into("years_in_business", "4")
        r.type_into("monthly_revenue_mnt", "9000000")
        r.type_into("loan_amount_mnt", "20000000")
        r.type_into("tenor_months", "18")
        r.type_into("purpose", "Buy raw cashmere for the winter production season")
        r.at(s, 11.0)
        r.glide_click("#dropzone", steps=16)
        page.set_input_files("#fileInput", [str(FIX / n) for n in ("sales_ledger.jpg", "bank_statement.csv", "bank_statement.pdf", "payment_export.csv")])
        page.mouse.move(W * 0.25, H * 0.8, steps=20)
        r.hold_until(s, durs["02_request"] + 3.0)

        # 3 — run the live pipeline
        s = r.begin("03_pipeline")
        r.glide_click("#runBtn", steps=22)
        page.mouse.move(W * 0.62, H * 0.45, steps=25)
        try:
            page.wait_for_function("() => /^(done|error)/.test(document.querySelector('#pipelineMeta').textContent)", timeout=170_000)
        except Exception as exc:  # keep recording even if the run is slow
            print("pipeline wait:", exc)
        r.hold_until(s, durs["03_pipeline"] + 1.5)

        # 4 — passport
        s = r.begin("04_passport")
        r.scroll_to("#passportCard", -40)
        r.at(s, 8.0)
        r.scroll_to("#factors", -120)
        r.at(s, 15.0)
        r.scroll_to("#flags", -140)
        r.at(s, 21.0)
        r.scroll_to("#ownerSummary", -200)
        r.at(s, 26.5)
        r.glide_click('#langToggle button[data-lang="mn"]', steps=18)
        r.at(s, 30.0)
        r.scroll_to("#plan", -160)
        r.hold_until(s, durs["04_passport"] + 3.0)

        # 5 — bank
        s = r.begin("05_bank")
        r.glide_click('.tab[data-view="bank"]', steps=20)
        page.wait_for_selector("#bankList .item", timeout=20_000)
        r.at(s, 4.0)
        r.glide_click("#ofBank", steps=16)
        page.locator("#ofBank").press_sequentially("Partner Bank", delay=40)
        r.at(s, 8.0)
        r.glide_click("#ofBtn", steps=14)
        r.at(s, 10.5)
        page.evaluate("() => { const m = document.querySelector('.memo'); if (m) { m.scrollIntoView({behavior:'smooth', block:'start'}); setTimeout(() => m.scrollBy({top: 520, behavior:'smooth'}), 1500); } }")
        r.at(s, 16.0)
        r.glide_click('.tab[data-view="business"]', steps=20)
        r.scroll_to("#offersBusiness", -220)
        r.at(s, 18.5)
        r.glide_click("#offersBusiness button.btn-primary", steps=16)
        r.at(s, 21.5)
        r.glide_click('.tab[data-view="bank"]', steps=20)
        r.hold_until(s, durs["05_bank"] + 3.0)

        # 6 — agent trust
        s = r.begin("06_trust")
        r.glide_click('.tab[data-view="trust"]', steps=20)
        page.wait_for_selector("#traceTable tr", timeout=20_000)
        r.at(s, 5.0)
        r.scroll_to("#traceTable", -140)
        r.at(s, 11.0)
        r.scroll_to("#inputsTable", -140)
        r.at(s, 16.0)
        r.scroll_to("#checksTable", -140)
        r.at(s, 21.0)
        r.scroll_to("#evidenceTable", -160)
        r.hold_until(s, durs["06_trust"] + 2.0)

        # 7 — outro
        s = r.begin("07_outro")
        page.goto((VID / "outro.html").as_uri())
        r.hold_until(s, durs["07_outro"] + 1.5)
        total = r.now()
        marks = list(r.marks)
        video = page.video
        ctx.close()
        path = Path(video.path())
        browser.close()
    print(f"recorded {path.name} ({total:.1f}s)")
    return path, marks


def build_audio(marks: list[tuple[str, float]], durs: dict[str, float], video_len: float) -> Path:
    rate, chans, width = 44100, 2, 2
    buf = bytearray()
    for seg, start in marks:
        target = int(start * rate) * chans * width
        if len(buf) < target:
            buf.extend(b"\x00" * (target - len(buf)))
        buf.extend(pcm(VID / f"narration_{seg}.mp3"))
    end = int((video_len + 0.5) * rate) * chans * width
    if len(buf) < end:
        buf.extend(b"\x00" * (end - len(buf)))
    wav = VID / "raw" / "narration_track.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(chans)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return wav


def main() -> None:
    (VID / "raw").mkdir(exist_ok=True)
    durs = {s: duration(VID / f"narration_{s}.mp3") for s in SEGS}
    print("narration:", {k: round(v, 1) for k, v in durs.items()})
    video, marks = record(durs)
    vlen = duration(video)
    wav = build_audio(marks, durs, vlen)
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(video), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
           "-c:a", "aac", "-b:a", "160k", "-shortest", str(OUT_MP4)]
    subprocess.run(cmd, check=True)
    (VID / "raw" / "marks.json").write_text(json.dumps(marks, indent=2), encoding="utf-8")
    print(f"wrote {OUT_MP4} ({duration(OUT_MP4):.1f}s, {OUT_MP4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
