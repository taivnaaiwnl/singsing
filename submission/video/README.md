# Demo video

`kervan_demo.mp4` (3:19, 1080p) is generated automatically by `make_video.py`: Playwright records the live app at http://localhost:8080 (typed form, file upload, cold Gemini pipeline, passport, bank offer and acceptance, agent trust) between a title and an architecture card, and the ElevenLabs narration segments are aligned to the recorded timestamps and muxed with ffmpeg. Re-run with `python submission/video/make_video.py` after `python submission/make_narration.py` if the words change. The manual recording notes below remain for a hand-recorded alternative.

Narration is pre-generated with ElevenLabs (`narration_full.mp3`, plus one file per segment). Record the screen while the narration plays, or record silently and lay the audio over it in the editor. Target length 3:20–3:45 (hard cap 4:00).

## Timing plan

| Segment | File | Narration | Screen | Extra time on screen |
|---|---|---|---|---|
| 1 Intro | narration_01_intro.mp3 | ~21 s | Title slide from the deck, then the app header | 0 s |
| 2 Loan request | narration_02_request.mp3 | ~18 s | Fill the form, drop the four files (prepare them in a folder) | +5 s while dropping files |
| 3 Pipeline | narration_03_pipeline.mp3 | ~41 s | Click Run assessment; let the steps complete live | +20–30 s of real pipeline time (keep it, it proves the demo is live) |
| 4 Passport | narration_04_passport.mp3 | ~32 s | Scroll the passport: grade, bars, flag, plan; click Монгол | +5 s |
| 5 Bank | narration_05_bank.mp3 | ~23 s | Bank tab: list, memo, make offer; Business tab: accept | +10 s |
| 6 Trust | narration_06_trust.mp3 | ~25 s | Agent trust tab: guardrails, trace, evidence trail | +5 s |
| 7 Outro | narration_07_outro.mp3 | ~24 s | Architecture slide from the deck | 0 s |

Narration alone ≈ 3:00 (voice: Daniel, ElevenLabs multilingual v2); with the on-screen pauses above the video lands at ≈ 3:35–3:50, so keep the pauses short.

## Fastest workflow (Windows, no extra software)

1. Open the app at full-screen width (1920×1080), reset data so the Bank view starts clean, and keep the four files for the test business in an open Explorer window.
2. Press **Win + G** (Xbox Game Bar) → Capture → Record. Narrate nothing yourself; just follow the timing plan and leave the pauses.
3. Stop recording, open the clip in **Clipchamp** (preinstalled on Windows 11), drop `narration_full.mp3` on the audio track, and nudge the clip so each segment lines up with the matching screen (split the full track at the segment boundaries if needed, or use the per-segment files).
4. Export 1080p, upload to YouTube as **Unlisted** (or Google Drive with link sharing), and put the link on slide 13 and in the submission form.

## If you need to change the words

Edit `SEGMENTS` in `submission/make_narration.py` and re-run it; the key is read from `.env`.
