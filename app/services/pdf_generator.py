"""Assemble approved snapshots into a consolidated, topic-focused PDF."""
import io
import os
from datetime import datetime

import pymupdf as fitz
from PIL import Image

PAGE_W, PAGE_H = fitz.paper_size("a4")
MARGIN = 48
TOP = 56
BOTTOM = 52
AVAIL_W = PAGE_W - 2 * MARGIN
DARK = (0.13, 0.17, 0.25)
GREY = (0.38, 0.42, 0.48)
ACCENT = (0.11, 0.35, 0.71)
LEVEL_COLORS = {"HIGH": (0.10, 0.53, 0.33), "MEDIUM": (0.80, 0.52, 0.05), "LOW": (0.45, 0.45, 0.50)}


def _wrap(text, size, font, width):
    lines = []
    for para in str(text).split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if fitz.get_text_length(trial, fontname=font, fontsize=size) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                while fitz.get_text_length(w, fontname=font, fontsize=size) > width and len(w) > 1:
                    cut = max(1, int(len(w) * width / fitz.get_text_length(w, fontname=font, fontsize=size)) - 1)
                    lines.append(w[:cut])
                    w = w[cut:]
                cur = w
        lines.append(cur)
    return lines


class Writer:
    def __init__(self, title):
        self.doc = fitz.open()
        self.title = title
        self.page = None
        self.y = TOP

    def new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = TOP
        return self.page

    def space_left(self):
        return PAGE_H - BOTTOM - self.y

    def ensure(self, h):
        if self.page is None or self.space_left() < h:
            self.new_page()

    def text(self, text, size=10, bold=False, color=DARK, x=MARGIN, width=AVAIL_W, gap=2):
        font = "hebo" if bold else "helv"
        lh = size * 1.32
        for line in _wrap(text, size, font, width):
            self.ensure(lh)
            self.page.insert_text((x, self.y + size), line, fontname=font, fontsize=size, color=color)
            self.y += lh
        self.y += gap

    def rule(self, color=(0.82, 0.85, 0.9), width=0.6):
        self.page.draw_line((MARGIN, self.y), (PAGE_W - MARGIN, self.y), color=color, width=width)
        self.y += 6

    def image(self, path, width_pt, height_pt, pad_after=8):
        scale = min(1.0, AVAIL_W / width_pt)
        w, h = width_pt * scale, height_pt * scale
        x0 = MARGIN + (AVAIL_W - w) / 2 if w < AVAIL_W * 0.6 else MARGIN
        full_h = PAGE_H - TOP - BOTTOM
        if h <= self.space_left():
            self._place(path, x0, w, h, None)
        elif h <= full_h and h > 0.35 * self.space_left():
            self.new_page()
            self._place(path, x0, w, h, None)
        else:
            img = Image.open(path)
            px_per_pt = img.height / h
            top_px = 0
            while top_px < img.height:
                avail = self.space_left()
                if avail < 60:
                    self.new_page()
                    avail = self.space_left()
                slice_px = min(img.height - top_px, int(avail * px_per_pt))
                crop = img.crop((0, top_px, img.width, top_px + slice_px))
                buf = io.BytesIO()
                crop.save(buf, format="PNG")
                self._place(None, x0, w, slice_px / px_per_pt, buf.getvalue())
                top_px += slice_px
                if top_px < img.height:
                    self.new_page()
        self.y += pad_after

    def _place(self, path, x0, w, h, stream):
        rect = fitz.Rect(x0, self.y, x0 + w, self.y + h)
        if stream is not None:
            self.page.insert_image(rect, stream=stream)
        else:
            self.page.insert_image(rect, filename=path)
        self.page.draw_rect(rect, color=(0.85, 0.87, 0.9), width=0.4)
        self.y += h


def _fmt_pages(pages):
    pages = sorted(set(pages))
    if not pages:
        return ""
    if len(pages) == 1:
        return f"Page {pages[0]}"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"Pages {pages[0]}-{pages[-1]}"
    return "Pages " + ", ".join(str(p) for p in pages)


