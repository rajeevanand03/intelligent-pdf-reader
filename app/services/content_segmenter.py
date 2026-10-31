"""Logical document segmentation.

Turns raw text lines (from PyMuPDF or OCR) into reading-ordered logical elements:
headings (with levels), paragraphs, list items, captions and notes, each carrying
page + coordinates, plus a section hierarchy so that discussions can later be
grown across paragraphs and pages.
"""
import math
import re
from collections import Counter, defaultdict

from .geometry import union, v_overlap

BULLET_RE = re.compile(
    r"^\s*(?:[\u2022\u25cf\u25cb\u25aa\u25a0\u25e6\u2023\u2043\u2219\u27a2\u2713\-\u2013\*]|"
    r"\(?\d{1,3}[.)]|\(?[a-hj-z][.)]|\(?(?:i|ii|iii|iv|v|vi|vii|viii|ix|x)[.)])\s+",
    re.I,
)
CAPTION_RE = re.compile(r"^\s*(figure|fig\.|table|exhibit|chart|graph|diagram)\s*\d+[\w.\-]*\s*[:.\-–]?", re.I)
NOTE_RE = re.compile(r"^\s*(note|notes|source|sources|nb)\s*[:.]", re.I)
LONE_BULLET_RE = re.compile(r"^\s*(?:[\u2022\u25cf\u25cb\u25aa\u25a0\u25e6\u2023\u2043\u2219\u27a2\u2713\-\u2013\*]|\(?\d{1,3}[.)]|\(?[a-z][.)])\s*$", re.I)
PAGE_NUM_RE = re.compile(r"^(page\s*)?#+(\s*(of|/)\s*#+)?$|^-\s*#+\s*-$", re.I)
CASE_RE = re.compile(r"\b(case stud(y|ies)|example|illustration|scenario|incident report)\b", re.I)
CONCLUSION_RE = re.compile(r"\b(conclusions?|recommendations?|summary|key takeaways|lessons learned)\b", re.I)


def _norm_key(text):
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))


def body_font_sizes(pages):
    counters = defaultdict(Counter)
    for p in pages:
        for b in p["blocks"]:
            for ln in b["lines"]:
                counters[p["source"]][round(ln["size"] * 2) / 2] += len(ln["text"])
    sizes = {src: c.most_common(1)[0][0] for src, c in counters.items() if c}
    sizes.setdefault("text", 10.0)
    sizes.setdefault("ocr", sizes["text"])
    return sizes


def detect_headers_footers(pages, margin=0.1):
    """Return {(page_idx, block_idx, line_idx)} for running headers/footers and page numbers."""
    n = len(pages)
    occurrences = defaultdict(set)
    candidates = []
    for pi, p in enumerate(pages):
        h = p["height"]
        for bi, b in enumerate(p["blocks"]):
            for li, ln in enumerate(b["lines"]):
                y0, y1 = ln["bbox"][1], ln["bbox"][3]
                if y1 < margin * h or y0 > (1 - margin) * h:
                    key = _norm_key(ln["text"])
                    candidates.append((pi, bi, li, key))
                    occurrences[key].add(pi)
    threshold = max(2, math.ceil(0.4 * n))
    marks = set()
    for pi, bi, li, key in candidates:
        if PAGE_NUM_RE.match(key) or (n >= 2 and len(occurrences[key]) >= threshold):
            marks.add((pi, bi, li))
    return marks


