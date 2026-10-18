"""Render process_flow.png and architecture.png for the Kervan submission (Pillow only)."""
import math
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = r"C:\Users\taivanbat.b\coding\kervan\submission\assets"
REG = r"C:\Windows\Fonts\segoeui.ttf"
BOLD = r"C:\Windows\Fonts\segoeuib.ttf"

W, H = 2400, 1350
S = 2  # supersample factor for anti-aliased edges

WHITE = (255, 255, 255)
INK = (0x1c, 0x19, 0x17)
MUTED = (0x6b, 0x72, 0x80)
TEAL = (0x0b, 0x6b, 0x85)
HAIR = (0xe7, 0xe5, 0xe4)

WARNINGS = []


def font(path, size):
    return ImageFont.truetype(path, size * S)


class Canvas:
    def __init__(self):
        self.im = Image.new("RGB", (W * S, H * S), WHITE)
        self.d = ImageDraw.Draw(self.im)

    # ---- primitives (logical coordinates) ----
    def rbox(self, x0, y0, x1, y1, outline=INK, fill=None, width=2, radius=12):
        self.d.rounded_rectangle(
            [x0 * S, y0 * S, x1 * S, y1 * S],
            radius=radius * S, outline=outline, fill=fill, width=width * S,
        )

    def line(self, p0, p1, color=INK, width=3):
        self.d.line([(p0[0] * S, p0[1] * S), (p1[0] * S, p1[1] * S)], fill=color, width=width * S)

    def arrow(self, p0, p1, color=INK, width=3, head_len=22, head_w=20, both=False):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        px, py = -uy, ux

        def head(tip, ux, uy):
            bx, by = tip[0] - ux * head_len, tip[1] - uy * head_len
            pts = [tip, (bx + px * head_w / 2, by + py * head_w / 2), (bx - px * head_w / 2, by - py * head_w / 2)]
            self.d.polygon([(x * S, y * S) for x, y in pts], fill=color)

        start = p0
        end = (p1[0] - ux * head_len * 0.7, p1[1] - uy * head_len * 0.7)
        if both:
            start = (p0[0] + ux * head_len * 0.7, p0[1] + uy * head_len * 0.7)
        self.line(start, end, color, width)
        head(p1, ux, uy)
        if both:
            head(p0, -ux, -uy)

    def text_width(self, text, f):
        return f.getlength(text) / S

    def wrap(self, text, f, max_w):
        lines = []
        for para in text.split("\n"):
            cur = ""
            for w in para.split(" "):
                trial = (cur + " " + w).strip()
                if self.text_width(trial, f) <= max_w or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
        for ln in lines:
            if self.text_width(ln, f) > max_w:
                WARNINGS.append(f"line too wide ({self.text_width(ln, f):.0f} > {max_w}): {ln!r}")
        return lines

    def text(self, x, y, s, f, color=INK, anchor="la"):
        self.d.text((x * S, y * S), s, font=f, fill=color, anchor=anchor)

    def block(self, cx, y, lines, f, line_h, color=INK, align="center", x_left=None):
        """Draw lines starting at top y; returns bottom y."""
        for ln in lines:
            if align == "center":
                self.text(cx, y, ln, f, color, anchor="ma")
            else:
                self.text(x_left, y, ln, f, color, anchor="la")
            y += line_h
        return y

    def card(self, box, title, detail, tf, df, t_lh, d_lh, *, outline=INK, fill=None,
             t_color=INK, d_color=MUTED, pad=18, gap=8, width=2, align="center", name=""):
        x0, y0, x1, y1 = box
        self.rbox(x0, y0, x1, y1, outline=outline, fill=fill, width=width)
        max_w = (x1 - x0) - 2 * pad
        t_lines = self.wrap(title, tf, max_w) if title else []
        d_lines = self.wrap(detail, df, max_w) if detail else []
        total = len(t_lines) * t_lh + len(d_lines) * d_lh + (gap if t_lines and d_lines else 0)
        inner_h = (y1 - y0) - 2 * pad
        if total > inner_h:
            WARNINGS.append(f"card {name or title!r} overflows: text {total} > inner {inner_h}")
        y = y0 + (y1 - y0 - total) / 2
        cx = (x0 + x1) / 2
        xl = x0 + pad
        y = self.block(cx, y, t_lines, tf, t_lh, t_color, align, xl)
        if t_lines and d_lines:
            y += gap
        self.block(cx, y, d_lines, df, d_lh, d_color, align, xl)

    def save(self, path):
        out = self.im.resize((W, H), Image.LANCZOS)
        out.save(path, "PNG", optimize=True)
        return out.size


