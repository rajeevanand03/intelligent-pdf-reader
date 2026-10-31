"""Table detection and complete-table reconstruction.

Tables are detected independently from paragraphs using pdfplumber and PyMuPDF
(with Camelot lattice as a fallback for ruled pages neither library resolves).
Each detected table keeps its full grid (all rows, columns and headers); titles,
footnotes and multi-page continuations are attached afterwards so a relevant table
is always extracted as a whole.
"""
import logging
import re

from .geometry import h_overlap, iou, union

log = logging.getLogger(__name__)

CAPTION_RE = re.compile(r"^\s*(table|exhibit|schedule|appendix\s+table)\s*[\w.\-:]*", re.I)
FOOTNOTE_RE = re.compile(r"^\s*(note|notes|source|sources|\*|†|‡|\(\w\)|\d{1,2}\s)", re.I)
CONTINUED_RE = re.compile(r"\(?\s*cont(inued|'d|\.)?\s*\)?", re.I)


def _clean_cell(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_rows(rows):
    rows = [[_clean_cell(c) for c in (r or [])] for r in (rows or [])]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any(r[j] for r in rows)]
    return [[r[j] for j in keep] for r in rows]


def is_valid_table(rows, bbox=None, page_rect=None):
    if len(rows) < 2 or max(len(r) for r in rows) < 2:
        return False
    filled = sum(1 for r in rows for c in r if c)
    if filled < 4:
        return False
    if bbox and page_rect:
        # A "table" covering the whole page is almost always a page border.
        pw, ph = page_rect[2] - page_rect[0], page_rect[3] - page_rect[1]
        if (bbox[2] - bbox[0]) > 0.97 * pw and (bbox[3] - bbox[1]) > 0.95 * ph:
            return False
    return True


def _has_ruling_lines(fitz_page, minimum=8):
    try:
        drawings = fitz_page.get_drawings()
    except Exception:
        return False
    count = 0
    for d in drawings:
        for item in d.get("items", []):
            if item[0] in ("l", "re"):
                count += 1
                if count >= minimum:
                    return True
    return False


def _add_candidate(found, cand):
    for i, other in enumerate(found):
        if iou(other["bbox"], cand["bbox"]) > 0.3:
            if _cell_count(cand["rows"]) > _cell_count(other["rows"]):
                found[i] = cand
            return
    found.append(cand)


def _cell_count(rows):
    return sum(1 for r in rows for c in r if c)


def _camelot_tables(pdf_path, page_index, page_height):
    import camelot

    out = []
    tables = camelot.read_pdf(pdf_path, pages=str(page_index + 1), flavor="lattice")
    for t in tables:
        x0, y0, x1, y1 = t._bbox
        rows = clean_rows(t.df.values.tolist())
        out.append({"bbox": [x0, page_height - y1, x1, page_height - y0], "rows": rows, "method": "camelot"})
    return out


def extract_page_tables(plumber_page, fitz_page, page_index, pdf_path=None, use_camelot=False):
    """Detect tables on one digital page. Returns [{bbox, rows, method}]."""
    found = []
    page_rect = list(fitz_page.rect)

    if plumber_page is not None:
        try:
            settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
            for t in plumber_page.find_tables(table_settings=settings):
                rows = clean_rows(t.extract())
                bbox = [float(v) for v in t.bbox]
                if is_valid_table(rows, bbox, page_rect):
                    _add_candidate(found, {"bbox": bbox, "rows": rows, "method": "pdfplumber"})
        except Exception as exc:
            log.warning("pdfplumber table detection failed on page %s: %s", page_index + 1, exc)

    try:
        for t in fitz_page.find_tables().tables:
            rows = clean_rows(t.extract())
            bbox = [float(v) for v in t.bbox]
            if is_valid_table(rows, bbox, page_rect):
                _add_candidate(found, {"bbox": bbox, "rows": rows, "method": "pymupdf"})
    except Exception as exc:
        log.warning("PyMuPDF table detection failed on page %s: %s", page_index + 1, exc)

    if not found and use_camelot and pdf_path and _has_ruling_lines(fitz_page):
        try:
            for cand in _camelot_tables(pdf_path, page_index, fitz_page.rect.height):
                if is_valid_table(cand["rows"], cand["bbox"], page_rect):
                    _add_candidate(found, cand)
        except Exception as exc:
            log.info("Camelot fallback unavailable on page %s: %s", page_index + 1, exc)

    return found


