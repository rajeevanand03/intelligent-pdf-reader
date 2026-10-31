"""Mandatory acceptance test: full HTTP workflow on the sample documents."""
import os

import pymupdf as fitz
import pytest

from tests.conftest import run_job, upload


@pytest.fixture(scope="module")
def processed(app, fixtures):
    c = app.test_client()
    sid = c.post("/api/sessions").get_json()["id"]
    for name in ("annual_operations_review.pdf", "data_office_bulletin.pdf", "scanned_incident_memo.pdf"):
        assert upload(c, sid, os.path.join(fixtures, name)).status_code == 201
    r = c.put(f"/api/sessions/{sid}/interests",
              json={"topics": ["Operational Risk", "Data Quality", "Artificial Intelligence"], "keywords": []})
    assert r.status_code == 200
    c.put(f"/api/sessions/{sid}/settings", json={"mode": "strict"})
    job = run_job(c, sid)
    assert job["status"] == "done", job
    data = c.get(f"/api/sessions/{sid}/results").get_json()
    return c, sid, data["results"]


def by_topic(results, topic, included=True):
    return [r for r in results if r["topic"] == topic and (r["included"] or not included)]


def test_direct_discussion_found(processed):
    _, _, res = processed
    texts = " ".join(r["text"] for r in by_topic(res, "Operational Risk"))
    assert "Operational risk is the risk of loss" in texts


def test_semantic_match_without_exact_terms(processed):
    _, _, res = processed
    case = [r for r in by_topic(res, "Operational Risk") if "server outage" in r["text"]]
    assert case, "case study without the words 'operational risk' should be found"
    assert "operational risk" not in case[0]["text"].lower()
    ml = [r for r in by_topic(res, "Artificial Intelligence") if "machine learning model" in r["text"]]
    assert ml


def test_logical_unit_is_complete(processed):
    _, _, res = processed
    unit = next(r for r in by_topic(res, "Operational Risk") if r["section"] == "3.1 Process Failures and Control Weaknesses")
    for phrase in ("Several process breakdowns", "maker-checker", "Insufficient training", "Root cause analysis"):
        assert phrase in unit["text"]
    assert len(unit["regions"]) == 1  # one contiguous snapshot, not fragments


def test_complete_table_when_only_one_cell_matches(processed):
    _, _, res = processed
    t = next(r for r in by_topic(res, "Data Quality") if r["kind"] == "table")
    assert t["table"]["title"].startswith("Table 4")
    assert t["table"]["rows"] >= 5 and t["table"]["cols"] == 5
    assert "Data quality exceptions" in t["text"] and "Platform uptime" in t["text"]


def test_multipage_table_extracted_whole(processed):
    _, _, res = processed
    t = next(r for r in res if r["kind"] == "table" and (r["table"]["title"] or "").startswith("Table 5"))
    assert t["table"]["rows"] == 57 and t["table"]["pages"] == [6, 7]
    assert {g["page"] for g in t["regions"]} == {5, 6}


def test_unrelated_content_excluded(processed):
    _, _, res = processed
    inc = " ".join(r["text"] for r in res if r["included"])
    for unrelated in ("yoga", "cafeteria", "Marketing Budget", "Wellness Participation", "brand awareness"):
        assert unrelated not in inc


def test_scanned_document_content_found(processed):
    _, _, res = processed
    scan = [r for r in by_topic(res, "Operational Risk") if r["filename"] == "scanned_incident_memo.pdf"]
    assert any(r["kind"] == "table" and r["table"]["rows"] == 4 for r in scan)


def test_duplicates_detected(processed):
    _, _, res = processed
    dup = [r for r in res if r.get("duplicate_of")]
    assert any("Appendix" in (r["section"] or "") for r in dup)
    assert all(not r["included"] for r in dup)


def test_snapshots_served(processed):
    c, sid, res = processed
    g = res[0]["regions"][0]
    r = c.get(f"/api/sessions/{sid}/snapshots/{g['file']}")
    assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get(f"/api/sessions/{sid}/snapshots/..%2Fsession.json").status_code in (400, 404)


def test_review_toggle_and_generate(processed):
    c, sid, res = processed
    target = next(r for r in res if r["included"] and r["topic"] == "Data Quality" and r["kind"] == "section")
    assert c.patch(f"/api/sessions/{sid}/results/{target['id']}", json={"included": False}).get_json()["included"] is False
    out = c.post(f"/api/sessions/{sid}/generate", json={"title": "Acceptance Extract"})
    assert out.status_code == 200, out.get_json()
    pdf = c.get(f"/api/sessions/{sid}/download")
    assert pdf.status_code == 200 and pdf.data[:5] == b"%PDF-"
    doc = fitz.open(stream=pdf.data, filetype="pdf")
    cover = doc[0].get_text()
    assert "Acceptance Extract" in cover and "Source documents" in cover and "Operational Risk" in cover
    body = "".join(p.get_text() for p in doc)
    assert "Source: annual_operations_review.pdf" in body and "Source: scanned_incident_memo.pdf" in body
    assert f"Page {doc.page_count} of {doc.page_count}" in doc[-1].get_text()
    assert len(doc[1].get_images()) > 0
    assert doc.get_toc()[0][1] == "Operational Risk"
    c.patch(f"/api/sessions/{sid}/results/{target['id']}", json={"included": True})


def test_upload_validation(client, fixtures):
    sid = client.post("/api/sessions").get_json()["id"]
    r = upload(client, sid, os.path.join(fixtures, "sample_keywords.txt"))
    assert r.status_code == 415
    assert upload(client, sid, os.path.join(fixtures, "corrupted.pdf")).status_code == 422
    assert upload(client, sid, os.path.join(fixtures, "not_a_pdf.pdf")).status_code == 422
    locked = upload(client, sid, os.path.join(fixtures, "protected_report.pdf")).get_json()
    assert locked["status"] == "locked"
    bad = client.post(f"/api/sessions/{sid}/documents/{locked['id']}/unlock", json={"password": "nope"})
    assert bad.status_code == 422
    ok = client.post(f"/api/sessions/{sid}/documents/{locked['id']}/unlock", json={"password": "secret123"})
    assert ok.get_json()["status"] == "ready"
    assert client.delete(f"/api/sessions/{sid}/documents/{locked['id']}").get_json()["remaining"] == 0


def test_processing_requires_inputs(client, fixtures):
    sid = client.post("/api/sessions").get_json()["id"]
    assert client.post(f"/api/sessions/{sid}/process").status_code == 400
    upload(client, sid, os.path.join(fixtures, "data_office_bulletin.pdf"))
    assert client.post(f"/api/sessions/{sid}/process").status_code == 400
    assert client.get("/api/sessions/" + "0" * 32).status_code == 404


def test_keyword_only_search(client, fixtures):
    sid = client.post("/api/sessions").get_json()["id"]
    upload(client, sid, os.path.join(fixtures, "annual_operations_review.pdf"))
    with open(os.path.join(fixtures, "sample_keywords.csv"), "rb") as fh:
        kws = client.post(f"/api/sessions/{sid}/keywords/file", data={"file": (fh, "k.csv")},
                          content_type="multipart/form-data").get_json()["keywords"]
    client.put(f"/api/sessions/{sid}/interests", json={"topics": [], "keywords": kws})
    assert run_job(client, sid)["status"] == "done"
    res = client.get(f"/api/sessions/{sid}/results").get_json()["results"]
    assert res and any("root cause" in [k.lower() for k in r["matched_keywords"]] for r in res)