# ----------------------------------------------------------------------------
def process_flow():
    c = Canvas()
    f_label = font(REG, 28)
    f_title = font(BOLD, 30)
    f_detail = font(REG, 28)
    f_ptitle = font(BOLD, 36)
    f_pdesc = font(REG, 28)
    f_caption = font(REG, 30)

    CY = 640  # vertical centre of the diagram body
    # ---- inputs -------------------------------------------------------------
    in_x0, in_x1 = 60, 420
    in_h, in_gap, decl_gap = 140, 36, 56
    inputs = [
        ("Ledger photo", "handwritten, Mongolian"),
        ("Bank statement", "PDF / CSV"),
        ("Payment export", "CSV"),
        ("Owner's declaration", ""),
    ]
    total = 4 * in_h + 2 * in_gap + decl_gap
    y = CY - total / 2
    in_centers = []
    for i, (t, d) in enumerate(inputs):
        box = (in_x0, y, in_x1, y + in_h)
        c.card(box, t, d, f_title, f_detail, 38, 36, outline=INK, name=t)
        in_centers.append(y + in_h / 2)
        y += in_h + (decl_gap if i == 2 else in_gap)
    c.text(in_x0, 215, "Inputs", f_label, MUTED)

    # ---- pipeline -----------------------------------------------------------
    p_x0, p_w, p_gap, p_h = 520, 228, 44, 460
    p_y0 = CY - p_h / 2
    stages = [
        ("READ", "Gemini multimodal, structured JSON, page/row pointers", False),
        ("VERIFY", "Python arithmetic + Gemini checks, conservative status wins", False),
        ("SCORE", "deterministic scorecard, no LLM", True),
        ("EXPLAIN", "Gemini writes; gains re-scored; number guardrail", False),
        ("WRITE", "Gemini credit memo + evidence appendix", False),
    ]
    boxes = []
    for i, (t, d, teal) in enumerate(stages):
        x0 = p_x0 + i * (p_w + p_gap)
        box = (x0, p_y0, x0 + p_w, p_y0 + p_h)
        boxes.append(box)
        if teal:
            c.card(box, t, d, f_ptitle, f_pdesc, 46, 36, outline=TEAL, fill=TEAL,
                   t_color=WHITE, d_color=WHITE, pad=18, gap=14, name=t)
        else:
            c.card(box, t, d, f_ptitle, f_pdesc, 46, 36, outline=INK,
                   t_color=INK, d_color=INK, pad=18, gap=14, name=t)
        if i > 0:
            prev = boxes[i - 1]
            c.arrow((prev[2], CY), (x0, CY))
    c.text(p_x0, 215, "Pipeline", f_label, MUTED)
    p_x1 = boxes[-1][2]

    # inputs -> READ (bus)
    bus_x = (in_x1 + p_x0) / 2
    for cy in in_centers:
        c.line((in_x1, cy), (bus_x, cy))
    c.line((bus_x, in_centers[0]), (bus_x, in_centers[-1]))
    c.arrow((bus_x, CY), (p_x0, CY))

    # ---- outputs ------------------------------------------------------------
    o_x0, o_x1 = 1940, 2340
    o_h, o_gap = 160, 44
    outputs = [
        ("Credit passport", "grade K1\u2013K7, PD, rate band, factors, flags"),
        ("Owner explanation\n(EN / MN)", "+ improvement plan"),
        ("Underwriter credit memo", ""),
    ]
    total = 3 * o_h + 2 * o_gap
    y = CY - total / 2
    out_centers = []
    for t, d in outputs:
        c.card((o_x0, y, o_x1, y + o_h), t, d, f_title, f_detail, 38, 36, outline=INK, name=t)
        out_centers.append(y + o_h / 2)
        y += o_h + o_gap
    c.text(o_x0, 215, "Outputs", f_label, MUTED)

    # WRITE -> outputs (bus)
    bus_x = (p_x1 + o_x0) / 2
    c.line((p_x1, CY), (bus_x, CY))
    c.line((bus_x, out_centers[0]), (bus_x, out_centers[-1]))
    for cy in out_centers:
        c.arrow((bus_x, cy), (o_x0, cy))

    # ---- caption ------------------------------------------------------------
    c.line((60, 1210), (2340, 1210), HAIR, 2)
    c.text(W / 2, 1252, "Gemini reads, verifies, explains and writes. The score comes only from the scorecard.",
           f_caption, MUTED, anchor="ma")
    return c.save(OUT_DIR + r"\process_flow.png")


