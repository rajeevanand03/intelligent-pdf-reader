# Intelligent PDF Reader

An interest-based document reader. Upload long PDFs, describe the topics you care about, and get a
**shorter, topic-focused PDF made of original snapshots** of the relevant discussions and tables, with every
extract labelled by source document and page. Content is **extracted, never rewritten or summarised**.

```
UNDERSTAND THE TOPIC -> IDENTIFY RELEVANT CONTENT -> EXTRACT ORIGINAL CONTENT -> GENERATE A FOCUSED PDF
```

## Features

- **Upload**: drag-and-drop multiple PDFs with upload progress, page counts, removal, type/size/corruption checks,
  password-protected PDFs (unlock in the UI), text-based and scanned documents.
- **Interests**: topics (editable tags), a free-text topic description, manual keywords, and keyword import from
  `.xlsx`, `.csv` or `.txt`. Keywords only *add* to topic-based retrieval.
- **Hybrid search**: local Sentence Transformers (`all-MiniLM-L6-v2`) cosine similarity + BM25 + exact and partial
  (stem/prefix) keyword matching + heading/section relevance + surrounding context. Topics are expanded with a
  local concept glossary (`app/data/topic_glossary.json`), description clauses and phrases mined from the documents.
  No external API is used.
- **Strict / Exploratory** modes, HIGH / MEDIUM / LOW relevance levels, automatic (document-adaptive) or manual
  thresholds.
- **Logical extraction**: relevant paragraphs grow into complete discussions — heading chain, lead-in sentences,
  whole bullet lists, examples/case studies, conclusions, continuing paragraphs across pages.
- **Complete tables**: pdfplumber / PyMuPDF / Camelot detection, title + footnote capture, multi-page continuation
  merging (repeated headers removed). A table matched through a single cell or row is always extracted whole.
- **OCR**: Tesseract for image-only pages, word boxes mapped to PDF coordinates, grid detection + per-cell OCR for
  scanned tables, low-confidence flags; OCR errors are reported, never silently dropped.
- **Snapshots**: high-resolution (200 DPI default) region crops of the original pages (not full pages), figures inside
  a discussion are included, overlapping regions merged.
- **Duplicate detection**: overlapping regions, the same content under several topics ("also relevant to"), and
  passages repeated elsewhere in the documents are excluded by default.
- **Review**: results grouped by topic with snapshots, reasons ("why this was selected"), matched keywords, flags
  (borderline relevance, uncertain boundary, low OCR confidence, duplicate), filters and include/exclude switches.
- **Output PDF**: cover page (title, date, sources, topics, keywords, page and extract counts), one chapter per topic,
  extracts in original reading order, source/page/section labels, bookmarks and page numbers.

## Requirements

- Python 3.10+
- Tesseract OCR (`sudo apt-get install tesseract-ocr` / `brew install tesseract`)
- Ghostscript is optional (only used by Camelot)

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
```

The embedding model (~90 MB) is downloaded from Hugging Face on first use and cached locally; afterwards the app
works offline. To pre-download: `python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/all-MiniLM-L6-v2')"`.

## Running

```bash
python run.py            # http://127.0.0.1:5000
HOST=0.0.0.0 PORT=8000 python run.py
```

For production use a WSGI server with a **single worker process** (jobs run in an in-process thread pool):
`gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 "app:create_app()"`.

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `IPR_DATA_DIR` | `./instance` | uploads, sessions, snapshots, outputs, embedding cache |
| `IPR_MAX_FILE_SIZE_MB` | 200 | per-file upload limit |
| `IPR_MAX_FILES` | 20 | documents per session |
| `IPR_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | any Sentence Transformers model |
| `IPR_SNAPSHOT_DPI` / `IPR_OCR_DPI` | 200 / 300 | rendering resolution |
| `IPR_OCR_LANG` | `eng` | Tesseract language(s), e.g. `eng+deu` |
| `IPR_WORKERS` | 2 | concurrent processing jobs |
| `IPR_SESSION_TTL_HOURS` | 72 | sessions older than this are deleted at startup |
| `IPR_USE_CAMELOT` | 1 | extra lattice table detector |
| `SECRET_KEY` | dev value | set in production |

## Workflow

1. **Upload** PDFs (try `tests/fixtures/generated/*.pdf`; run `python tests/fixtures/make_samples.py` to create them).
2. **Interests**: e.g. topics `Operational Risk`, `Data Quality`, `Artificial Intelligence`; optionally import
   `app/static/samples/sample_keywords.xlsx`.
3. **Settings**: Strict or Exploratory, levels to pre-select, extraction options.
4. **Process**: watch per-page progress and the processing log.
5. **Review**: inspect snapshots and reasons, include/exclude results.
6. **Generate** the PDF, preview it in the browser and download it.

## Architecture

```
app/
  routes/api.py              JSON API (sessions, documents, interests, settings, jobs, results, output)
  routes/pages.py            single-page UI
  services/pdf_processor.py  validation, page-by-page analysis, OCR fallback, table separation
  services/ocr_processor.py  Tesseract OCR, table grid detection, per-cell OCR
  services/table_extractor.py table detection, cleaning, context (title/footnote), multi-page merging
  services/content_segmenter.py reading order, columns, headers/footers, paragraphs, headings, sections
  services/keyword_search.py exact / partial matching, BM25
  services/semantic_search.py Sentence Transformers encoder with SQLite embedding cache
  services/topic_engine.py   topic expansion, hybrid scoring, adaptive thresholds, logical units, explanations
  services/snapshot_extractor.py regions -> high-resolution PNG snapshots
  services/duplicate_detector.py region / cross-topic / repeated-text duplicates
  services/pdf_generator.py  consolidated output PDF
  services/pipeline.py       orchestration of a processing run
  services/job_manager.py    background thread pool with progress + cancel
  services/session_store.py  file-backed sessions
```

Background jobs use an in-process `ThreadPoolExecutor` (no Redis/Celery needed). `JobManager.start()` is the only
entry point, so it can be swapped for Celery/RQ later.

## Tests

```bash
python -m pytest
```

The suite generates acceptance PDFs (unrelated sections, multi-paragraph and multi-page discussions, relevant and
unrelated tables, a 56-row table spanning two pages, a two-column page, a repeated glossary definition, a scanned
memo with a table, a password-protected copy, a corrupted file and a fake PDF) and checks exact matching, semantic
matching without exact terms, complete logical units, complete/multi-page tables, OCR, unrelated-content exclusion,
duplicate detection, snapshots, metadata, upload validation and the generated PDF.

## Limitations (Version 1)

- Relevance is computed with a small local embedding model; very domain-specific vocabularies benefit from a
  topic description, keywords or glossary entries.
- Table detection relies on ruling lines or clear column alignment; borderless, irregular tables may be reconstructed
  with less structure (they are still snapshotted as an image region).
- OCR quality depends on scan quality; low-confidence regions are flagged for review.
- Question answering, summaries, classification and comparison are intentionally out of scope.