def generate_pdf(session, out_path, snapshot_dir, title=None):
    docs = {d["id"]: d for d in session["documents"]}
    doc_order = {d["id"]: i for i, d in enumerate(session["documents"])}
    included = [r for r in session["results"] if r.get("included")]
    topics = [t for t in session.get("topics_run") or [] if any(r["topic"] == t for r in included)]
    topics += sorted({r["topic"] for r in included} - set(topics))
    title = (title or "").strip() or "Topic-Focused Extract"
    interests = session.get("interests", {})

    w = Writer(title)
    w.new_page()
    w.page.draw_rect(fitz.Rect(0, 0, PAGE_W, 150), color=None, fill=DARK)
    w.page.insert_text((MARGIN, 82), title[:70], fontname="hebo", fontsize=24, color=(1, 1, 1))
    w.page.insert_text((MARGIN, 110), "Interest-based extraction of original document content",
                       fontname="helv", fontsize=11, color=(0.8, 0.85, 0.95))
    w.y = 180
    used_docs = sorted({r["doc_id"] for r in included}, key=lambda d: doc_order.get(d, 0))
    total_pages = sum(docs[d]["pages"] for d in docs if docs[d].get("status") == "ready")
    sections = sum(1 for r in included if r["kind"] == "section")
    tables = sum(1 for r in included if r["kind"] == "table") + sum(
        1 for r in included if r["kind"] == "section" and any(g["kind"] == "table" for g in r["regions"]))
    src_pages = {(r["doc_id"], p) for r in included for p in r["pages"]}

    w.text("Generated", 9, True, GREY, gap=0)
    w.text(datetime.now().strftime("%d %B %Y, %H:%M"), 11, gap=8)
    w.text("Source documents", 9, True, GREY, gap=0)
    for d in session["documents"]:
        if d.get("status") == "ready":
            mark = "" if d["id"] in used_docs else "  (no content selected)"
            w.text(f"- {d['filename']}  ({d['pages']} pages){mark}", 11, gap=0)
    w.y += 8
    w.text("Selected topics", 9, True, GREY, gap=0)
    w.text(", ".join(topics) or "-", 11, gap=8)
    if interests.get("description"):
        w.text("Topic description", 9, True, GREY, gap=0)
        w.text(interests["description"], 10, gap=8)
    w.text("Keywords", 9, True, GREY, gap=0)
    w.text(", ".join(interests.get("keywords") or []) or "-", 10, gap=8)
    w.text("Summary", 9, True, GREY, gap=0)
    for line in (f"Original pages analysed: {total_pages}",
                 f"Extracted sections: {sections}",
                 f"Extracted tables: {tables}",
                 f"Source pages represented: {len(src_pages)}"):
        w.text(line, 11, gap=0)
    w.y += 10
    w.text("All content in this document is reproduced as original snapshots of the source pages. "
           "Each extract is labelled with its source document and original page number(s).", 9, color=GREY)

    toc = []
    for topic in topics:
        w.new_page()
        toc.append([1, topic, w.doc.page_count])
        w.page.draw_rect(fitz.Rect(MARGIN, w.y, PAGE_W - MARGIN, w.y + 30), color=None, fill=ACCENT)
        w.page.insert_text((MARGIN + 10, w.y + 20), f"Topic: {topic}"[:80], fontname="hebo", fontsize=14,
                           color=(1, 1, 1))
        w.y += 42
        items = sorted((r for r in included if r["topic"] == topic),
                       key=lambda r: (doc_order.get(r["doc_id"], 0), r["pages"][0] if r["pages"] else 0,
                                      r.get("first_order", 0)))
        for r in items:
            first = r["regions"][0] if r["regions"] else None
            first_h = 0
            if first:
                first_h = (first["bbox"][3] - first["bbox"][1]) * min(1.0, AVAIL_W / max(1, first["bbox"][2] - first["bbox"][0]))
                if first_h > PAGE_H - TOP - BOTTOM - 40:
                    first_h = 200  # will be split across pages; just avoid an orphaned header
            w.ensure(40 + first_h)
            src = docs.get(r["doc_id"], {}).get("filename", "")
            toc.append([2, f"{src} - {_fmt_pages(r['pages'])}", w.doc.page_count])
            meta = f"Source: {src}   |   {_fmt_pages(r['pages'])}"
            if r.get("section"):
                meta += f"   |   Section: {r['section']}"
            w.text(meta, 8.5, True, DARK, gap=0)
            kind = "Table" if r["kind"] == "table" else "Discussion"
            extra = f"   |   Also relevant to: {', '.join(r['also_relevant_to'])}" if r.get("also_relevant_to") else ""
            w.text(f"{kind}   |   Relevance: {r['level']}{extra}", 8, color=LEVEL_COLORS.get(r["level"], GREY), gap=2)
            w.rule()
            for g in r["regions"]:
                path = os.path.join(snapshot_dir, g["file"])
                if os.path.exists(path):
                    w.image(path, g["bbox"][2] - g["bbox"][0], g["bbox"][3] - g["bbox"][1], pad_after=6)
            w.y += 10

    n = w.doc.page_count
    for i, page in enumerate(w.doc, start=1):
        if i == 1:
            continue
        page.draw_line((MARGIN, PAGE_H - 36), (PAGE_W - MARGIN, PAGE_H - 36), color=(0.85, 0.87, 0.9), width=0.5)
        page.insert_text((MARGIN, PAGE_H - 22), title[:80], fontname="helv", fontsize=8, color=GREY)
        label = f"Page {i} of {n}"
        page.insert_text((PAGE_W - MARGIN - fitz.get_text_length(label, "helv", 8), PAGE_H - 22), label,
                         fontname="helv", fontsize=8, color=GREY)
    w.doc.set_toc(toc)
    w.doc.set_metadata({"title": title, "creator": "Intelligent PDF Reader", "producer": "PyMuPDF"})
    w.doc.save(out_path, garbage=3, deflate=True)
    w.doc.close()
    return {"pages": n, "results": len(included), "topics": topics}