# ----------------------------------------------------------------------------
def architecture():
    c = Canvas()
    f_label = font(REG, 28)
    f_h = font(BOLD, 36)
    f_t = font(BOLD, 30)
    f_d = font(REG, 28)

    # container
    cx0, cy0, cx1, cy1 = 700, 215, 1780, 915
    c.rbox(cx0, cy0, cx1, cy1, outline=INK, width=2, radius=16)
    c.text(cx0 + 30, cy0 + 26, "Cloud Run \u2014 FastAPI service", f_h, INK)
    CY = (cy0 + cy1) / 2

    ix0, ix1 = cx0 + 30, cx1 - 30
    # pipeline.py box (full width)
    pb = (ix0, 305, ix1, 425)
    c.card(pb, "pipeline.py", "READ \u2192 VERIFY \u2192 SCORE \u2192 EXPLAIN \u2192 WRITE",
           f_t, f_d, 38, 36, outline=INK, d_color=INK, name="pipeline")

    col_gap = 30
    col_w = (ix1 - ix0 - col_gap) / 2
    lx0, lx1 = ix0, ix0 + col_w
    rx0, rx1 = ix1 - col_w, ix1
    r1y0, r1y1 = 465, 645
    r2y0, r2y1 = 685, 865

    c.card((lx0, r1y0, lx1, r1y1), "scorecard_config.py + score.py", "deterministic scorecard, no LLM",
           f_t, f_d, 38, 36, outline=TEAL, t_color=TEAL, d_color=INK, name="score")
    gc = (rx0, r1y0, rx1, r1y1)
    c.card(gc, "gemini_client.py", "response_schema + Pydantic, retry, timeout, cache, trace",
           f_t, f_d, 38, 36, outline=INK, d_color=INK, name="gemini_client")
    c.card((lx0, r2y0, lx1, r2y1), "guardrail.py", "no invented numbers",
           f_t, f_d, 38, 36, outline=INK, d_color=INK, name="guardrail")
    c.card((rx0, r2y0, rx1, r2y1), "store.py", "JSON on instance; Firestore planned",
           f_t, f_d, 38, 36, outline=INK, d_color=INK, name="store")

    # browser (left)
    bx0, bx1 = 60, 420
    bh = 200
    bb = (bx0, CY - bh / 2, bx1, CY + bh / 2)
    c.card(bb, "Browser", "static/index.html\nvanilla JS, SSE", f_h, f_d, 46, 36, outline=INK, d_color=INK,
           gap=12, name="browser")
    c.arrow((bx1, CY), (cx0, CY), both=True)
    mid = (bx1 + cx0) / 2
    c.text(mid, CY - 24 - 36 - 36, "HTTPS +", f_label, MUTED, anchor="ma")
    c.text(mid, CY - 24 - 36, "Server-Sent Events", f_label, MUTED, anchor="ma")

    # gemini (right)
    gx0, gx1 = 1940, 2340
    gh = 200
    gcy = (r1y0 + r1y1) / 2
    gb = (gx0, gcy - gh / 2, gx1, gcy + gh / 2)
    c.card(gb, "Gemini 3.8 Flash", "google-genai SDK", f_h, f_d, 46, 36, outline=INK, d_color=INK,
           gap=12, name="gemini")
    c.arrow((gc[2], gcy), (gx0, gcy))

    # infra row (below container)
    iy0, iy1 = 1035, 1155
    iw, igap = 340, 30
    infra = [
        ("Cloud Build", "deploy --source", True),
        ("Secret Manager", "GEMINI_API_KEY", True),
        ("Artifact Registry", "", False),
    ]
    x = cx0
    for t, d, up in infra:
        box = (x, iy0, x + iw, iy1)
        c.card(box, t, d, f_t, f_d, 38, 36, outline=INK, d_color=INK, name=t)
        if up:
            c.arrow(((box[0] + box[2]) / 2, iy0), ((box[0] + box[2]) / 2, cy1))
        x += iw + igap

    return c.save(OUT_DIR + r"\architecture.png")


if __name__ == "__main__":
    s1 = process_flow()
    s2 = architecture()
    print("process_flow.png", s1)
    print("architecture.png", s2)
    for w in WARNINGS:
        print("WARNING:", w)
    if not WARNINGS:
        print("no layout warnings")
