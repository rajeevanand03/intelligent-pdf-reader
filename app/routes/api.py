"""JSON API used by the frontend."""
import os
import time
import uuid

from flask import Blueprint, current_app, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from ..services.keyword_loader import KeywordFileError, load_keywords
from ..services.keyword_search import parse_keyword_text
from ..services.pdf_generator import generate_pdf
from ..services.pdf_processor import PDFError, inspect_pdf, unlock_pdf
from ..services.pipeline import effective_settings, process_session
from ..services.session_store import SessionNotFound

api_bp = Blueprint("api", __name__)
LEVELS = ("HIGH", "MEDIUM", "LOW")


def store():
    return current_app.extensions["session_store"]


def jobs():
    return current_app.extensions["job_manager"]


def err(message, status=400, **extra):
    return jsonify(error=message, **extra), status


@api_bp.errorhandler(SessionNotFound)
def _missing(_e):
    return err("Session not found or expired. Start a new session.", 404)


def _require(sid):
    if not store().exists(sid):
        raise SessionNotFound(sid)


def _public_session(s):
    out = {k: v for k, v in s.items() if k not in ("results", "queries")}
    out["documents"] = [{k: v for k, v in d.items() if k != "path"} for d in s["documents"]]
    out["result_count"] = len(s.get("results", []))
    out["settings_effective"] = effective_settings(s.get("settings"))
    return out


def _guard_running(sid):
    if jobs().is_running(sid):
        return err("Processing is running. Wait for it to finish or cancel it first.", 409)
    return None


@api_bp.post("/sessions")
def create_session():
    sid = store().create()
    return jsonify(_public_session(store().load(sid))), 201


@api_bp.get("/sessions/<sid>")
def get_session(sid):
    _require(sid)
    s = store().load(sid)
    s["job"] = jobs().status(sid)
    return jsonify(_public_session(s))


@api_bp.delete("/sessions/<sid>")
def delete_session(sid):
    _require(sid)
    jobs().cancel(sid)
    store().delete(sid)
    return jsonify(ok=True)


# ---------------------------------------------------------------- documents
@api_bp.post("/sessions/<sid>/documents")
def upload_document(sid):
    _require(sid)
    if (r := _guard_running(sid)):
        return r
    f = request.files.get("file")
    if not f or not f.filename:
        return err("No file was uploaded.")
    name = secure_filename(f.filename) or "document.pdf"
    if not name.lower().endswith(".pdf"):
        return err(f"'{f.filename}' is not a PDF. Only .pdf files are supported.", 415, code="unsupported")
    cfg = current_app.config
    s = store().load(sid)
    if len(s["documents"]) >= cfg["MAX_FILES_PER_SESSION"]:
        return err(f"A session can hold at most {cfg['MAX_FILES_PER_SESSION']} documents.")
    doc_id = uuid.uuid4().hex[:10]
    path = os.path.join(store().upload_dir(sid), f"{doc_id}.pdf")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f.save(path)
    size = os.path.getsize(path)
    if size > cfg["MAX_FILE_SIZE_MB"] * 1024 * 1024:
        os.remove(path)
        return err(f"'{f.filename}' exceeds the {cfg['MAX_FILE_SIZE_MB']} MB limit.", 413)
    doc = {"id": doc_id, "filename": f.filename, "path": path, "size": size, "pages": 0,
           "status": "ready", "error": None, "encrypted": False, "uploaded": time.time()}
    try:
        info = inspect_pdf(path)
        doc["pages"] = info["page_count"]
    except PDFError as exc:
        if exc.code == "password_required":
            doc.update(status="locked", error=exc.message, encrypted=True)
        else:
            os.remove(path)
            return err(f"'{f.filename}': {exc.message}", 422, code=exc.code)

    def put(sess):
        sess["documents"].append(doc)
    store().update(sid, put)
    return jsonify({k: v for k, v in doc.items() if k != "path"}), 201


