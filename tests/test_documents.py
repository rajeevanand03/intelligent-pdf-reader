"""PDF validation, layout analysis, table extraction and OCR."""
import os
import shutil

import pytest

from app.services.pdf_processor import PDFError, analyze_document, inspect_pdf
from app.services.table_extractor import row_texts

OPTS = {"ocr": True, "tables": True, "ocr_dpi": 300, "ocr_lang": "eng", "ocr_low_conf": 60, "use_camelot": False}


@pytest.fixture(scope="module")
def main_analysis(fixtures):
    return analyze_document(os.path.join(fixtures, "annual_operations_review.pdf"), "d0", OPTS)


def test_inspect_valid(fixtures):
    assert inspect_pdf(os.path.join(fixtures, "annual_operations_review.pdf"))["page_count"] == 8


@pytest.mark.parametrize("name,code", [("corrupted.pdf", "corrupted"), ("not_a_pdf.pdf", "not_pdf"),
                                       ("protected_report.pdf", "password_required")])
def test_inspect_rejects_bad_files(fixtures, name, code):
    with pytest.raises(PDFError) as exc:
        inspect_pdf(os.path.join(fixtures, name))
    assert exc.value.code == code


def test_password_protected_opens_with_password(fixtures):
    assert inspect_pdf(os.path.join(fixtures, "protected_report.pdf"), password="secret123")["page_count"] == 1
    with pytest.raises(PDFError):
        inspect_pdf(os.path.join(fixtures, "protected_report.pdf"), password="wrong")


def test_headings_and_sections(main_analysis):
    heads = [e["text"] for e in main_analysis["elements"] if e["type"] == "heading"]
    assert "3. Operational Risk Management" in heads
    assert "3.2 Case Study: Payment System Outage" in heads
    para = next(e for e in main_analysis["elements"] if e["text"].startswith("In March a server outage"))
    assert para["section_path"][-1] == "3.2 Case Study: Payment System Outage"


def test_headers_footers_removed(main_analysis):
    assert not any("Page " in e["text"] and len(e["text"]) < 20 for e in main_analysis["elements"])


def test_list_items_complete(main_analysis):
    items = [e["text"] for e in main_analysis["elements"] if e["type"] == "list_item"]
    assert any(t.startswith("•") and "maker-checker" in t for t in items)
    assert not any(t.strip() == "•" for t in items)


def test_multipage_tables_are_merged(main_analysis):
    tables = {t["title"].split(":")[0]: t for t in main_analysis["tables"] if t.get("title")}
    t5 = tables["Table 5"]
    assert sorted({p["page"] for p in t5["parts"]}) == [5, 6]
    assert t5["n_rows"] == 57  # header + 56 data rows, repeated header removed
    t2 = tables["Table 2"]
    assert len({p["page"] for p in t2["parts"]}) == 2
    assert t2["footnote"] and "taxonomy" in t2["footnote"]


def test_table_rows_keep_headers(main_analysis):
    t4 = next(t for t in main_analysis["tables"] if (t.get("title") or "").startswith("Table 4"))
    assert t4["rows"][0][0] == "Metric"
    assert any("Metric:" in r for r in row_texts(t4)[1:])


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_scanned_page_ocr_and_table(fixtures):
    a = analyze_document(os.path.join(fixtures, "scanned_incident_memo.pdf"), "d1", OPTS)
    assert a["pages"][0]["source"] == "ocr"
    text = " ".join(e["text"] for e in a["elements"])
    assert "Warehouse" in text and "incident" in text.lower()
    t = a["tables"][0]
    assert t["rows"][0] == ["Action", "Owner", "Due date"]
    assert len(t["rows"]) == 4 and all(len(r) == 3 for r in t["rows"])
    assert all(e.get("ocr_conf") is not None for e in a["elements"] if e["type"] != "table")
