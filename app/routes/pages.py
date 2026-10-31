from flask import Blueprint, current_app, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def index():
    return render_template("index.html", max_mb=current_app.config["MAX_FILE_SIZE_MB"],
                           max_files=current_app.config["MAX_FILES_PER_SESSION"])


@pages_bp.get("/health")
def health():
    return {"status": "ok"}