@api_bp.post("/sessions/<sid>/documents/<doc_id>/unlock")
def unlock_document(sid, doc_id):
    _require(sid)
    password = (request.get_json(silent=True) or {}).get("password", "")
    s = store().load(sid)
    doc = next((d for d in s["documents"] if d["id"] == doc_id), None)
    if not doc:
        return err("Document not found.", 404)
    if doc["status"] != "locked":
        return jsonify({k: v for k, v in doc.items() if k != "path"})
    if not password:
        return err("Enter the document password.")
    unlocked = doc["path"].replace(".pdf", "_unlocked.pdf")
    try:
        unlock_pdf(doc["path"], password, unlocked)
        info = inspect_pdf(unlocked)
    except PDFError as exc:
        return err(exc.message, 422, code=exc.code)

    def put(sess):
        for d in sess["documents"]:
            if d["id"] == doc_id:
                d.update(path=unlocked, pages=info["page_count"], status="ready", error=None)
                return {k: v for k, v in d.items() if k != "path"}
    return jsonify(store().update(sid, put))


@api_bp.delete("/sessions/<sid>/documents/<doc_id>")
def remove_document(sid, doc_id):
    _require(sid)
    if (r := _guard_running(sid)):
        return r

    def put(sess):
        keep = []
        for d in sess["documents"]:
            if d["id"] == doc_id:
                for p in {d["path"], d["path"].replace("_unlocked.pdf", ".pdf")}:
                    if os.path.exists(p):
                        os.remove(p)
            else:
                keep.append(d)
        sess["documents"] = keep
        sess["results"] = [r for r in sess.get("results", []) if r["doc_id"] != doc_id]
        return len(keep)
    remaining = store().update(sid, put)
    return jsonify(ok=True, remaining=remaining)


# ---------------------------------------------------------------- interests / settings
@api_bp.post("/sessions/<sid>/keywords/file")
def keyword_file(sid):
    _require(sid)
    f = request.files.get("file")
    if not f or not f.filename:
        return err("No keyword file uploaded.")
    data = f.read(current_app.config["MAX_KEYWORD_FILE_MB"] * 1024 * 1024 + 1)
    if len(data) > current_app.config["MAX_KEYWORD_FILE_MB"] * 1024 * 1024:
        return err("Keyword file is too large.", 413)
    try:
        keywords = load_keywords(f.filename, data)
    except KeywordFileError as exc:
        return err(str(exc), 415)
    return jsonify(keywords=keywords, count=len(keywords))


def _clean_list(values, limit):
    out, seen = [], set()
    for v in values or []:
        for k in parse_keyword_text(str(v)) if "," in str(v) else [str(v)]:
            k = " ".join(k.split())[:120]
            if k and k.lower() not in seen:
                seen.add(k.lower())
                out.append(k)
    return out[:limit]


@api_bp.put("/sessions/<sid>/interests")
def set_interests(sid):
    _require(sid)
    body = request.get_json(silent=True) or {}
    interests = {"topics": _clean_list(body.get("topics"), 20),
                 "description": str(body.get("description") or "")[:4000].strip(),
                 "keywords": _clean_list(body.get("keywords"), 2000)}

    def put(s):
        s["interests"] = interests
    store().update(sid, put)
    return jsonify(interests)


@api_bp.put("/sessions/<sid>/settings")
def set_settings(sid):
    _require(sid)
    body = request.get_json(silent=True) or {}
    s = {}
    for key in ("semantic", "keyword", "supporting_context", "tables", "paragraphs", "ocr"):
        if key in body:
            s[key] = bool(body[key])
    if body.get("mode") in ("strict", "exploratory"):
        s["mode"] = body["mode"]
    if body.get("min_level") in LEVELS:
        s["min_level"] = body["min_level"]
    if isinstance(body.get("include_levels"), list):
        s["include_levels"] = [lv for lv in LEVELS if lv in body["include_levels"]]
    for key in ("high_threshold", "medium_threshold"):
        if key in body:
            v = body[key]
            try:
                s[key] = None if v in (None, "") else min(0.95, max(0.05, float(v)))
            except (TypeError, ValueError):
                return err(f"{key} must be a number between 0.05 and 0.95")

    def put(sess):
        sess["settings"] = s
    store().update(sid, put)
    return jsonify(effective_settings(s))


