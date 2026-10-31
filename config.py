import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

    DATA_DIR = os.environ.get("IPR_DATA_DIR", os.path.join(BASE_DIR, "instance"))
    UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
    OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
    SESSION_DIR = os.path.join(DATA_DIR, "sessions")
    CACHE_DIR = os.path.join(DATA_DIR, "cache")

    MAX_FILE_SIZE_MB = int(os.environ.get("IPR_MAX_FILE_SIZE_MB", "200"))
    MAX_FILES_PER_SESSION = int(os.environ.get("IPR_MAX_FILES", "20"))
    MAX_CONTENT_LENGTH = MAX_FILE_SIZE_MB * 1024 * 1024 + 1024 * 1024
    MAX_KEYWORD_FILE_MB = 5

    EMBEDDING_MODEL = os.environ.get("IPR_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    EMBEDDING_BATCH_SIZE = 64

    SNAPSHOT_DPI = int(os.environ.get("IPR_SNAPSHOT_DPI", "200"))
    OCR_DPI = int(os.environ.get("IPR_OCR_DPI", "300"))
    OCR_LANG = os.environ.get("IPR_OCR_LANG", "eng")
    OCR_LOW_CONFIDENCE = 60

    WORKER_THREADS = int(os.environ.get("IPR_WORKERS", "2"))
    SESSION_TTL_HOURS = int(os.environ.get("IPR_SESSION_TTL_HOURS", "72"))

    # Use Camelot (lattice) as an additional table detector when pdfplumber and
    # PyMuPDF find no table on a page that contains ruling lines.
    USE_CAMELOT = os.environ.get("IPR_USE_CAMELOT", "1") == "1"


class TestConfig(Config):
    TESTING = True