def _cluster_columns(blocks):
    clusters = []
    for b in sorted(blocks, key=lambda b: b["bbox"][0]):
        placed = False
        for c in clusters:
            if b["bbox"][0] < c["x1"] - 5 and b["bbox"][2] > c["x0"] + 5:
                c["blocks"].append(b)
                c["x0"], c["x1"] = min(c["x0"], b["bbox"][0]), max(c["x1"], b["bbox"][2])
                placed = True
                break
        if not placed:
            clusters.append({"x0": b["bbox"][0], "x1": b["bbox"][2], "blocks": [b]})
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                a, c = clusters[i], clusters[j]
                if a["x0"] < c["x1"] - 5 and a["x1"] > c["x0"] + 5:
                    a["blocks"] += c["blocks"]
                    a["x0"], a["x1"] = min(a["x0"], c["x0"]), max(a["x1"], c["x1"])
                    clusters.pop(j)
                    merged = True
                    break
            if merged:
                break
    return sorted(clusters, key=lambda c: c["x0"])


def order_blocks(blocks, page_width):
    """Reading order that respects multi-column layouts.

    The page is cut into horizontal bands at full-width blocks; inside a band,
    side-by-side columns are read left to right, top to bottom.
    """
    if not blocks:
        return []
    xs0 = min(b["bbox"][0] for b in blocks)
    xs1 = max(b["bbox"][2] for b in blocks)
    content_w = max(1.0, xs1 - xs0)
    mid = (xs0 + xs1) / 2

    def is_full(b):
        w = b["bbox"][2] - b["bbox"][0]
        return w > 0.6 * content_w or (b["bbox"][0] < mid - 0.1 * content_w and b["bbox"][2] > mid + 0.1 * content_w)

    bands, cur = [], []
    for b in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])):
        if is_full(b):
            if cur:
                bands.append(cur)
                cur = []
            bands.append([b])
        else:
            cur.append(b)
    if cur:
        bands.append(cur)

    ordered = []
    for col_idx_base, band in enumerate(bands):
        if len(band) == 1:
            band[0]["column"] = 0
            ordered.extend(band)
            continue
        clusters = _cluster_columns(band)
        side_by_side = any(
            v_overlap(union(x["bbox"] for x in a["blocks"]), union(x["bbox"] for x in c["blocks"])) > 0
            for i, a in enumerate(clusters) for c in clusters[i + 1:]
        )
        if len(clusters) > 1 and side_by_side:
            for ci, c in enumerate(clusters):
                for b in sorted(c["blocks"], key=lambda b: b["bbox"][1]):
                    b["column"] = ci
                    ordered.append(b)
        else:
            for b in sorted(band, key=lambda b: (b["bbox"][1], b["bbox"][0])):
                b["column"] = 0
                ordered.append(b)
    return ordered


def _join_lines(lines):
    text = ""
    for ln in lines:
        t = ln["text"].strip()
        if not t:
            continue
        if text.endswith("-") and t[:1].islower():
            text = text[:-1] + t
        elif text:
            text += " " + t
        else:
            text = t
    return text


def split_paragraphs(block):
    """Split a block's lines into paragraphs / list items."""
    paras = []
    cur = None
    for ln in block["lines"]:
        text = ln["text"].strip()
        if not text:
            continue
        is_bullet = bool(BULLET_RE.match(text))
        new = cur is None
        if cur is not None:
            last = cur["lines"][-1]
            height = max(1.0, last["bbox"][3] - last["bbox"][1])
            gap = ln["bbox"][1] - last["bbox"][3]
            if is_bullet:
                new = True
            elif abs(ln["size"] - last["size"]) > 1.0:
                new = True
            elif ln.get("bold") != last.get("bold") and (ln.get("bold") or last.get("bold")) and len(text.split()) < 15 \
                    and len(last["text"].split()) < 15:
                new = True
            elif gap > max(3.0, 0.8 * height):
                new = True
            elif cur["list"] and ln["bbox"][0] < cur["lines"][0]["bbox"][0] - 4:
                new = True  # end of an indented list item
        if new:
            cur = {"lines": [ln], "list": is_bullet}
            paras.append(cur)
        else:
            cur["lines"].append(ln)
    return paras