# ---------------------------------------------------------------- processing
@api_bp.post("/sessions/<sid>/process")
def start_processing(sid):
    _require(sid)
    s = store().load(sid)
    if not any(d["status"] == "ready" for d in s["documents"]):
        return err("Upload at least one readable PDF first.")
    i = s["interests"]
    if not (i["topics"] or i["description"] or i["keywords"]):
        return err("Enter at least one topic, a description or keywords first.")
    config = {k: current_app.config[k] for k in (
        "OCR_DPI", "OCR_LANG", "OCR_LOW_CONFIDENCE", "USE_CAMELOT", "EMBEDDING_MODEL", "CACHE_DIR",
        "EMBEDDING_BATCH_SIZE", "SNAPSHOT_DPI")}
    st = store()
    try:
        state = jobs().start(sid, lambda _sid, progress, check: process_session(config, st, _sid, progress, check))
    except RuntimeError as exc:
        return err(str(exc), 409)
    return jsonify(state), 202


@api_bp.get("/sessions/<sid>/job")
def job_status(sid):
    _require(sid)
    return jsonify(jobs().status(sid))


@api_bp.post("/sessions/<sid>/job/cancel")
def cancel_job(sid):
    _require(sid)
    return jsonify(cancelled=jobs().cancel(sid))


# ---------------------------------------------------------------- review
def _result_view(r):
    return {k: v for k, v in r.items()}


@api_bp.get("/sessions/<sid>/results")
def results(sid):
    _require(sid)
    s = store().load(sid)
    return jsonify(results=[_result_view(r) for r in s.get("results", [])], topics=s.get("topics_run", []),
                   queries=s.get("queries", []))


@api_bp.patch("/sessions/<sid>/results/<rid>")
def toggle_result(sid, rid):
    _require(sid)
    body = request.get_json(silent=True) or {}
    if "included" not in body:
        return err("'included' is required")

    def put(s):
        for r in s.get("results", []):
            if r["id"] == rid:
                r["included"] = bool(body["included"])
                s["output"] = None
                return r
    r = store().update(sid, put)
    if r is None:
        return err("Result not found.", 404)
    return jsonify(id=r["id"], included=r["included"])


@api_bp.post("/sessions/<sid>/results/bulk")
def bulk_results(sid):
    _require(sid)
    body = request.get_json(silent=True) or {}
    included = bool(body.get("included"))
    ids = set(body.get("ids") or [])
    levels = set(body.get("levels") or [])
    topic = body.get("topic")

    def put(s):
        n = 0
        for r in s.get("results", []):
            if ids and r["id"] not in ids:
                continue
            if levels and r["level"] not in levels:
                continue
            if topic and r["topic"] != topic:
                continue
            if not ids and included and r.get("duplicate_of"):
                continue
            r["included"] = included
            n += 1
        s["output"] = None
        return n
    return jsonify(updated=store().update(sid, put))


@api_bp.get("/sessions/<sid>/snapshots/<path:name>")
def snapshot(sid, name):
    _require(sid)
    if "/" in name or not name.endswith(".png"):
        return err("Invalid snapshot name", 400)
    return send_from_directory(store().snapshot_dir(sid), name, max_age=3600)


# ---------------------------------------------------------------- output
@api_bp.post("/sessions/<sid>/generate")
def generate(sid):
    _require(sid)
    if (r := _guard_running(sid)):
        return r
    body = request.get_json(silent=True) or {}
    s = store().load(sid)
    if not any(r.get("included") for r in s.get("results", [])):
        return err("No extracts are selected. Include at least one result in the review step.")
    title = str(body.get("title") or "").strip()[:120]
    fname = secure_filename(title) or "focused_extract"
    out_path = os.path.join(store().output_dir(sid), f"{fname}.pdf")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    info = generate_pdf(s, out_path, store().snapshot_dir(sid), title)
    output = {"file": os.path.basename(out_path), "generated": time.time(), "title": title, **info}

    def put(sess):
        sess["output"] = output
    store().update(sid, put)
    return jsonify(output)


@api_bp.get("/sessions/<sid>/download")
def download(sid):
    _require(sid)
    s = store().load(sid)
    out = s.get("output")
    if not out:
        return err("Generate the PDF first.", 404)
    path = os.path.join(store().output_dir(sid), out["file"])
    if not os.path.exists(path):
        return err("The generated file is no longer available.", 404)
    inline = request.args.get("inline") == "1"
    return send_file(path, mimetype="application/pdf", as_attachment=not inline, download_name=out["file"])
