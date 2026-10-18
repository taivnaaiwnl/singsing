"""Fill the hackathon pitch-deck template with Kervan content.

    python submission/build_deck.py            # writes submission/Kervan_TL_pitch_deck.pptx

Keeps the template's slides, headings and branding; adds content text boxes and images
below each heading. Convert to PDF with LibreOffice afterwards (see README in submission/).
"""

from __future__ import annotations

import copy
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "submission" / "template.pptx"
OUT = ROOT / "submission" / "Kervan_TL_pitch_deck.pptx"
ASSETS = ROOT / "submission" / "assets"

INK = RGBColor(0x1C, 0x19, 0x17)
MUTED = RGBColor(0x6B, 0x72, 0x80)
ACCENT = RGBColor(0x0B, 0x6B, 0x85)

LINKS = {
    "github": "https://github.com/taivnaaiwnl/singsing",
    "video": "(demo video link)",
    "product": "(Cloud Run URL)",
}

STORAGE_BULLET = "Storage: JSON in /tmp on the Cloud Run instance for the demo (resets on cold start); Firestore persistence for passports, offers and traces is the planned next step."
CONTENT_TOP = Inches(1.35)
CONTENT_LEFT = Inches(0.35)
CONTENT_WIDTH = Inches(9.3)
CONTENT_HEIGHT = Inches(4.05)


def add_text(slide, left, top, width, height, paragraphs, size=13, color=INK, bold_first=False, line_spacing=1.1):
    """paragraphs: list of str or (str, dict) with keys bold/size/color/bullet/level."""
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    first = True
    for item in paragraphs:
        text, opts = (item, {}) if isinstance(item, str) else item
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        bullet = opts.get("bullet", False)
        p.text = ("•  " if bullet else "") + text
        p.alignment = PP_ALIGN.LEFT
        p.line_spacing = line_spacing
        p.space_after = Pt(opts.get("space_after", 4))
        if opts.get("level"):
            p.level = opts["level"]
        for r in p.runs:
            r.font.size = Pt(opts.get("size", size))
            r.font.bold = opts.get("bold", False)
            r.font.color.rgb = opts.get("color", color)
            r.font.name = "Arial"
    return box


def heading(text):
    return (text, {"bold": True, "size": 14, "color": ACCENT, "space_after": 2})


def bullets(items, size=12):
    return [(t, {"bullet": True, "size": size}) for t in items]


def add_image(slide, name, left, top, width=None, height=None):
    path = ASSETS / name
    if not path.exists():
        add_text(slide, left, top, width or Inches(4), Inches(0.5), [(f"[missing asset {name}]", {"color": MUTED, "size": 10})])
        return None
    return slide.shapes.add_picture(str(path), left, top, width=width, height=height)


def delete_slide(prs, index):
    sld_id_lst = prs.slides._sldIdLst
    slides = list(sld_id_lst)
    rId = slides[index].rId
    prs.part.drop_rel(rId)
    sld_id_lst.remove(slides[index])


def set_heading(slide, text):
    """Replace the template's prompt text in the heading box (the 9.8in-wide box near the top)."""
    for sh in slide.shapes:
        if sh.has_text_frame and sh.width > Inches(9) and sh.top < Inches(1.0) and sh.text_frame.text.strip():
            tf = sh.text_frame
            # keep the first run's formatting
            first_para = tf.paragraphs[0]
            runs = first_para.runs
            if runs:
                runs[0].text = text
                for r in runs[1:]:
                    r.text = ""
            else:
                first_para.text = text
            for extra in tf.paragraphs[1:]:
                for r in extra.runs:
                    r.text = ""
            return sh
    return None


