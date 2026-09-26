"""Render a pitch (from pitch.py) as a 16:9 PowerPoint deck.

Every claim is followed by a small grey source line (e.g. "HDFC ERGO brochure p.11"), so the
deck itself shows where each statement came from. Rejected claims (advisor decision) are left out.
"""
import io
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

NAVY = RGBColor(0x00, 0x2C, 0x77)
NAVY_LIGHT = RGBColor(0xE8, 0xEE, 0xF8)
TEXT = RGBColor(0x1A, 0x1F, 0x2B)
MUTED = RGBColor(0x5F, 0x6B, 0x7A)
BORDER = RGBColor(0xDD, 0xE3, 0xEC)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x1E, 0x8E, 0x3E)
AMBER = RGBColor(0xB2, 0x6A, 0x00)
RED = RGBColor(0xC5, 0x22, 0x1F)
PALE_AMBER = RGBColor(0xFE, 0xF3, 0xE0)
PALE_RED = RGBColor(0xFC, 0xE8, 0xE6)
FONT = "Segoe UI"

W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)
TOTAL_SLIDES = 5


# ---------------------------------------------------------------- drawing helpers

def _box(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.RECTANGLE):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    s.shadow.inherit = False
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    return s


def _text(slide, x, y, w, h, text="", size=14, color=TEXT, bold=False, align=PP_ALIGN.LEFT,
          anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    _set_run(tf.paragraphs[0], text, size, color, bold)
    tf.paragraphs[0].alignment = align
    return tf


def _set_run(paragraph, text, size, color, bold=False, italic=False):
    run = paragraph.add_run()
    run.text = text
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.italic = italic
    return run


def _sources_text(claim) -> str:
    labels = list(dict.fromkeys(s["label"] for s in claim.get("sources", [])))
    if labels:
        return "Source: " + "; ".join(labels)
    if claim.get("claim_type") == "assumption":
        return "Estimate based on the company profile"
    return ""


def _claim_paragraphs(tf, claims, size=14, first=True, bullet="•"):
    """Bullets, each followed by its source line in small grey text."""
    for claim in claims:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_before = Pt(8)
        _set_run(p, f"{bullet} " if bullet else "", size, NAVY, bold=True)
        _set_run(p, claim["text"], size, TEXT)
        source = _sources_text(claim)
        if source:
            sp = tf.add_paragraph()
            sp.space_before = Pt(1)
            _set_run(sp, ("   " if bullet else "") + source, 9, MUTED, italic=True)


def _frame(prs, pitch, n, title, subtitle=""):
    """New slide with the navy title band and footer."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    _box(slide, 0, 0, W, Inches(1.15), fill=NAVY)
    size = 26 if len(title) <= 50 else 22 if len(title) <= 62 else 19
    _text(slide, MARGIN, Inches(0.2), W - 2 * MARGIN, Inches(0.6), title, size, WHITE, bold=True)
    if subtitle:
        _text(slide, MARGIN, Inches(0.72), W - 2 * MARGIN, Inches(0.35), subtitle, 12, RGBColor(0xC9, 0xD6, 0xEE))
    _box(slide, MARGIN, H - Inches(0.55), W - 2 * MARGIN, Pt(1), fill=BORDER)
    _text(slide, MARGIN, H - Inches(0.5), Inches(8), Inches(0.35),
          f"Marsh | Prepared for {pitch['company']} | Draft for advisor review", 9, MUTED)
    _text(slide, W - MARGIN - Inches(1.5), H - Inches(0.5), Inches(1.5), Inches(0.35),
          f"{n} / {TOTAL_SLIDES}", 9, MUTED, align=PP_ALIGN.RIGHT)
    return slide


def _active(claims, decisions):
    """Drop claims the advisor rejected; use edited text where given."""
    result = []
    for c in claims or []:
        d = (decisions or {}).get(c.get("id"), {})
        if d.get("status") == "rejected":
            continue
        result.append({**c, "text": d.get("text") or c["text"]})
    return result


# ---------------------------------------------------------------- slides

def _overview(prs, pitch, s, decisions):
    slide = _frame(prs, pitch, 1, s["title"], pitch["profile"].get("description", ""))
    tiles = list(s.get("facts", {}).items())[:4]
    tile_w, tile_h, gap = Inches(3.6), Inches(1.05), Inches(0.2)
    for i, (key, value) in enumerate(tiles):
        y = Inches(1.5) + i * (tile_h + gap)
        _box(slide, MARGIN, y, tile_w, tile_h, fill=NAVY_LIGHT)
        _text(slide, MARGIN + Inches(0.15), y + Inches(0.1), tile_w - Inches(0.3), Inches(0.3),
              key.replace("_", " ").upper(), 10, MUTED, bold=True)
        _text(slide, MARGIN + Inches(0.15), y + Inches(0.38), tile_w - Inches(0.3), Inches(0.6), value, 15, NAVY, bold=True)
    if tiles:
        _text(slide, MARGIN, Inches(1.5) + 4 * (tile_h + gap) - Inches(0.1), tile_w, Inches(0.3),
              "Source: Wikidata", 9, MUTED)

    x = MARGIN + tile_w + Inches(0.5)
    tf = _text(slide, x, Inches(1.4), W - x - MARGIN, Inches(0.4), "What this means for health cover", 16, NAVY, bold=True)
    _claim_paragraphs(tf, _active(s["claims"], decisions), size=15, first=False)

    risks = pitch["profile"].get("risks", [])[:5]
    if risks:
        tf3 = _text(slide, x, Inches(4.55), W - x - MARGIN, Inches(0.3), "Likely workforce risks (estimates)", 11, MUTED, bold=True)
        for r in risks:
            p = tf3.add_paragraph()
            p.space_before = Pt(3)
            high = r["relevance"] == "high"
            _set_run(p, "HIGH  " if high else "MEDIUM  ", 9, RED if high else AMBER, bold=True)
            _set_run(p, r["label"].split("(")[0].strip(), 11, TEXT)


def _why_marsh(prs, pitch, s, decisions):
    slide = _frame(prs, pitch, 2, s["title"], "Independent, evidence-backed advice for your people")
    claims = _active(s["claims"], decisions)
    card_w = (W - 2 * MARGIN - Inches(0.4)) / 2
    card_h = Inches(2.35)
    for i, c in enumerate(claims[:4]):
        col, row = i % 2, i // 2
        x = MARGIN + col * (card_w + Inches(0.4))
        y = Inches(1.5) + row * (card_h + Inches(0.3))
        _box(slide, x, y, card_w, card_h, fill=WHITE, line=BORDER)
        _box(slide, x, y, Inches(0.12), card_h, fill=NAVY)
        title, _, body = c["text"].partition(":")
        _text(slide, x + Inches(0.35), y + Inches(0.25), card_w - Inches(0.6), Inches(0.5), title.strip(), 18, NAVY, bold=True)
        _text(slide, x + Inches(0.35), y + Inches(0.85), card_w - Inches(0.6), Inches(1.3), body.strip() or title, 14, TEXT)
    _text(slide, MARGIN, H - Inches(0.9), W - 2 * MARGIN, Inches(0.3),
          "Source: " + (claims[0]["sources"][0]["label"] if claims and claims[0].get("sources") else ""), 9, MUTED)


def _risk_benefits(prs, pitch, s, decisions):
    slide = _frame(prs, pitch, 3, s["title"], "Each benefit below is quoted from the insurer's brochure")
    active_ids = {c["id"] for c in _active(s["claims"], decisions)}
    rows = [r for r in s["rows"] if r["claim"]["id"] in active_ids]
    by_id = {c["id"]: c for c in _active(s["claims"], decisions)}
    top, row_h = Inches(1.45), Inches(1.2)
    for i, r in enumerate(rows[:4]):
        y = top + i * (row_h + Inches(0.1))
        high = r["relevance"] == "high"
        _box(slide, MARGIN, y, Inches(3.9), row_h, fill=PALE_RED if high else PALE_AMBER)
        _text(slide, MARGIN + Inches(0.15), y + Inches(0.1), Inches(3.6), Inches(0.3),
              "HIGH RISK" if high else "MEDIUM RISK", 9, RED if high else AMBER, bold=True)
        label = r["risk"].split("(")[0].strip()
        _text(slide, MARGIN + Inches(0.15), y + Inches(0.38), Inches(3.6), Inches(0.8), label, 13, TEXT, bold=True)
        _text(slide, MARGIN + Inches(4.0), y + Inches(0.35), Inches(0.4), Inches(0.5), "→", 22, NAVY, bold=True)
        tf = _text(slide, MARGIN + Inches(4.5), y + Inches(0.05), W - MARGIN - Inches(4.5) - MARGIN, row_h - Inches(0.1), "", 14)
        _claim_paragraphs(tf, [by_id[r["claim"]["id"]]], size=14, bullet="")
    if not rows:
        _text(slide, MARGIN, top, W - 2 * MARGIN, Inches(1), "No verified benefits matched these risks.", 16, MUTED)


def _comparison(prs, pitch, s, decisions):
    slide = _frame(prs, pitch, 4, s["title"], "Values as printed in each insurer's brochure; ★ = recommended")
    table = s["table"]
    cols = table["columns"]
    decided = {c["id"]: c for c in _active(s.get("claims", []), decisions)}
    n_rows, n_cols = len(table["rows"]) + 2, len(cols) + 1
    shape = slide.shapes.add_table(n_rows, n_cols, MARGIN, Inches(1.4), W - 2 * MARGIN, Inches(0.5) * n_rows)
    t = shape.table
    t.columns[0].width = Inches(2.4)
    for j in range(1, n_cols):
        t.columns[j].width = int((W - 2 * MARGIN - Inches(2.4)) / len(cols))

    def cell(i, j, text, size=11, color=TEXT, bold=False, fill=WHITE):
        c = t.cell(i, j)
        c.fill.solid()
        c.fill.fore_color.rgb = fill
        c.margin_left = c.margin_right = Inches(0.08)
        c.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = c.text_frame
        tf.word_wrap = True
        tf.paragraphs[0].text = ""
        _set_run(tf.paragraphs[0], text, size, color, bold)

    cell(0, 0, "", fill=NAVY)
    for j, col in enumerate(cols, start=1):
        star = "★ " if col["code"] == pitch["recommended"] else ""
        cell(0, j, f"{star}{col['name']}", 12, WHITE, True, NAVY)
    cell(1, 0, "Fit score for this workforce", 11, TEXT, True, NAVY_LIGHT)
    for j, col in enumerate(cols, start=1):
        cell(1, j, f"{col['score']} / 100", 12, NAVY, True, NAVY_LIGHT)
    for i, row in enumerate(table["rows"], start=2):
        cell(i, 0, row["label"], 11, TEXT, True)
        for j, c in enumerate(row["cells"], start=1):
            if c["claim_type"] == "none":
                cell(i, j, "Not stated in brochure", 10, MUTED)
            elif c["id"] not in decided:
                cell(i, j, "—", 10, MUTED)  # rejected by the advisor
            else:
                cell(i, j, decided[c["id"]]["text"], 10, TEXT)
    sources = sorted({src["label"] for c in decided.values() for src in c.get("sources", [])})
    _text(slide, MARGIN, H - Inches(1.0), W - 2 * MARGIN, Inches(0.4),
          "Sources: " + "; ".join(sources) + ". Fit score: Marsh scoring of benefits against this workforce's risks.", 9, MUTED)


def _recommendation(prs, pitch, s, decisions):
    slide = _frame(prs, pitch, 5, s["title"], "Recommended as voluntary top-up cover for employees and their families")
    best = next(p for p in pitch["ranking"] if p["code"] == pitch["recommended"])
    _box(slide, MARGIN, Inches(1.45), Inches(3.3), Inches(3.9), fill=NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    _text(slide, MARGIN, Inches(1.8), Inches(3.3), Inches(0.4), "FIT SCORE", 12, RGBColor(0xC9, 0xD6, 0xEE), True, PP_ALIGN.CENTER)
    _text(slide, MARGIN, Inches(2.2), Inches(3.3), Inches(1.2), f"{best['score']}", 60, WHITE, True, PP_ALIGN.CENTER)
    _text(slide, MARGIN, Inches(3.4), Inches(3.3), Inches(0.4), "out of 100", 12, RGBColor(0xC9, 0xD6, 0xEE), False, PP_ALIGN.CENTER)
    _text(slide, MARGIN + Inches(0.2), Inches(4.0), Inches(2.9), Inches(1.2), best["name"], 16, WHITE, True, PP_ALIGN.CENTER)

    x = MARGIN + Inches(3.8)
    tf = _text(slide, x, Inches(1.4), W - x - MARGIN, Inches(0.4), "Why it fits", 16, NAVY, bold=True)
    _claim_paragraphs(tf, _active(s["claims"], decisions), size=15, first=False)
    if best["gaps"]:
        _text(slide, x, Inches(4.75), W - x - MARGIN, Inches(0.6),
              "Not covered by brochure evidence: " + "; ".join(g.split("(")[0].strip() for g in best["gaps"]), 11, AMBER)

    _box(slide, MARGIN, Inches(5.6), W - 2 * MARGIN, Inches(1.1), fill=NAVY_LIGHT)
    _text(slide, MARGIN + Inches(0.2), Inches(5.7), W - 2 * MARGIN - Inches(0.4), Inches(0.9), s["disclaimer"], 10, MUTED)


RENDERERS = {"overview": _overview, "why_marsh": _why_marsh, "risk_benefits": _risk_benefits,
             "comparison": _comparison, "recommendation": _recommendation}


def build_pptx(pitch: dict, decisions: dict | None = None) -> bytes:
    """Return the deck as .pptx bytes. decisions: {claim_id: {"status": "rejected"|..., "text": edited}}."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    for s in pitch["slides"]:
        RENDERERS[s["type"]](prs, pitch, s, decisions or {})
    prs.core_properties.title = f"Marsh pitch for {pitch['company']}"
    prs.core_properties.author = "Marsh Pitch Generator"
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def filename_for(pitch: dict) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", pitch["company"]).strip("_")[:40] or "client"
    return f"Marsh_pitch_{safe}.pptx"