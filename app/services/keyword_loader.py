"""Parse keyword lists from .txt, .csv and .xlsx uploads."""
import csv
import io
import os

from .keyword_search import parse_keyword_text

ALLOWED = {".txt", ".csv", ".xlsx"}
HEADER_WORDS = {"keyword", "keywords", "term", "terms", "topic", "topics", "phrase", "phrases"}


class KeywordFileError(ValueError):
    pass


def _dedupe(items):
    seen, out = set(), []
    for k in items:
        k = " ".join(str(k).split()).strip(" ;,")
        if k and k.lower() not in seen and k.lower() not in HEADER_WORDS:
            seen.add(k.lower())
            out.append(k)
    return out


def load_keywords(filename, data):
    ext = os.path.splitext(filename.lower())[1]
    if ext not in ALLOWED:
        raise KeywordFileError("Keyword files must be .txt, .csv or .xlsx")
    if ext == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise KeywordFileError(f"Could not read Excel file: {exc}") from exc
        cells = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                cells += [str(c) for c in row if c is not None and str(c).strip()]
        return _dedupe(k for c in cells for k in parse_keyword_text(c))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if ext == ".csv":
        cells = [c for row in csv.reader(io.StringIO(text)) for c in row if c.strip()]
        return _dedupe(k for c in cells for k in parse_keyword_text(c))
    return _dedupe(parse_keyword_text(text))
