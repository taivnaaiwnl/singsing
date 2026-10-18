"""Synthetic demo data generator for Kervan Credit Passport.

Everything produced here is invented: businesses, owners, bank, account
numbers, transactions, amounts. Nothing is derived from real customers or
real bank data.

Usage:
    python demo_data/generate.py            # writes demo_data/personas/<key>/...

Outputs per persona:
    declared.json          what the owner types into the loan form
    sales_ledger.jpg       "handwritten" ledger photo (Pillow-rendered)
    sales_ledger.truth.json ground truth for the ledger (developer use only)
    bank_statement.csv     12-month (or 4-month) bank statement
    bank_statement.pdf     the same statement as a scanned-looking PDF
    payment_export.csv     payment-provider settlement export
and demo_data/personas/index.json listing the personas for the UI.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "personas"

MN_MONTHS = [
    "Нэгдүгээр сар", "Хоёрдугаар сар", "Гуравдугаар сар", "Дөрөвдүгээр сар",
    "Тавдугаар сар", "Зургаадугаар сар", "Долоодугаар сар", "Наймдугаар сар",
    "Есдүгээр сар", "Аравдугаар сар", "Арван нэгдүгээр сар", "Арван хоёрдугаар сар",
]

HAND_FONTS = [
    "C:/Windows/Fonts/segoepr.ttf",
    "C:/Windows/Fonts/comic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
]
PRINT_FONTS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
]
PRINT_FONTS_BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
]


FALLBACK_FONTS = [
    "C:/Windows/Fonts/DejaVuSans-Oblique.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_glyph_cache: dict[tuple[str, int, str], bool] = {}


def has_glyph(font, ch: str) -> bool:
    """True if `font` has a real glyph for `ch` (compares against the .notdef box)."""
    if ch.isspace():
        return True
    path = getattr(font, "path", "default")
    size = getattr(font, "size", 0)
    key = (str(path), size, ch)
    if key not in _glyph_cache:
        def render(c: str) -> bytes:
            img = Image.new("L", (size * 2 + 8, size * 2 + 8), 0)
            ImageDraw.Draw(img).text((2, 2), c, font=font, fill=255)
            return img.tobytes()
        try:
            _glyph_cache[key] = render(ch) != render("͸")  # U+0378 is unassigned -> .notdef
        except Exception:
            _glyph_cache[key] = True
    return _glyph_cache[key]


def draw_with_fallback(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fallback, fill) -> None:
    """Draw text, switching to `fallback` for characters the main font lacks."""
    x, y = xy
    runs: list[tuple[str, object]] = []
    for ch in text:
        f = font if has_glyph(font, ch) else fallback
        if runs and runs[-1][1] is f:
            runs[-1] = (runs[-1][0] + ch, f)
        else:
            runs.append((ch, f))
    for run, f in runs:
        d.text((x, y), run, font=f, fill=fill)
        x += int(f.getlength(run))

BANK_NAME = "Khangai Bank"
PSP_NAME = "PayNode"

# Last twelve full months before the demo date (2026-10-17): 2025-10 .. 2026-09
PERIOD_END = (2026, 9)


def months_ending(end: tuple[int, int], n: int) -> list[tuple[int, int]]:
    y, m = end
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def ym(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def days_in_month(y: int, m: int) -> int:
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return (nxt - date(y, m, 1)).days


def load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def mnt(v: float) -> str:
    return f"{v:,.0f}₮"


# --------------------------------------------------------------------------- #
# Persona definitions (synthetic)
# --------------------------------------------------------------------------- #
PERSONAS: dict[str, dict] = {
    "persona_a": {
        "title": "Persona A — Erdenet Cashmere Workshop (good, slightly over-declared)",
        "short": "Cashmere workshop, Erdenet · 12 months · declared revenue above bank inflows",
        "expected": "Decent grade (around K3) with one warning: declared revenue exceeds bank-documented inflows.",
        "declared": {
            "business_name": "Erdenet Cashmere Workshop",
            "owner_name": "Oyunchimeg Batbold",
            "sector": "Cashmere garment workshop",
            "location": "Erdenet, Orkhon",
            "years_in_business": 4,
            "monthly_revenue_mnt": 9_000_000,
            "loan_amount_mnt": 20_000_000,
            "tenor_months": 18,
            "purpose": "Buy raw cashmere for the winter production season",
        },
        "account_opened": "2022-11-03",
        "account_number": "5001-0234-5678",
        "opening_balance": 2_850_000,
        "months": 12,
        "inflows_m": [8.9, 9.6, 8.4, 7.1, 5.2, 4.4, 3.9, 4.1, 4.6, 5.0, 5.9, 7.3],  # mean 6.2M
        "outflow_ratio": 0.78,
        "ledger_ratio": 1.12,   # ledger sales incl. cash > bank inflows
        "ledger_expense_ratio": 0.66,
        "payment_share": 0.45,
        "merchant_id": "PN-46961",
        "files": ["sales_ledger.jpg", "bank_statement.csv", "bank_statement.pdf", "payment_export.csv"],
        "credit_descs": ["Борлуулалт - QR төлбөр", "Шилжүүлэг: захиалга", "PayNode settlement", "Захиалга төлбөр", "Жижиглэн борлуулалт", "Бөөний захиалга - Улаанбаатар"],
        "debit_cats": [("Түрээс - цех", 0.12, "fixed"), ("Цалин", 0.28, "fixed"), ("Түүхий эд - ноолуур", 0.42, "var"), ("Цахилгаан, ус", 0.05, "fixed"), ("Бусад зардал", 0.13, "var")],
        "ledger_notes": ["захиалга 14 ш", "", "үзэсгэлэн УБ", "", "", "хаврын хямдрал", "", "", "", "", "түүхий эд авсан", "шинэ захиалга"],
    },
    "persona_b": {
        "title": "Persona B — Khan-Uul Auto Parts Trading (strong)",
        "short": "Auto parts trading, Ulaanbaatar · 12 months · all records consistent",
        "expected": "Top grade (K1): consistent ledger, bank statement and payment export; declared figures verify.",
        "declared": {
            "business_name": "Khan-Uul Auto Parts Trading",
            "owner_name": "Batzorig Ganbaatar",
            "sector": "Auto parts retail and wholesale",
            "location": "Ulaanbaatar, Khan-Uul",
            "years_in_business": 6,
            "monthly_revenue_mnt": 22_000_000,
            "loan_amount_mnt": 40_000_000,
            "tenor_months": 24,
            "purpose": "Expand inventory and open a second sales counter",
        },
        "account_opened": "2020-03-12",
        "account_number": "5001-0876-1123",
        "opening_balance": 9_400_000,
        "months": 12,
        "inflows_m": [20.8, 21.9, 22.4, 19.7, 20.6, 21.3, 22.0, 21.1, 22.6, 21.8, 21.5, 22.3],  # mean 21.5M
        "outflow_ratio": 0.82,
        "ledger_ratio": 1.02,
        "ledger_expense_ratio": 0.74,
        "payment_share": 0.55,
        "merchant_id": "PN-31877",
        "files": ["sales_ledger.jpg", "bank_statement.csv", "payment_export.csv"],
        "credit_descs": ["Борлуулалт - карт", "PayNode settlement", "Шилжүүлэг: сэлбэг", "Бөөний захиалга", "Жижиглэн борлуулалт", "Засварын газар - төлбөр"],
        "debit_cats": [("Түрээс - дэлгүүр", 0.10, "fixed"), ("Цалин", 0.22, "fixed"), ("Бараа татан авалт - сэлбэг", 0.52, "var"), ("Цахилгаан, интернет", 0.03, "fixed"), ("Тээвэр, гааль", 0.13, "var")],
        "ledger_notes": ["", "импорт ирсэн", "", "", "", "", "улирлын захиалга", "", "", "", "", "жилийн тооллого"],
    },
    "persona_c": {
        "title": "Persona C — Darkhan Fresh Bakery (thin file)",
        "short": "Bakery, Darkhan · only 4 months of records · new business",
        "expected": "Low grade (K6) with a clear, re-scored improvement plan: add history and corroborating records.",
        "declared": {
            "business_name": "Darkhan Fresh Bakery",
            "owner_name": "Nomin Erdenebat",
            "sector": "Bakery",
            "location": "Darkhan, Darkhan-Uul",
            "years_in_business": 1,
            "monthly_revenue_mnt": 4_500_000,
            "loan_amount_mnt": 15_000_000,
            "tenor_months": 12,
            "purpose": "Second oven and a display fridge",
        },
        "account_opened": "2025-10-20",
        "account_number": "5001-1902-4471",
        "opening_balance": 640_000,
        "months": 4,
        "inflows_m": [2.4, 3.6, 5.2, 6.6],  # mean 4.45M, ramping up
        "outflow_ratio": 0.9,
        "ledger_ratio": None,
        "ledger_expense_ratio": None,
        "payment_share": None,
        "merchant_id": None,
        "files": ["bank_statement.csv"],
        "credit_descs": ["Борлуулалт - QR төлбөр", "Жижиглэн борлуулалт", "Захиалга - кафе", "Шилжүүлэг: талх захиалга"],
        "debit_cats": [("Түрээс", 0.18, "fixed"), ("Гурил, элсэн чихэр", 0.45, "var"), ("Цалин", 0.22, "fixed"), ("Хий, цахилгаан", 0.08, "fixed"), ("Бусад", 0.07, "var")],
        "ledger_notes": [],
    },
}


# --------------------------------------------------------------------------- #
# Bank statement
# --------------------------------------------------------------------------- #
def split_amount(rng: random.Random, total: float, n: int, step: int = 1000) -> list[int]:
    weights = [rng.lognormvariate(0, 0.6) for _ in range(n)]
    s = sum(weights)
    parts = [int(round(total * w / s / step)) * step for w in weights]
    parts[-1] += int(total - sum(parts))
    return parts


def build_statement(key: str, p: dict, rng: random.Random) -> tuple[list[dict], list[dict]]:
    months = months_ending(PERIOD_END, p["months"])
    balance = p["opening_balance"]
    rows: list[dict] = []
    summary: list[dict] = []
    for (y, m), inflow_m in zip(months, p["inflows_m"]):
        inflow = int(round(inflow_m * 1_000_000))
        outflow = int(round(inflow * p["outflow_ratio"] / 1000) * 1000)
        n_credits = rng.randint(11, 15)
        credits = split_amount(rng, inflow, n_credits)
        dim = days_in_month(y, m)
        credit_days = sorted(rng.randint(1, dim) for _ in range(n_credits))
        debits: list[tuple[int, str, int]] = []
        for desc, share, kind in p["debit_cats"]:
            amt = int(round(outflow * share / 1000) * 1000)
            if kind == "fixed":
                debits.append((rng.randint(1, 6), desc, amt))
            else:
                parts = split_amount(rng, amt, rng.randint(2, 3))
                for part in parts:
                    debits.append((rng.randint(3, dim), desc, part))
        diff = outflow - sum(d[2] for d in debits)
        if diff:
            d0 = debits[0]
            debits[0] = (d0[0], d0[1], d0[2] + diff)
        tx: list[tuple[int, str, int, int]] = []
        for d, amt in zip(credit_days, credits):
            tx.append((d, rng.choice(p["credit_descs"]), 0, amt))
        for d, desc, amt in debits:
            tx.append((d, desc, amt, 0))
        tx.sort(key=lambda t: t[0])
        tot_c = tot_d = 0
        for d, desc, debit, credit in tx:
            balance += credit - debit
            tot_c += credit
            tot_d += debit
            rows.append({"date": f"{y:04d}-{m:02d}-{d:02d}", "description": desc, "debit": debit, "credit": credit, "balance": balance})
        summary.append({"month": ym(y, m), "credits": tot_c, "debits": tot_d, "closing": balance})
    return rows, summary


def write_statement_csv(path: Path, p: dict, rows: list[dict], summary: list[dict]) -> None:
    months = months_ending(PERIOD_END, p["months"])
    start = f"{months[0][0]:04d}-{months[0][1]:02d}-01"
    end_y, end_m = months[-1]
    end = f"{end_y:04d}-{end_m:02d}-{days_in_month(end_y, end_m):02d}"
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"{BANK_NAME} — Account statement"])
    w.writerow(["Account holder", p["declared"]["business_name"]])
    w.writerow(["Account number", p["account_number"]])
    w.writerow(["Account opened", p["account_opened"]])
    w.writerow(["Currency", "MNT"])
    w.writerow(["Statement period", f"{start} to {end}"])
    w.writerow([])
    w.writerow(["Date", "Description", "Debit (MNT)", "Credit (MNT)", "Balance (MNT)"])
    for r in rows:
        w.writerow([r["date"], r["description"], r["debit"] or "", r["credit"] or "", r["balance"]])
    w.writerow([])
    w.writerow(["Monthly summary"])
    w.writerow(["Month", "Total credits (MNT)", "Total debits (MNT)", "Closing balance (MNT)"])
    for s in summary:
        w.writerow([s["month"], s["credits"], s["debits"], s["closing"]])
    w.writerow([])
    w.writerow(["Period totals", "Total credits (MNT)", "Total debits (MNT)"])
    w.writerow(["", sum(s["credits"] for s in summary), sum(s["debits"] for s in summary)])
    path.write_text(buf.getvalue(), encoding="utf-8")


def write_statement_pdf(path: Path, p: dict, rows: list[dict], summary: list[dict]) -> None:
    W, H = 1240, 1754
    font = load_font(PRINT_FONTS, 20)
    bold = load_font(PRINT_FONTS_BOLD, 22)
    title_font = load_font(PRINT_FONTS_BOLD, 34)
    cols = [60, 220, 720, 900, 1080]
    per_page = 44
    pages: list[Image.Image] = []
    months = months_ending(PERIOD_END, p["months"])
    period = f"{months[0][0]:04d}-{months[0][1]:02d}-01 to {months[-1][0]:04d}-{months[-1][1]:02d}-{days_in_month(*months[-1]):02d}"

    def new_page(n: int) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
        img = Image.new("RGB", (W, H), (252, 252, 250))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 110], fill=(23, 58, 94))
        d.text((60, 30), f"{BANK_NAME}", font=title_font, fill="white")
        d.text((60, 72), "Account statement", font=font, fill=(200, 215, 235))
        y = 140
        if n == 1:
            for label, value in [
                ("Account holder", p["declared"]["business_name"]),
                ("Account number", p["account_number"]),
                ("Account opened", p["account_opened"]),
                ("Currency", "MNT"),
                ("Statement period", period),
            ]:
                d.text((60, y), label, font=bold, fill=(40, 40, 40))
                d.text((320, y), value, font=font, fill=(20, 20, 20))
                y += 30
            y += 20
        d.rectangle([50, y, W - 50, y + 34], fill=(230, 236, 244))
        for x, h in zip(cols, ["Date", "Description", "Debit (MNT)", "Credit (MNT)", "Balance (MNT)"]):
            d.text((x, y + 6), h, font=bold, fill=(23, 58, 94))
        d.text((W - 160, H - 50), f"Page {n}", font=font, fill=(120, 120, 120))
        return img, d, y + 44

    page_no = 1
    img, d, y = new_page(page_no)
    count = 0
    for r in rows:
        if count >= per_page:
            pages.append(img)
            page_no += 1
            img, d, y = new_page(page_no)
            count = 0
        d.text((cols[0], y), r["date"], font=font, fill=(20, 20, 20))
        d.text((cols[1], y), r["description"], font=font, fill=(20, 20, 20))
        d.text((cols[2], y), f"{r['debit']:,}" if r["debit"] else "", font=font, fill=(150, 30, 30))
        d.text((cols[3], y), f"{r['credit']:,}" if r["credit"] else "", font=font, fill=(20, 90, 40))
        d.text((cols[4], y), f"{r['balance']:,}", font=font, fill=(20, 20, 20))
        y += 30
        count += 1
    if count > per_page - 18:
        pages.append(img)
        page_no += 1
        img, d, y = new_page(page_no)
    y += 30
    d.text((60, y), "Monthly summary", font=title_font, fill=(23, 58, 94))
    y += 50
    d.rectangle([50, y, W - 50, y + 34], fill=(230, 236, 244))
    for x, h in zip([60, 320, 620, 920], ["Month", "Total credits (MNT)", "Total debits (MNT)", "Closing balance (MNT)"]):
        d.text((x, y + 6), h, font=bold, fill=(23, 58, 94))
    y += 44
    for s in summary:
        for x, v in zip([60, 320, 620, 920], [s["month"], f"{s['credits']:,}", f"{s['debits']:,}", f"{s['closing']:,}"]):
            d.text((x, y), v, font=font, fill=(20, 20, 20))
        y += 30
    y += 10
    d.text((60, y), "Period totals", font=bold, fill=(40, 40, 40))
    d.text((320, y), f"{sum(s['credits'] for s in summary):,}", font=bold, fill=(20, 90, 40))
    d.text((620, y), f"{sum(s['debits'] for s in summary):,}", font=bold, fill=(150, 30, 30))
    pages.append(img)
    # Slightly "scanned" look: light noise + blur
    processed = []
    for pg in pages:
        pg = pg.filter(ImageFilter.GaussianBlur(0.4))
        processed.append(pg.convert("RGB"))
    processed[0].save(path, "PDF", save_all=True, append_images=processed[1:], resolution=150.0)


# --------------------------------------------------------------------------- #
# Payment-provider export
# --------------------------------------------------------------------------- #
def write_payment_export(path: Path, p: dict, rng: random.Random) -> list[dict]:
    months = months_ending(PERIOD_END, p["months"])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["settlement_date", "txn_id", "merchant_id", "channel", "gross_mnt", "fee_mnt", "net_mnt", "status"])
    totals = []
    merchant = p["merchant_id"]
    seq = 100000
    for (y, m), inflow_m in zip(months, p["inflows_m"]):
        net_total = int(round(inflow_m * 1_000_000 * p["payment_share"] / 1000) * 1000)
        n = rng.randint(16, 22)
        nets = split_amount(rng, net_total, n, step=500)
        days = sorted(rng.randint(1, days_in_month(y, m)) for _ in range(n))
        month_net = 0
        for d, net in zip(days, nets):
            fee = int(round(net * 0.012))
            gross = net + fee
            seq += rng.randint(1, 9)
            w.writerow([f"{y:04d}-{m:02d}-{d:02d}", f"PN{seq}", merchant, rng.choice(["qr", "qr", "card"]), gross, fee, net, "settled"])
            month_net += net
        totals.append({"month": ym(y, m), "net": month_net})
    path.write_text(buf.getvalue(), encoding="utf-8")
    return totals


# --------------------------------------------------------------------------- #
# Handwritten-style ledger photo
# --------------------------------------------------------------------------- #
def render_ledger(path: Path, p: dict, rng: random.Random) -> dict:
    months = months_ending(PERIOD_END, p["months"])
    sales = [int(round(v * 1_000_000 * p["ledger_ratio"] / 10000) * 10000) for v in p["inflows_m"]]
    expenses = [int(round(s * p["ledger_expense_ratio"] * rng.uniform(0.95, 1.05) / 10000) * 10000) for s in sales]

    W, H = 1240, 1754
    paper = (244, 236, 214)
    img = Image.new("RGB", (W, H), paper)
    noise = Image.effect_noise((W, H), 18).convert("L")
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    img = Image.blend(img, noise_rgb, 0.08)
    d = ImageDraw.Draw(img)

    # ruled lines + red margin
    line_gap = 62
    top = 150
    for y in range(top, H - 80, line_gap):
        d.line([(60, y), (W - 60, y)], fill=(176, 190, 212), width=2)
    d.line([(130, 60), (130, H - 60)], fill=(214, 120, 120), width=3)
    # punched holes
    for y in (380, 880, 1380):
        d.ellipse([22, y - 16, 54, y + 16], fill=(225, 220, 205), outline=(200, 195, 180))

    hand_title = load_font(HAND_FONTS, 50)
    hand = load_font(HAND_FONTS, 31)
    hand_small = load_font(HAND_FONTS, 25)
    ink = (28, 34, 72)

    fallback_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def fallback_for(font) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        size = getattr(font, "size", 30)
        if size not in fallback_cache:
            fallback_cache[size] = load_font(FALLBACK_FONTS, size)
        return fallback_cache[size]

    def write(x: int, y: int, text: str, font=hand, jitter: int = 3, color=ink) -> None:
        draw_with_fallback(d, (x + rng.randint(-jitter, jitter), y + rng.randint(-jitter, jitter)), text, font, fallback_for(font), color)

    write(150, 60, "Борлуулалтын дэвтэр", font=hand_title, jitter=1)
    write(150, 128, f"{p['declared']['business_name']}   {months[0][0]}–{months[-1][0]}", font=hand_small, jitter=1)

    cols = [150, 560, 830, 1050]
    y = top + line_gap * 2 - 44
    for x, h in zip(cols, ["Сар", "Борлуулалт", "Зарлага", "Тэмдэглэл"]):
        write(x, y, h, jitter=1)
    d.line([(140, y + 50), (W - 80, y + 50)], fill=ink, width=2)

    notes = p.get("ledger_notes") or [""] * len(months)
    rows = []
    y += line_gap
    for i, ((yy, mm), s, e) in enumerate(zip(months, sales, expenses)):
        label = f"{MN_MONTHS[mm - 1]} {yy}"
        write(cols[0], y, label, font=hand_small)
        write(cols[1], y, mnt(s))
        write(cols[2], y, mnt(e))
        if i < len(notes) and notes[i]:
            write(cols[3], y, notes[i], font=hand_small, color=(60, 60, 90))
        rows.append({"month": ym(yy, mm), "sales_mnt": s, "expenses_mnt": e, "row": i + 1})
        y += line_gap
    y += line_gap // 2
    d.line([(140, y - 10), (W - 80, y - 10)], fill=ink, width=2)
    write(cols[0], y, "Нийт борлуулалт:", font=hand)
    write(cols[1], y, mnt(sum(sales)), font=hand)
    y += line_gap
    write(cols[0], y, "Нийт зарлага:", font=hand)
    write(cols[1], y, mnt(sum(expenses)), font=hand)
    y += line_gap * 2
    write(cols[0], y, f"Гарын үсэг: {p['declared']['owner_name'].split()[0]}", font=hand_small, color=(50, 50, 90))
    # signature scribble
    pts = []
    sx, sy = cols[1] + 20, y + 20
    for k in range(18):
        pts.append((sx + k * 14 + rng.randint(-4, 4), sy + int(14 * math.sin(k * 1.3)) + rng.randint(-5, 5)))
    d.line(pts, fill=ink, width=3)

    # coffee ring + vignette + rotation
    ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    cx, cy = W - 260, 300
    rd.ellipse([cx - 110, cy - 110, cx + 110, cy + 110], outline=(150, 100, 40, 70), width=14)
    ring = ring.filter(ImageFilter.GaussianBlur(3))
    img = Image.alpha_composite(img.convert("RGBA"), ring).convert("RGB")

    img = img.rotate(-1.4, resample=Image.BICUBIC, expand=False, fillcolor=(90, 85, 80))
    vign = Image.radial_gradient("L").resize((W, H))
    vign = vign.point(lambda v: 255 - int(v * 0.35))
    img = Image.composite(img, Image.new("RGB", (W, H), (60, 55, 50)), vign)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    img.save(path, "JPEG", quality=84)
    return {"months": rows, "total_sales_mnt": sum(sales), "total_expenses_mnt": sum(expenses)}


# --------------------------------------------------------------------------- #
def generate(out_dir: Path = OUT) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {"generated_for": "Kervan Credit Passport demo", "synthetic": True, "personas": []}
    for key, p in PERSONAS.items():
        rng = random.Random({"persona_a": 11, "persona_b": 23, "persona_c": 37}[key])
        pdir = out_dir / key
        pdir.mkdir(exist_ok=True)
        (pdir / "declared.json").write_text(json.dumps(p["declared"], indent=2, ensure_ascii=False), encoding="utf-8")
        rows, summary = build_statement(key, p, rng)
        write_statement_csv(pdir / "bank_statement.csv", p, rows, summary)
        write_statement_pdf(pdir / "bank_statement.pdf", p, rows, summary)
        truth = {"bank_monthly": summary}
        if p["payment_share"]:
            truth["payments_monthly"] = write_payment_export(pdir / "payment_export.csv", p, rng)
        if p["ledger_ratio"]:
            truth["ledger"] = render_ledger(pdir / "sales_ledger.jpg", p, rng)
            (pdir / "sales_ledger.truth.json").write_text(json.dumps(truth["ledger"], indent=2, ensure_ascii=False), encoding="utf-8")
        (pdir / "truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")
        index["personas"].append({
            "key": key,
            "title": p["title"],
            "short": p["short"],
            "expected": p["expected"],
            "files": p["files"],
            "declared": p["declared"],
        })
        print(f"{key}: {len(rows)} bank rows, {p['months']} months -> {pdir}")
    (out_dir / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return index


if __name__ == "__main__":
    generate()
