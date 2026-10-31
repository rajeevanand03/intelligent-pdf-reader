"""Tesseract OCR for scanned / image-based pages.

Produces the same block/line structure as the text extractor (coordinates mapped
back to PDF points) plus grid-detected table regions, so scanned pages flow through
the same segmentation and search pipeline as digital pages.
"""
import logging
import shutil
import statistics

import pymupdf as fitz
import numpy as np
from PIL import Image

from .geometry import contains_point, center

log = logging.getLogger(__name__)


class OCRError(Exception):
    pass


def tesseract_available():
    try:
        import pytesseract

        if shutil.which(pytesseract.pytesseract.tesseract_cmd) is None:
            return False
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _render(page, dpi):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _line_positions(mask_1d, min_fill):
    """Return centre positions of runs where the projection exceeds min_fill."""
    positions, start = [], None
    for i, v in enumerate(mask_1d):
        if v >= min_fill and start is None:
            start = i
        elif v < min_fill and start is not None:
            positions.append((start + i - 1) / 2.0)
            start = None
    if start is not None:
        positions.append((start + len(mask_1d) - 1) / 2.0)
    return positions


def detect_table_grids(gray):
    """Detect ruled tables in a page image. Returns [(x0, y0, x1, y1, col_xs, row_ys)] in pixels."""
    import cv2

    arr = np.array(gray)
    h, w = arr.shape
    bw = cv2.adaptiveThreshold(255 - arr, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 25), 1)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 60))))
    grid = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tables = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw < 0.2 * w or ch < 0.03 * h:
            continue
        region_h = horiz[y:y + ch, x:x + cw] > 0
        region_v = vert[y:y + ch, x:x + cw] > 0
        row_ys = _line_positions(region_h.sum(axis=1), 0.5 * cw)
        col_xs = _line_positions(region_v.sum(axis=0), 0.5 * ch)
        if len(row_ys) < 3 or len(col_xs) < 2:
            continue
        tables.append((x, y, x + cw, y + ch, [x + v for v in col_xs], [y + v for v in row_ys]))
    return tables


def _merge_close(values, tol):
    out = []
    for v in sorted(values):
        if out and v - out[-1] < tol:
            out[-1] = (out[-1] + v) / 2.0
        else:
            out.append(v)
    return out


def _ocr_cells(img, col_xs, row_ys, lang):
    """OCR every grid cell separately; ruling lines confuse full-page OCR inside tables."""
    import pytesseract
    from pytesseract import Output

    cells, confs = [], []
    for r in range(len(row_ys) - 1):
        row = []
        for c in range(len(col_xs) - 1):
            box = (int(col_xs[c]) + 5, int(row_ys[r]) + 5, int(col_xs[c + 1]) - 5, int(row_ys[r + 1]) - 5)
            text = ""
            if box[2] - box[0] > 6 and box[3] - box[1] > 6:
                crop = img.crop(box)
                if np.array(crop).min() < 128:
                    d = pytesseract.image_to_data(crop, lang=lang, output_type=Output.DICT, config="--psm 6")
                    toks = []
                    for t, cf in zip(d["text"], d["conf"]):
                        try:
                            cf = float(cf)
                        except (TypeError, ValueError):
                            cf = -1
                        if t.strip() and cf >= 0:
                            toks.append(t.strip())
                            confs.append(cf)
                    text = " ".join(toks)
            row.append(text)
        cells.append(row)
    return cells, confs


def ocr_page(page, dpi=300, lang="eng", low_conf=60, detect_tables=True):
    """OCR a page. Returns dict(blocks, tables, mean_conf, low_conf_lines, word_count)."""
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:  # pragma: no cover
        raise OCRError("pytesseract is not installed") from exc
    if not tesseract_available():
        raise OCRError("Tesseract OCR engine is not installed or not on PATH")

    img = _render(page, dpi)
    scale = 72.0 / dpi
    ox, oy = page.rect.x0, page.rect.y0
    try:
        data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT, config="--psm 3")
    except Exception as exc:
        raise OCRError(f"Tesseract failed: {exc}") from exc

    words = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not txt or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append({
            "text": txt,
            "conf": conf,
            "px": (x, y, x + w, y + h),
            "bbox": [ox + x * scale, oy + y * scale, ox + (x + w) * scale, oy + (y + h) * scale],
            "key": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
        })

    tables = []
    table_px = []
    if detect_tables and words:
        try:
            grids = detect_table_grids(img)
        except Exception as exc:  # pragma: no cover - opencv problems should not kill OCR
            log.warning("OCR table detection failed: %s", exc)
            grids = []
        for x0, y0, x1, y1, col_xs, row_ys in grids:
            col_xs = _merge_close(col_xs, 8)
            row_ys = _merge_close(row_ys, 8)
            n_rows, n_cols = len(row_ys) - 1, len(col_xs) - 1
            if n_rows < 1 or n_cols < 1:
                continue
            cells, confs = _ocr_cells(img, col_xs, row_ys, lang)
            for wd in words:
                if contains_point((x0, y0, x1, y1), center(wd["px"])):
                    wd["in_table"] = True
            if n_cols < 2 and n_rows < 2:
                continue
            tables.append({
                "bbox": [ox + x0 * scale, oy + y0 * scale, ox + x1 * scale, oy + y1 * scale],
                "rows": cells,
                "method": "ocr-grid",
                "ocr_conf": round(statistics.mean(confs), 1) if confs else None,
            })
            table_px.append((x0, y0, x1, y1))

    lines = {}
    for wd in words:
        if wd.get("in_table"):
            continue
        lines.setdefault(wd["key"], []).append(wd)

    blocks = {}
    low_lines = 0
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda w: w["bbox"][0])
        bbox = [min(w["bbox"][0] for w in ws), min(w["bbox"][1] for w in ws),
                max(w["bbox"][2] for w in ws), max(w["bbox"][3] for w in ws)]
        conf = statistics.mean(w["conf"] for w in ws)
        heights = [w["bbox"][3] - w["bbox"][1] for w in ws]
        line = {
            "bbox": bbox,
            "text": " ".join(w["text"] for w in ws),
            "size": round(statistics.median(heights) * 0.95, 1),
            "bold": False,
            "ocr_conf": round(conf, 1),
        }
        if conf < low_conf:
            line["low_conf"] = True
            low_lines += 1
        blocks.setdefault(key[:2], []).append(line)

    out_blocks = []
    for key in sorted(blocks):
        ls = blocks[key]
        out_blocks.append({
            "bbox": [min(l["bbox"][0] for l in ls), min(l["bbox"][1] for l in ls),
                     max(l["bbox"][2] for l in ls), max(l["bbox"][3] for l in ls)],
            "lines": ls,
        })

    mean_conf = round(statistics.mean(w["conf"] for w in words), 1) if words else None
    return {
        "blocks": out_blocks,
        "tables": tables,
        "mean_conf": mean_conf,
        "low_conf_lines": low_lines,
        "word_count": len(words),
    }
