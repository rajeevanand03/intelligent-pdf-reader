FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    IPR_DATA_DIR=/data \
    HF_HOME=/data/huggingface \
    XDG_CACHE_HOME=/data/cache

WORKDIR /app

# Tesseract is required for image-only PDFs; Ghostscript enables Camelot's
# optional lattice table extraction.
RUN apt-get update \
    && apt-get install --no-install-recommends -y tesseract-ocr ghostscript \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY . .
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir /data \
    && chown -R appuser:appuser /app /data

USER appuser
VOLUME ["/data"]
EXPOSE 8000

CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8000", "app:create_app()"]
