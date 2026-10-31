"""PDF validation and page-by-page document analysis."""
import logging

import pymupdf as fitz

from . import content_segmenter as seg
from .geometry import center, contains_point, pad, union
from .ocr_processor import OCRError, ocr_page
from .table_extractor import attach_context, extract_page_tables, merge_multipage_tables

log = logging.getLogger(__name__)


class PDFError(Exception):
    def __init__(self, message, code="invalid_pdf"):
        super().__init__(message)
        self.message = message
        self.code = code


def inspect_pdf(path, password=None):
    """Validate a PDF and return basic info. Raises PDFError with a user-facing message."""
    with open(path, "rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        raise PDFError("The file is not a valid PDF document.", "not_pdf")
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise PDFError("The PDF is corrupted or cannot be opened.", "corrupted") from exc
    try:
        encrypted = bool(doc.needs_pass)
        if doc.needs_pass:
            if not password:
                raise PDFError("This PDF is password-protected. Enter its password to unlock it.", "password_required")
            if not doc.authenticate(password):
                raise PDFError("Incorrect password for this PDF.", "wrong_password")
        if doc.page_count == 0:
            raise PDFError("The PDF contains no pages.", "empty")
        try:
            doc[0].get_text("text")
        except Exception as exc:
            raise PDFError("The PDF is corrupted or cannot be read.", "corrupted") from exc
        return {"page_count": doc.page_count, "encrypted": encrypted, "title": (doc.metadata or {}).get("title") or ""}
    finally:
        doc.close()


def unlock_pdf(path, password, out_path):
    doc = fitz.open(path)
    try:
        if doc.needs_pass and not doc.authenticate(password):
            raise PDFError("Incorrect password for this PDF.", "wrong_password")
        doc.save(out_path, encryption=fitz.PDF_ENCRYPT_NONE, garbage=1)
    finally:
        doc.close()


def extract_text_blocks(page):
    d = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    blocks = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines = []
        for ln in b.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s.get("text")]
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            weights = [(len(s["text"].strip()) or 1) for s in spans]
            total = sum(weights)
            size = max(spans, key=lambda s: len(s["text"].strip()))["size"]
            bold_w = sum(w for s, w in zip(spans, weights)
                         if (s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower()
                         or "black" in s.get("font", "").lower())
            lines.append({
                "bbox": [round(v, 2) for v in ln["bbox"]],
                "text": text,
                "size": round(size, 2),
                "bold": bold_w >= 0.6 * total,
            })
        if lines:
            blocks.append({"bbox": union(l["bbox"] for l in lines), "lines": lines})
    return blocks


def page_needs_ocr(page, blocks):
    chars = sum(len(l["text"].strip()) for b in blocks for l in b["lines"])
    if chars >= 50:
        return False
    page_area = page.rect.width * page.rect.height
    img_area = 0.0
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"]) & page.rect
        img_area += r.width * r.height
    return img_area > 0.25 * page_area


def analyze_document(path, doc_id, options, progress=None):
    """Analyse one PDF page by page.

    options: ocr (bool), tables (bool), ocr_dpi, ocr_lang, ocr_low_conf, use_camelot.
    Returns dict(elements, tables, pages, warnings, body_sizes).
    """
    import pdfplumber

    doc = fitz.open(path)
    plumb = pdfplumber.open(path) if options.get("tables", True) else None
    n = doc.page_count
    pages, warnings = [], []
    try:
        for i in range(n):
            page = doc[i]
            rec = {"index": i, "width": page.rect.width, "height": page.rect.height, "source": "text",
                   "blocks": [], "raw_tables": [], "ocr_conf": None, "unprocessed": False}
            try:
                blocks = extract_text_blocks(page)
            except Exception as exc:
                blocks = []
                warnings.append({"page": i + 1, "level": "error", "message": f"Text extraction failed: {exc}"})
            if page_needs_ocr(page, blocks):
                if options.get("ocr", True):
                    try:
                        r = ocr_page(page, dpi=options.get("ocr_dpi", 300), lang=options.get("ocr_lang", "eng"),
                                     low_conf=options.get("ocr_low_conf", 60),
                                     detect_tables=options.get("tables", True))
                        rec.update(blocks=r["blocks"], raw_tables=r["tables"], source="ocr", ocr_conf=r["mean_conf"])
                        if r["word_count"] == 0:
                            rec["unprocessed"] = True
                            warnings.append({"page": i + 1, "level": "warning",
                                             "message": "Scanned page: OCR recognised no text."})
                        elif r["mean_conf"] is not None and r["mean_conf"] < options.get("ocr_low_conf", 60):
                            warnings.append({"page": i + 1, "level": "warning",
                                             "message": f"Low OCR confidence ({r['mean_conf']}%). Review extracts."})
                    except OCRError as exc:
                        rec["unprocessed"] = True
                        warnings.append({"page": i + 1, "level": "error", "message": f"OCR failed: {exc}"})
                else:
                    rec["unprocessed"] = True
                    warnings.append({"page": i + 1, "level": "warning",
                                     "message": "Page appears to be scanned but OCR is disabled; it was not searched."})
            else:
                rec["blocks"] = blocks
                if plumb is not None:
                    pp = plumb.pages[i]
                    try:
                        rec["raw_tables"] = extract_page_tables(pp, page, i, path, options.get("use_camelot", False))
                    finally:
                        close = getattr(pp, "close", None)
                        if close:
                            close()
            pages.append(rec)
            if progress:
                progress(i + 1, n)
    finally:
        doc.close()
        if plumb is not None:
            plumb.close()

    hf = seg.detect_headers_footers(pages)
    tables, body_sizes = _separate_tables(pages, hf, doc_id)
    bounds = {}
    for p in pages:
        ys = [l["bbox"] for b in p["blocks"] for l in b["lines"]]
        bounds[p["index"]] = (min(y[1] for y in ys), max(y[3] for y in ys)) if ys else (None, None)
    tables = merge_multipage_tables(tables, bounds)
    for k, t in enumerate(tables):
        t["id"] = f"{doc_id}-t{k}"
    by_page = {}
    for t in tables:
        by_page.setdefault(t["parts"][0]["page"], []).append(t)
    for p in pages:
        p["tables"] = by_page.get(p["index"], [])

    elements = seg.build_elements(pages, doc_id, body_sizes)
    page_meta = [{"index": p["index"], "width": p["width"], "height": p["height"], "source": p["source"],
                  "ocr_conf": p["ocr_conf"], "unprocessed": p["unprocessed"]} for p in pages]
    return {"elements": elements, "tables": tables, "pages": page_meta, "warnings": warnings,
            "body_sizes": body_sizes}


def _merge_lone_bullets(lines):
    """Attach bullet glyphs that PDF generators emit as separate lines to their item text."""
    lone = [i for i, ln in enumerate(lines) if seg.LONE_BULLET_RE.match(ln["text"])]
    drop = set()
    for i in lone:
        b = lines[i]["bbox"]
        best, best_dx = None, None
        for j, ln in enumerate(lines):
            if j == i or j in drop or j in lone:
                continue
            o = ln["bbox"]
            overlap = min(b[3], o[3]) - max(b[1], o[1])
            dx = o[0] - b[2]
            if overlap > 0.4 * (b[3] - b[1]) and -1 <= dx < 40 and (best_dx is None or dx < best_dx):
                best, best_dx = j, dx
        if best is not None:
            tgt = lines[best]
            tgt["text"] = lines[i]["text"].strip() + " " + tgt["text"].lstrip()
            tgt["bbox"] = union([tgt["bbox"], b])
            drop.add(i)
    return drop


def _separate_tables(pages, hf_marks, doc_id):
    """Remove header/footer and in-table lines; build table records with titles/footnotes.

    Returns (tables, body_sizes); body font sizes are measured on non-table text only.
    """
    flats = []
    for p in pages:
        flat = []
        for bi, b in enumerate(p["blocks"]):
            for li, ln in enumerate(b["lines"]):
                if (p["index"], bi, li) not in hf_marks:
                    flat.append((bi, ln))
        dropped = _merge_lone_bullets([ln for _, ln in flat])
        consumed = set(dropped)
        raw = p.get("raw_tables", [])
        for k, (bi, ln) in enumerate(flat):
            c = center(ln["bbox"])
            if any(contains_point(pad(t["bbox"], 2), c) for t in raw):
                consumed.add(k)
        flats.append((flat, consumed))

    body_sizes = seg.body_font_sizes([
        {"source": p["source"], "blocks": [{"lines": [ln for k, (_, ln) in enumerate(flat) if k not in consumed]}]}
        for p, (flat, consumed) in zip(pages, flats)
    ])

    tables = []
    for p, (flat, consumed) in zip(pages, flats):
        body = body_sizes.get(p["source"], body_sizes["text"])
        for t in p.get("raw_tables", []):
            remaining = [(k, ln) for k, (bi, ln) in enumerate(flat) if k not in consumed]
            lines = [ln for _, ln in remaining]
            title_idx, foot_idx = attach_context(t["bbox"], lines, body)
            title_lines = [lines[i] for i in title_idx]
            foot_lines = [lines[i] for i in foot_idx]
            for i in title_idx + foot_idx:
                consumed.add(remaining[i][0])
            rows = t["rows"]
            tables.append({
                "doc_id": doc_id,
                "title": " ".join(l["text"].strip() for l in title_lines) or None,
                "footnote": " ".join(l["text"].strip() for l in foot_lines) or None,
                "rows": rows,
                "n_rows": len(rows),
                "n_cols": max(len(r) for r in rows) if rows else 0,
                "method": t.get("method"),
                "source": p["source"],
                "ocr_conf": t.get("ocr_conf"),
                "parts": [{
                    "page": p["index"],
                    "bbox": [round(v, 2) for v in t["bbox"]],
                    "title_bbox": union(l["bbox"] for l in title_lines) if title_lines else None,
                    "footnote_bbox": union(l["bbox"] for l in foot_lines) if foot_lines else None,
                }],
            })
        new_blocks = {}
        for k, (bi, ln) in enumerate(flat):
            if k not in consumed:
                new_blocks.setdefault(bi, []).append(ln)
        p["blocks"] = [{"bbox": union(l["bbox"] for l in ls), "lines": ls} for _, ls in sorted(new_blocks.items())]
    return tables, body_sizes