def build() -> Path:
    prs = Presentation(str(TEMPLATE))
    slides = list(prs.slides)

    # ---- 1. Title / team details -------------------------------------------------
    s = slides[0]
    for sh in s.shapes:
        if sh.has_text_frame and "Team Details" in sh.text_frame.text:
            tf = sh.text_frame
            lines = [
                ("Team Details", {"bold": True, "size": 16}),
                ("Team name: TL  ·  Taivanbat Bayarsaikhan (leader), Lkhagvadorj Munkhdalai", {"size": 13}),
                ("AI Engineers & Data Scientists at M bank, Mongolia's first neobank (fully digital, licensed 2022)", {"size": 12}),
                ("Problem Statement: BFSI — Intelligent Risk, Fraud & Financial Experiences. Kervan Credit Passport: an AI underwriting agent that turns a small business's informal records (ledger photos, bank statements, payment exports) into a verified, explainable credit passport a bank can act on. Demo market: Mongolia.", {"size": 11}),
            ]
            for i, (text, opts) in enumerate(lines):
                p = tf.paragraphs[i] if i < len(tf.paragraphs) else tf.add_paragraph()
                if p.runs:
                    p.runs[0].text = text
                    for r in p.runs[1:]:
                        r.text = ""
                else:
                    p.text = text
                for r in p.runs:
                    r.font.size = Pt(opts["size"])
                    r.font.bold = opts.get("bold", False)
            for extra in tf.paragraphs[len(lines):]:
                for r in extra.runs:
                    r.text = ""
            # the template numbers these lines (a., b.); render them as plain lines
            from pptx.oxml.ns import qn
            for p in tf.paragraphs:
                pPr = p._p.get_or_add_pPr()
                for tag in ("a:buAutoNum", "a:buChar", "a:buFont"):
                    for el in pPr.findall(qn(tag)):
                        pPr.remove(el)
                if pPr.find(qn("a:buNone")) is None:
                    pPr.insert(0, pPr.makeelement(qn("a:buNone"), {}))
                pPr.set("marL", "0"); pPr.set("indent", "0")
    add_image(s, "mbank_logo.png", Inches(8.05), Inches(4.78), width=Inches(1.6))

    # ---- 3. Brief about the idea ---------------------------------------------------
    s = slides[2]
    add_text(s, CONTENT_LEFT, CONTENT_TOP, CONTENT_WIDTH, CONTENT_HEIGHT, [
        heading("The problem"),
        *bullets([
            "Most Mongolian micro and small businesses keep their books in a handwritten ledger, a bank account and a QR payment app. Banks cannot underwrite that at scale, so thin-file businesses are refused or overpriced.",
            "Declared figures are unverifiable, manual review is slow, and decisions are opaque to the owner.",
        ]),
        heading("Kervan Credit Passport"),
        *bullets([
            "The owner uploads a ledger photo, a bank statement (PDF/CSV) and a payment export, and declares revenue, tenure, amount and tenor.",
            "In about a minute the agent returns a credit passport (grade K1–K7 with an indicative PD, rate band and suggested limit from Kervan's v0.1 policy scorecard, to be recalibrated on partner-bank data), an owner explanation in English and Mongolian with a re-scored improvement plan, and an underwriter-ready credit memo with an evidence appendix.",
            "Core principle: Gemini reads, verifies, explains and writes; points, penalties and grade come only from a deterministic, auditable scorecard. Gemini never computes a score and can never raise one: at most it tightens an input's verification status, and its flags carry no penalty.",
            "Banks receive anonymized passports and make indicative offers; identity is revealed only when the business accepts, and the bank's own KYC and credit approval follow before anything is disbursed.",
        ]),
    ])

    # ---- 4. Opportunities ----------------------------------------------------------
    s = slides[3]
    add_text(s, CONTENT_LEFT, Inches(2.2), CONTENT_WIDTH, Inches(3.2), [
        heading("How it differs"),
        *bullets([
            "Verification, not extraction: declared vs documented and documents vs each other (ledger vs bank, payments vs bank, totals vs re-summed rows, account holder, tenure vs account age); arithmetic in Python; every figure traced to a page or row.",
            "Explainable by construction: a points scorecard with reason codes; self-declared inputs count half, inconsistent inputs count zero.",
            "Reads the records people actually have: handwritten Mongolian ledger photos, local bank CSV/PDF exports, payment settlements.",
        ], size=10.5),
        heading("How it solves the problem · USP"),
        *bullets([
            "Banks: an underwriter-ready memo and evidence trail in a minute, inconsistencies flagged; every memo is a draft for a human underwriter (K1–K2 need sign-off, K3–K6 are referred, K7 gets no standard offer).",
            "Owners: a plain-language grade in Mongolian and an improvement plan with gains computed by re-running the scorer.",
            "USP: trust by design — number guardrails, no protected attributes, cross-source consistency checks today (authenticity and registration documents on the roadmap), identity disclosure controlled by the business.",
        ], size=10.5),
    ])

    # ---- 5. Features ---------------------------------------------------------------
    s = slides[4]
    add_text(s, CONTENT_LEFT, CONTENT_TOP, Inches(4.6), CONTENT_HEIGHT, [
        heading("Agent pipeline (live, streamed to the UI)"),
        *bullets([
            "READ: Gemini multimodal, structured JSON; monthly figures with document + page/row pointers.",
            "VERIFY: Python re-sums CSVs and checks continuity, volatility, declared-vs-bank, ledger-vs-bank, payments-vs-bank, account age; Gemini adds qualitative checks; the more conservative status wins.",
            "SCORE: deterministic scorecard, no LLM; per-factor points, reason codes, penalties.",
            "EXPLAIN: owner summary in English and Mongolian; improvement plan with re-scored gains.",
            "WRITE: credit memo with cash-flow analysis, risks, mitigants, scorecard table and evidence appendix.",
        ], size=11.5),
    ])
    add_text(s, Inches(5.1), CONTENT_TOP, Inches(4.55), CONTENT_HEIGHT, [
        heading("Product views"),
        *bullets([
            "Business: loan request, uploads, live pipeline, passport card with status-encoded factor bars, flags, EN/MN toggle, improvement plan, offers.",
            "Bank: anonymized passport list, flags, memo, make-offer; identity revealed only when the business accepts.",
            "Agent trust: guardrails, step trace (prompt version, model, latency, schema validation, cache), scorer inputs, verification checks, evidence trail with links to the documents.",
        ], size=11.5),
    ])

    # ---- 6. Process flow -----------------------------------------------------------
    s = slides[5]
    # 16:9 diagram: size by height so it stays above the footer, centred
    add_image(s, "process_flow.png", Inches((10 - 3.95 * 16 / 9) / 2), Inches(1.3), height=Inches(3.95))

    # ---- 7. Wireframes / mock-ups --------------------------------------------------
    s = slides[6]
    add_image(s, "shot_business.png", Inches(0.3), Inches(1.3), width=Inches(4.6))
    add_image(s, "shot_passport.png", Inches(5.1), Inches(1.3), width=Inches(4.6))
    add_text(s, Inches(0.3), Inches(4.95), Inches(9.4), Inches(0.5), [
        ("Left: loan request and live agent pipeline. Right: the credit passport with status-encoded factor bars, flags and the re-scored improvement plan.", {"size": 10, "color": MUTED}),
    ])

    # ---- 8. Architecture -----------------------------------------------------------
    s = slides[7]
    add_image(s, "architecture.png", Inches((10 - 3.5 * 16 / 9) / 2), Inches(1.25), height=Inches(3.5))
    add_text(s, Inches(0.3), Inches(4.85), Inches(9.4), Inches(0.6), [
        ("Browser (static HTML/JS served by FastAPI) → Cloud Run service → Gemini 3.8 Flash via google-genai (structured JSON, one call per document in parallel). Build: Cloud Build from source → Artifact Registry. Secrets: Secret Manager → env at runtime. Progress streams back over Server-Sent Events.", {"size": 9, "color": MUTED}),
    ])

    # ---- 9. Technologies -----------------------------------------------------------
    s = slides[8]
    add_text(s, CONTENT_LEFT, CONTENT_TOP, Inches(4.6), CONTENT_HEIGHT, [
        heading("Google Cloud and AI"),
        *bullets([
            "Gemini 3.8 Flash via the google-genai SDK: JSON output with a Pydantic response_schema on all four model steps; low thinking level for sub-minute runs; one retry feeding the validation error back; per-call timeout; cache keyed by model, prompt version and file hash. The same code runs on Vertex AI with the Cloud Run service account (no API key).",
            "Cloud Run: deploy from source (Cloud Build → Artifact Registry), 1 vCPU / 1 GiB, scales to zero. Secret Manager holds the Gemini key; the runtime service account has only secretAccessor; no secret in the repo or image.",
            STORAGE_BULLET,
            "Access: the demo service is public and holds synthetic data only; Cloud Run IAM or Identity-Aware Proxy gates it before real customer documents.",
        ], size=10.5),
    ])
    add_text(s, Inches(5.1), CONTENT_TOP, Inches(4.55), CONTENT_HEIGHT, [
        heading("Application stack"),
        *bullets([
            "Python 3.12, FastAPI, Pydantic v2; Server-Sent Events for live progress.",
            "Deterministic verification math and a points scorecard in one config file; unit tests pin the behaviour.",
            "Number guardrail: every figure in generated text must exist in the inputs; retry once, then deterministic fallback.",
            "Front-end: one static page, vanilla HTML/CSS/JS.",
            "Prompts versioned as text files with the guardrails inside.",
        ], size=10.5),
    ])

    # ---- 10. Estimated cost --------------------------------------------------------
    s = slides[9]
    add_text(s, CONTENT_LEFT, CONTENT_TOP, CONTENT_WIDTH, CONTENT_HEIGHT, [
        heading("Per assessment (prototype measurements)"),
        *bullets([
            "4 to 7 Gemini Flash calls per assessment (one per document plus verify, explain, write); 60–120k input tokens; about US$0.02–0.05 at Flash list prices. Repeated runs on the same files are served from the cache at zero cost.",
            "Cloud Run with scale-to-zero: compute only while a request runs (~1 vCPU-minute per assessment); within the free tier for pilot volumes.",
        ], size=11),
        heading("Monthly, indicative"),
        *bullets([
            "Pilot (1,000 assessments / month): ≈ US$30–60 Gemini + ≈ US$5–15 Cloud Run + Secret Manager/Artifact Registry cents.",
            "Scale (50,000 assessments / month): ≈ US$1.5–2.5k Gemini + ≈ US$150–300 Cloud Run; Firestore adds cents per thousand operations.",
            "No per-seat licences; the scorecard and verification run in plain Python.",
        ], size=11),
        ("Estimates use public list prices in October 2026 and the prototype's measured token volumes; actual cost depends on document sizes.", {"size": 9, "color": MUTED}),
    ])

    # ---- 11. Snapshots -------------------------------------------------------------
    s = slides[10]
    add_image(s, "shot_bank.png", Inches(0.3), Inches(1.3), width=Inches(4.6))
    add_image(s, "shot_trust.png", Inches(5.1), Inches(1.3), width=Inches(4.6))
    add_text(s, Inches(0.3), Inches(4.95), Inches(9.4), Inches(0.5), [
        ("Left: Bank view — anonymized passport, offer, Gemini-drafted credit memo. Right: Agent trust — guardrails, step trace and evidence trail.", {"size": 10, "color": MUTED}),
    ])

    # ---- 12. Performance -----------------------------------------------------------
    s = slides[11]
    rows = [
        ("Measure", "Result (live Gemini 3.8 Flash, prototype)"),
        ("End-to-end run, 4 documents, cold", "≈ 60 s wall time (reads in parallel); 25–40 s for 1–3 documents"),
        ("Per step latency", "READ 4–20 s per document · VERIFY ≈ 9 s · SCORE < 5 ms · EXPLAIN ≈ 6 s · WRITE ≈ 14 s; repeated runs < 25 s (document reads cached)"),
        ("Extraction accuracy on synthetic fixtures", "12/12 months exact per document type (Pillow-rendered ledger photo, bank CSV, bank PDF, payment export) on three invented businesses; hackathon rules: no real data; not yet measured on real customer documents"),
        ("Schema validation", "100% of model calls validated on the first attempt in test runs"),
        ("Number guardrail", "≈ 100 numbers checked per memo, 0 invented figures in test runs"),
        ("Verification", "18 checks per assessment; declared-vs-bank gap flagged (−3 points) · thin file flagged"),
        ("Tests", "Unit tests for the scorer, verification math, uploads and storage pass"),
        ("Scorecard calibration", "Not yet calibrated: Kervan v0.1 weights, PD estimates and rate bands are expert-set initial policy, pinned by tests, to be recalibrated on partner-bank portfolio data"),
    ]
    tbl = s.shapes.add_table(len(rows), 2, Inches(0.35), Inches(1.3), Inches(9.3), Inches(3.4)).table
    tbl.columns[0].width = Inches(2.7)
    tbl.columns[1].width = Inches(6.6)
    for r, (a, b) in enumerate(rows):
        tbl.rows[r].height = Inches(0.36)
        for c, text in enumerate((a, b)):
            cell = tbl.cell(r, c)
            cell.text = text
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9 if r else 10)
                    run.font.bold = r == 0 or c == 0
                    run.font.name = "Arial"
                    run.font.color.rgb = INK if r else RGBColor(0xFF, 0xFF, 0xFF)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            if r == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = ACCENT
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0xFA, 0xF9, 0xF7) if r % 2 else RGBColor(0xFF, 0xFF, 0xFF)

    # ---- 13. Future development ----------------------------------------------------
    s = slides[12]
    add_text(s, CONTENT_LEFT, CONTENT_TOP, CONTENT_WIDTH, CONTENT_HEIGHT, [
        heading("Next 90 days"),
        *bullets([
            "Firebase Authentication for business and bank logins (ID tokens verified in FastAPI) and Identity-Aware Proxy in front of the bank view; Cloud Storage with signed URLs for uploaded documents.",
            "Registration and tax documents to fully verify tenure and identity; e-signature consent for data sharing.",
            "Scorecard calibration on portfolio data with a partner bank; monitoring of grade distributions and overrides; step traces to Cloud Logging.",
            "Gemini through Vertex AI so data stays in-region; Cloud Build trigger on main running the tests before each deploy; bank-side API into loan-origination systems.",
        ], size=11),
        heading("Scale story"),
        *bullets([
            "Same pipeline for Kazakhstan, Uzbekistan and Kyrgyzstan: a per-market scorecard config and prompt language notes; schemas, verification math, guardrails and deployment stay the same.",
            "Owners carry their passport between lenders and refresh it as records grow — the credit file follows the business, not the bank.",
        ], size=11),
    ])

    # ---- 14. Links -----------------------------------------------------------------
    s = slides[13]
    add_text(s, CONTENT_LEFT, Inches(2.3), CONTENT_WIDTH, Inches(3.0), [
        ("GitHub public repository", {"bold": True, "size": 13, "color": ACCENT}),
        (LINKS["github"], {"size": 13}),
        ("Demo video (3–4 minutes)", {"bold": True, "size": 13, "color": ACCENT}),
        (LINKS["video"], {"size": 13}),
        ("Final product link (Cloud Run)", {"bold": True, "size": 13, "color": ACCENT}),
        (LINKS["product"], {"size": 13}),
    ])

    # ---- 15. Spare slide: thank you line ------------------------------------------
    s = slides[14]
    add_text(s, Inches(0.5), Inches(2.0), Inches(9.0), Inches(1.6), [
        ("Kervan Credit Passport", {"bold": True, "size": 28, "color": ACCENT}),
        ("Gemini reads, verifies, explains and writes. The score comes only from the scorecard.", {"size": 14}),
        ("Team TL · Taivanbat Bayarsaikhan · Lkhagvadorj Munkhdalai", {"size": 13, "color": MUTED}),
        ("AI Engineers & Data Scientists at M bank, Mongolia's first neobank", {"size": 12, "color": MUTED}),
    ])
    add_image(s, "mbank_logo.png", Inches(7.7), Inches(0.95), width=Inches(1.9))

    # ---- Drop the instruction slide (index 1) --------------------------------------
    delete_slide(prs, 1)

    OUT.parent.mkdir(exist_ok=True)
    prs.save(str(OUT))
    return OUT


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        LINKS["product"] = sys.argv[1]
    if len(sys.argv) > 2:
        LINKS["video"] = sys.argv[2]
    print("wrote", build())
