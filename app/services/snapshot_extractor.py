"""Turn logical result units into rectangular page regions and render them as images."""
import os

import pymupdf as fitz

from .geometry import h_overlap, intersection_area, area, union
from .table_extractor import snapshot_box

PAD_X, PAD_Y = 6.0, 4.0
MAX_GAP = 40.0  # vertical gap (pt) that still counts as one contiguous region


def _clip(box, rect):
    return [max(rect.x0, box[0]), max(rect.y0, box[1]), min(rect.x1, box[2]), min(rect.y1, box[3])]


def _pad(box, rect):
    return _clip([box[0] - PAD_X, box[1] - PAD_Y, box[2] + PAD_X, box[3] + PAD_Y], rect)


def _image_boxes(page):
    boxes = []
    try:
        for info in page.get_image_info():
            b = list(info["bbox"])
            if area(b) > 400:
                boxes.append(b)
    except Exception:  # pragma: no cover - defensive, malformed images
        pass
    return boxes


def unit_regions(unit, elements_by_id, tables_by_id):
    """Ordered list of {page, bbox, kind} regions (PDF points, unpadded) covering a unit."""
    regions = []
    if unit["kind"] == "table":
        t = tables_by_id[unit["table_id"]]
        return [{"page": p["page"], "bbox": snapshot_box(p), "kind": "table"} for p in t["parts"]]

    current = None
    for eid in unit["element_ids"]:
        e = elements_by_id[eid]
        if e["type"] == "table" and e.get("table_id") in tables_by_id:
            current = None
            for p in tables_by_id[e["table_id"]]["parts"]:
                regions.append({"page": p["page"], "bbox": snapshot_box(p), "kind": "table"})
            continue
        b = e["bbox"]
        if (current and current["page"] == e["page"] and current["column"] == e.get("column", 0)
                and b[1] - current["bbox"][3] <= MAX_GAP and b[1] >= current["bbox"][1] - 2):
            current["bbox"] = union([current["bbox"], b])
        else:
            current = {"page": e["page"], "bbox": list(b), "kind": "text", "column": e.get("column", 0)}
            regions.append(current)
    for r in regions:
        r.pop("column", None)
    return regions


def expand_with_figures(regions, doc):
    """Grow text regions to include images (figures/charts) lying inside their vertical span."""
    cache = {}
    for r in regions:
        if r["kind"] != "text":
            continue
        if r["page"] not in cache:
            cache[r["page"]] = _image_boxes(doc[r["page"]])
        for ib in cache[r["page"]]:
            cy = (ib[1] + ib[3]) / 2
            page_area = doc[r["page"]].rect.width * doc[r["page"]].rect.height
            if area(ib) > 0.7 * page_area:
                continue  # full-page scan background, not a figure
            if r["bbox"][1] <= cy <= r["bbox"][3] and h_overlap(ib, r["bbox"]) > 0:
                r["bbox"] = union([r["bbox"], ib])
    return regions


def merge_overlapping(regions):
    """Merge regions on the same page that overlap, so content is never snapshotted twice."""
    out = []
    for r in regions:
        for o in out:
            if o["page"] == r["page"] and intersection_area(o["bbox"], r["bbox"]) > 0.3 * min(area(o["bbox"]), area(r["bbox"])):
                o["bbox"] = union([o["bbox"], r["bbox"]])
                if r["kind"] == "table":
                    o["kind"] = "table"
                break
        else:
            out.append(dict(r))
    return out


def finalize_regions(regions, doc):
    out = []
    for r in merge_overlapping(expand_with_figures(regions, doc)):
        rect = doc[r["page"]].rect
        box = _pad(r["bbox"], rect)
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            continue
        out.append({"page": r["page"], "bbox": [round(v, 2) for v in box], "kind": r["kind"]})
    return out


def render_region(doc, region, dpi, out_path):
    page = doc[region["page"]]
    pix = page.get_pixmap(dpi=dpi, clip=fitz.Rect(region["bbox"]), alpha=False)
    pix.save(out_path)
    return {"file": os.path.basename(out_path), "px_width": pix.width, "px_height": pix.height}
