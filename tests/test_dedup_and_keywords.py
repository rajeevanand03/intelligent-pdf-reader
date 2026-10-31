import os

import pytest

from app.services.duplicate_detector import mark_duplicates
from app.services.keyword_loader import KeywordFileError, load_keywords


def _r(rid, topic, page, bbox, score=0.6, level="HIGH", text="", doc="d0"):
    return {"id": rid, "topic": topic, "doc_id": doc, "level": level, "score": score, "included": True, "flags": [],
            "regions": [{"page": page, "bbox": bbox, "kind": "text"}], "text": text}


def test_overlapping_regions_same_topic_marked_duplicate():
    a = _r("a", "T", 0, [50, 50, 500, 300], 0.8)
    b = _r("b", "T", 0, [55, 60, 495, 290], 0.5)
    mark_duplicates([a, b], {"T": 0})
    assert a["included"] and not b["included"] and b["duplicate_of"] == "a"


def test_cross_topic_duplicate_kept_once():
    a = _r("a", "Risk", 0, [50, 50, 500, 300], 0.8)
    b = _r("b", "Quality", 0, [50, 50, 500, 300], 0.6)
    mark_duplicates([a, b], {"Risk": 0, "Quality": 1})
    assert b["duplicate_reason"] == "same_content_other_topic"
    assert a["also_relevant_to"] == ["Quality"]


def test_repeated_text_elsewhere_is_duplicate():
    text = "Operational risk is the risk of loss resulting from inadequate or failed internal processes people and systems"
    a = _r("a", "T", 1, [50, 50, 500, 150], 0.8, text="Heading\n" + text)
    b = _r("b", "T", 7, [50, 50, 500, 150], 0.7, text="Glossary\n" + text)
    mark_duplicates([a, b], {"T": 0})
    assert b["duplicate_reason"] == "repeated_text"


def test_distinct_regions_not_duplicates():
    a = _r("a", "T", 0, [50, 50, 500, 200], text="alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu")
    b = _r("b", "T", 0, [50, 400, 500, 600], text="one two three four five six seven eight nine ten eleven twelve")
    mark_duplicates([a, b], {"T": 0})
    assert a["included"] and b["included"]


@pytest.mark.parametrize("name", ["sample_keywords.txt", "sample_keywords.csv", "sample_keywords.xlsx"])
def test_keyword_files(fixtures, name):
    with open(os.path.join(fixtures, name), "rb") as fh:
        kws = load_keywords(name, fh.read())
    assert "control weakness" in kws and "root cause" in kws
    assert "keyword" not in [k.lower() for k in kws]
    assert len(kws) == len({k.lower() for k in kws})


def test_keyword_file_rejects_other_types():
    with pytest.raises(KeywordFileError):
        load_keywords("evil.exe", b"MZ")
