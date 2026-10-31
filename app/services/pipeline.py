"""End-to-end processing: analyse PDFs, score topics, build results and snapshots."""
import hashlib
import json
import logging
import os

import pymupdf as fitz

from .duplicate_detector import mark_duplicates
from .pdf_processor import analyze_document
from .semantic_search import SemanticEncoder
from .snapshot_extractor import finalize_regions, render_region, unit_regions
from .table_extractor import table_text
from .topic_engine import TopicEngine, default_settings, explain

log = logging.getLogger(__name__)
RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def effective_settings(user_settings):
    s = default_settings()
    s.update({k: v for k, v in (user_settings or {}).items() if v is not None or k.endswith("_threshold")})
    if not s.get("include_levels"):
        s["include_levels"] = ["HIGH", "MEDIUM"] if s["mode"] == "strict" else ["HIGH", "MEDIUM", "LOW"]
    if not s["semantic"] and not s["keyword"]:
        s["semantic"] = True
    return s


def _analysis_cached(path, doc_id, options, cache_dir, progress):
    key = hashlib.sha1(json.dumps([os.path.getsize(path), os.path.getmtime(path), options],
                                  sort_keys=True).encode()).hexdigest()[:12]
    cache = os.path.join(cache_dir, f"{doc_id}_{key}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    a = analyze_document(path, doc_id, options, progress=progress)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(a, fh)
    return a


def _unit_text(unit, els, tables):
    if unit["kind"] == "table":
        return table_text(tables[unit["table_id"]])
    parts = []
    for eid in unit["element_ids"]:
        e = els[eid]
        parts.append(table_text(tables[e["table_id"]]) if e["type"] == "table" and e.get("table_id") in tables
                     else e["text"])
    return "\n".join(parts)


def process_session(config, store, sid, progress, check_cancel):
    session = store.load(sid)
    docs = [d for d in session["documents"] if d.get("status") == "ready"]
    if not docs:
        raise ValueError("Upload at least one readable PDF before processing.")
    interests = session["interests"]
    if not (interests.get("topics") or interests.get("description") or interests.get("keywords")):
        raise ValueError("Enter at least one topic, a topic description or keywords.")
    settings = effective_settings(session.get("settings"))
    options = {"ocr": settings["ocr"], "tables": settings["tables"], "ocr_dpi": config["OCR_DPI"],
               "ocr_lang": config["OCR_LANG"], "ocr_low_conf": config["OCR_LOW_CONFIDENCE"],
               "use_camelot": config["USE_CAMELOT"]}
    analysis_dir = os.path.join(store.session_dir(sid), "analysis")
    snap_dir = store.snapshot_dir(sid)
    os.makedirs(analysis_dir, exist_ok=True)
    os.makedirs(snap_dir, exist_ok=True)
    for f in os.listdir(snap_dir):
        os.remove(os.path.join(snap_dir, f))

    total_pages = max(1, sum(d["pages"] for d in docs))
    done_pages = 0
    analyses, doc_reports = {}, {}
    for d in docs:
        check_cancel()
        base = done_pages

        def cb(i, n, _d=d, _base=base):
            check_cancel()
            progress(3 + 62 * (_base + i) / total_pages, "Analysing documents",
                     f"{_d['filename']}: page {i} of {n}")

        progress(3 + 62 * base / total_pages, "Analysing documents", f"Opening {d['filename']}",
                 f"Analysing {d['filename']} ({d['pages']} pages)")
        a = _analysis_cached(d["path"], d["id"], options, analysis_dir, cb)
        analyses[d["id"]] = a
        ocr_pages = [p["index"] + 1 for p in a["pages"] if p.get("source") == "ocr"]
        doc_reports[d["id"]] = {"warnings": a["warnings"], "ocr_pages": ocr_pages,
                                "tables": len(a["tables"]), "elements": len(a["elements"])}
        for w in a["warnings"]:
            progress(log_line=f"{d['filename']} p.{w['page']}: {w['message']}")
        done_pages += d["pages"]

    check_cancel()
    progress(66, "Understanding topics", "Expanding topics with related concepts",
             "Building topic queries (topics, description, keywords, related concepts)")
    encoder = SemanticEncoder(config["EMBEDDING_MODEL"], config["CACHE_DIR"],
                              batch_size=config.get("EMBEDDING_BATCH_SIZE", 64))
    engine = TopicEngine(encoder, settings)
    corpus = [e["text"] for a in analyses.values() for e in a["elements"] if e["type"] != "table"]
    queries = engine.build_queries(interests.get("topics", []), interests.get("description", ""),
                                   interests.get("keywords", []), corpus)
    topic_order = {q.name: i for i, q in enumerate(queries)}

    units = []
    for n, d in enumerate(docs):
        check_cancel()
        progress(68 + 14 * n / len(docs), "Searching for relevant content", f"Scoring {d['filename']}")
        a = analyses[d["id"]]
        scored, _, _ = engine.score_document(a, queries)
        for q in queries:
            found = []
            if settings["paragraphs"]:
                found += engine.build_units(a, q.name, scored[q.name])
            if settings["tables"]:
                found += engine.table_units(a, q.name, scored[q.name])
            found = [u for u in found if RANK.get(u["level"], 0) >= 1]
            units += [(d, u) for u in found]
            progress(log_line=f"{d['filename']}: {len(found)} candidate extract(s) for '{q.name}'")

    progress(83, "Extracting snapshots", "Locating content regions")
    results = []
    open_docs = {}
    try:
        for d, u in units:
            a = analyses[d["id"]]
            els = {e["id"]: e for e in a["elements"]}
            tables = {t["id"]: t for t in a["tables"]}
            if d["id"] not in open_docs:
                open_docs[d["id"]] = fitz.open(d["path"])
            regions = finalize_regions(unit_regions(u, els, tables), open_docs[d["id"]])
            if not regions:
                continue
            rid = f"r{len(results)}"
            res = {
                "id": rid, "topic": u["topic"], "kind": u["kind"], "doc_id": d["id"], "filename": d["filename"],
                "pages": sorted({p + 1 for p in u["pages"]} | {g["page"] + 1 for g in regions}),
                "level": u["level"], "score": round(float(u["score"]), 4), "section": u.get("section"),
                "matched_keywords": u.get("matched_keywords", []),
                "concept": (u.get("evidence") or {}).get("concept"),
                "reasons": explain(u, u["topic"]), "flags": list(u.get("flags", [])),
                "regions": regions, "text": _unit_text(u, els, tables),
                "included": u["level"] in settings["include_levels"],
                "first_order": u.get("first_order", 0), "tags": u.get("tags", []),
            }
            if u["kind"] == "table":
                t = tables[u["table_id"]]
                res["table"] = {"title": t.get("title"), "rows": t.get("n_rows", len(t["rows"])),
                                "cols": t.get("n_cols"), "pages": sorted({p["page"] + 1 for p in t["parts"]}),
                                "source": t.get("source", "")}
            low_conf = [e for e in (els[i] for i in u["element_ids"]) if e.get("low_conf")]
            if low_conf and "low_ocr_confidence" not in res["flags"]:
                res["flags"].append("low_ocr_confidence")
            results.append(res)

        mark_duplicates(results, topic_order)

        for n, r in enumerate(results):
            check_cancel()
            if n % 3 == 0:
                progress(85 + 14 * n / max(1, len(results)), "Extracting snapshots",
                         f"Rendering snapshot {n + 1} of {len(results)}")
            for i, g in enumerate(r["regions"]):
                g.update(render_region(open_docs[r["doc_id"]], g, config["SNAPSHOT_DPI"],
                                       os.path.join(snap_dir, f"{r['id']}_{i}.png")))
    finally:
        for fd in open_docs.values():
            fd.close()

    results.sort(key=lambda r: (topic_order.get(r["topic"], 99), [x["id"] for x in docs].index(r["doc_id"]),
                                r["pages"][0], r["first_order"]))
    progress(99, "Finalising", f"{len(results)} extract(s) ready for review",
             f"Found {len(results)} extract(s); {sum(r['included'] for r in results)} pre-selected")

    def put(s):
        s["results"] = results
        s["topics_run"] = [q.name for q in queries]
        s["queries"] = [q.to_dict() for q in queries]
        s["run_settings"] = settings
        s["output"] = None
        for doc in s["documents"]:
            if doc["id"] in doc_reports:
                doc["report"] = doc_reports[doc["id"]]
    store.update(sid, put)
    return results
