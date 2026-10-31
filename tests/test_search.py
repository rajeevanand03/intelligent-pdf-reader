from app.services.keyword_search import BM25Index, KeywordMatcher, parse_keyword_text


def test_exact_and_partial_matching():
    m = KeywordMatcher(["internal control", "remediation", "data validation"])
    exact, partial = m.match("Internal-control weaknesses were remediated after data validations failed.")
    assert "internal control" in exact
    assert "remediation" in partial and "data validation" in partial


def test_no_false_keyword_match():
    exact, partial = KeywordMatcher(["incident"]).match("The cafeteria was renovated.")
    assert not exact and not partial


def test_parse_keyword_text():
    assert parse_keyword_text("a, b; c\nA") == ["a", "b", "c"]


def test_bm25_ranks_relevant_text_first():
    idx = BM25Index(["yoga classes and nutrition", "loss events from failed processes", "parking spaces"])
    s = idx.scores(["failed processes", "loss"])
    assert s.argmax() == 1 and s.max() == 1.0