def _classify(text, size, bold, body, n_lines, is_list):
    words = len(text.split())
    if CAPTION_RE.match(text) and words <= 40:
        return "caption"
    if NOTE_RE.match(text):
        return "note"
    looks_heading = (
        words <= 18 and n_lines <= 3 and not text.rstrip().endswith((".", ";", ","))
        and (size >= body * 1.12 or (bold and size >= body * 0.9))
        and any(ch.isalpha() for ch in text)
    )
    if looks_heading:
        return "heading"
    if is_list:
        return "list_item"
    return "paragraph"


def build_elements(pages, doc_id, body_sizes):
    """Create reading-ordered logical elements for a document.

    ``pages`` items need: index, width, source, blocks (filtered lines) and
    ``tables`` (table dicts whose first part starts on this page).
    """
    elements = []
    for p in pages:
        body = body_sizes.get(p["source"], body_sizes["text"])
        page_elems = []
        for b in order_blocks(p["blocks"], p["width"]):
            for para in split_paragraphs(b):
                lines = para["lines"]
                text = _join_lines(lines)
                if not text:
                    continue
                size = Counter(round(l["size"], 1) for l in lines for _ in range(len(l["text"]))).most_common(1)[0][0]
                bold_chars = sum(len(l["text"]) for l in lines if l.get("bold"))
                bold = bold_chars >= 0.6 * sum(len(l["text"]) for l in lines)
                el = {
                    "doc_id": doc_id,
                    "page": p["index"],
                    "bbox": [round(v, 2) for v in union(l["bbox"] for l in lines)],
                    "text": text,
                    "type": _classify(text, size, bold, body, len(lines), para["list"]),
                    "size": size,
                    "bold": bold,
                    "column": b.get("column", 0),
                    "source": p["source"],
                    "line_count": len(lines),
                }
                confs = [l["ocr_conf"] for l in lines if "ocr_conf" in l]
                if confs:
                    el["ocr_conf"] = round(sum(confs) / len(confs), 1)
                    el["low_conf"] = any(l.get("low_conf") for l in lines)
                page_elems.append(el)
        for t in p.get("tables", []):
            first = t["parts"][0]
            tel = {
                "doc_id": doc_id,
                "page": p["index"],
                "bbox": first["bbox"],
                "text": (t.get("title") or "") + " [table]",
                "type": "table",
                "table_id": t["id"],
                "size": body,
                "bold": False,
                "column": 0,
                "source": t.get("source", "text"),
                "line_count": 0,
            }
            idx = next((k for k, e in enumerate(page_elems)
                        if e["bbox"][1] >= first["bbox"][1] and e["column"] == 0), len(page_elems))
            page_elems.insert(idx, tel)
        elements.extend(page_elems)

    _assign_heading_levels(elements, body_sizes)
    for i, el in enumerate(elements):
        el["order"] = i
        el["id"] = f"{doc_id}-e{i}"
    _assign_sections(elements)
    return elements


def _assign_heading_levels(elements, body_sizes):
    sizes = sorted({round(e["size"]) for e in elements if e["type"] == "heading"
                    and e["size"] >= body_sizes.get(e["source"], 10) * 1.12}, reverse=True)
    for e in elements:
        if e["type"] != "heading":
            continue
        s = round(e["size"])
        e["heading_level"] = sizes.index(s) + 1 if s in sizes else len(sizes) + 1


def _assign_sections(elements):
    stack = []
    for el in elements:
        if el["type"] == "heading":
            while stack and stack[-1]["heading_level"] >= el["heading_level"]:
                stack.pop()
            stack.append(el)
            el["section_id"] = el["id"]
        else:
            el["section_id"] = stack[-1]["id"] if stack else None
        el["section_path"] = [h["text"] for h in stack]
        el["heading_ids"] = [h["id"] for h in stack]
        tags = []
        scope = " ".join(el["section_path"][-1:]) + " " + el["text"][:80]
        if CASE_RE.search(scope):
            tags.append("case_study")
        if CONCLUSION_RE.search(scope):
            tags.append("conclusion")
        el["tags"] = tags