def attach_context(table_bbox, lines, body_size):
    """Find a table title (caption) and footnotes among the page's remaining lines.

    Returns (title_lines, footnote_lines) as lists of indices into ``lines``.
    """
    title_idx, foot_idx = [], []
    above = sorted(
        (i for i, ln in enumerate(lines)
         if ln["bbox"][3] <= table_bbox[1] + 2 and ln["bbox"][3] >= table_bbox[1] - 60
         and h_overlap(ln["bbox"], table_bbox) > 0),
        key=lambda i: -lines[i]["bbox"][3],
    )
    for rank, i in enumerate(above[:3]):
        ln = lines[i]
        gap = table_bbox[1] - ln["bbox"][3]
        words = len(ln["text"].split())
        if CAPTION_RE.match(ln["text"]) or CONTINUED_RE.fullmatch(ln["text"].strip()):
            title_idx.append(i)
            break
        if rank == 0 and gap < 24 and words <= 16 and (ln.get("bold") or ln["size"] > body_size * 1.05):
            title_idx.append(i)
            break
    if not title_idx:
        below_caption = [i for i, ln in enumerate(lines)
                         if 0 <= ln["bbox"][1] - table_bbox[3] < 24 and CAPTION_RE.match(ln["text"])]
        title_idx.extend(below_caption[:1])

    cursor = table_bbox[3]
    for i in sorted((i for i, ln in enumerate(lines) if ln["bbox"][1] >= table_bbox[3] - 2),
                    key=lambda i: lines[i]["bbox"][1]):
        if i in title_idx:
            cursor = max(cursor, lines[i]["bbox"][3])
            continue
        ln = lines[i]
        if ln["bbox"][1] - cursor > 16 or len(foot_idx) >= 4:
            break
        if h_overlap(ln["bbox"], table_bbox) <= 0:
            continue
        small = ln["size"] < body_size * 0.95
        if small or FOOTNOTE_RE.match(ln["text"]):
            foot_idx.append(i)
            cursor = ln["bbox"][3]
        else:
            break
    return title_idx, foot_idx


def _columns_compatible(a, b):
    if a["n_cols"] == b["n_cols"]:
        return True
    ba, bb = a["parts"][-1]["bbox"], b["parts"][0]["bbox"]
    return abs(ba[0] - bb[0]) < 15 and abs(ba[2] - bb[2]) < 15


def merge_multipage_tables(tables, page_content_bounds):
    """Merge tables that continue across consecutive pages.

    ``page_content_bounds[page] = (first_content_y, last_content_y)`` for non-table,
    non-header/footer content, used to check a table ends / starts a page.
    """
    tables = sorted(tables, key=lambda t: (t["parts"][0]["page"], t["parts"][0]["bbox"][1]))
    merged = []
    for t in tables:
        prev = merged[-1] if merged else None
        if prev and t["parts"][0]["page"] == prev["parts"][-1]["page"] + 1 and _columns_compatible(prev, t):
            prev_part, first = prev["parts"][-1], t["parts"][0]
            _, last_y = page_content_bounds.get(prev_part["page"], (None, None))
            first_y, _ = page_content_bounds.get(first["page"], (None, None))
            ends_page = last_y is None or last_y <= prev_part["bbox"][3] + 2
            title_continues = (not t["title"]) or bool(CONTINUED_RE.search(t["title"] or "")) or (
                prev["title"] and t["title"] and prev["title"].split(":")[0] in t["title"])
            starts_page = first_y is None or first_y >= first["bbox"][1] - 2
            if ends_page and starts_page and title_continues:
                rows = t["rows"]
                if prev["rows"] and rows and rows[0] == prev["rows"][0]:
                    rows = rows[1:]  # repeated header row
                prev["rows"].extend(rows)
                prev["parts"].extend(t["parts"])
                prev["n_rows"] = len(prev["rows"])
                if t["footnote"]:
                    prev["footnote"] = ((prev["footnote"] or "") + " " + t["footnote"]).strip()
                continue
        merged.append(t)
    return merged


def table_text(table, max_rows=None):
    rows = table["rows"] if max_rows is None else table["rows"][:max_rows]
    parts = [table.get("title") or ""]
    parts += [" | ".join(c for c in r if c) for r in rows]
    if table.get("footnote"):
        parts.append(table["footnote"])
    return "\n".join(p for p in parts if p)


def row_texts(table):
    """Row strings prefixed with header labels, used for row-level semantic matching."""
    rows = table["rows"]
    if not rows:
        return []
    header = rows[0]
    out = [" ; ".join(c for c in header if c)]
    for r in rows[1:]:
        cells = [f"{h}: {c}" if h else c for h, c in zip(header, r) if c]
        out.append(" ; ".join(cells))
    return [o for o in out if o]


def snapshot_box(part):
    boxes = [part["bbox"]] + [b for b in (part.get("title_bbox"), part.get("footnote_bbox")) if b]
    return union(boxes)
