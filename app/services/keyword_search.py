"""Lexical retrieval: exact phrase matching, partial (stem / prefix) matching and BM25."""
import re

import numpy as np
from rank_bm25 import BM25Okapi

STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or that the this to was were will with
which who whom into than then there these those their our we you your can could should would may might must not no""".split())


def stem(token):
    t = token.lower()
    for suf, rep in (("ies", "y"), ("ing", ""), ("edly", ""), ("ed", ""), ("es", ""), ("s", ""), ("ly", "")):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            if suf == "es" and not t.endswith(("ses", "xes", "zes", "ches", "shes")):
                return t[:-1]
            return t[: -len(suf)] + rep
    return t


def tokenize(text, drop_stopwords=True):
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(t) for t in toks if not (drop_stopwords and t in STOPWORDS)]


def parse_keyword_text(text):
    parts = re.split(r"[,;\n\r\t]+", text or "")
    seen, out = set(), []
    for p in parts:
        k = re.sub(r"\s+", " ", p).strip().strip("\"'")
        if k and k.lower() not in seen and len(k) <= 120:
            seen.add(k.lower())
            out.append(k)
    return out


def _shares_root(a, b):
    """True when two stems share a long common prefix (remediation ~ remediated)."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 6 and n >= 0.75 * min(len(a), len(b))


class KeywordMatcher:
    """Find exact and partial occurrences of keywords in text."""

    def __init__(self, keywords):
        self.keywords = [k for k in keywords if k.strip()]
        self._exact = {}
        self._stems = {}
        for k in self.keywords:
            words = re.findall(r"[A-Za-z0-9]+", k)
            if not words:
                continue
            pattern = r"\b" + r"[\s\-_/]+".join(re.escape(w) for w in words) + r"\b"
            self._exact[k] = re.compile(pattern, re.I)
            self._stems[k] = [stem(w) for w in words if w.lower() not in STOPWORDS] or [stem(w) for w in words]

    def match(self, text):
        exact, partial = [], []
        if not text:
            return exact, partial
        tokens = set(tokenize(text, drop_stopwords=False))
        for k, rx in self._exact.items():
            if rx.search(text):
                exact.append(k)
                continue
            stems = self._stems[k]
            if all(any(t == s or _shares_root(t, s) for t in tokens) for s in stems):
                partial.append(k)
        return exact, partial


class BM25Index:
    def __init__(self, texts):
        self.tokens = [tokenize(t) or ["_"] for t in texts]
        self.bm25 = BM25Okapi(self.tokens) if self.tokens else None

    def scores(self, query_terms):
        if not self.bm25:
            return np.zeros(0)
        q = []
        for term in query_terms:
            q.extend(tokenize(term))
        if not q:
            return np.zeros(len(self.tokens))
        s = np.asarray(self.bm25.get_scores(q), dtype=float)
        s[s < 0] = 0
        top = s.max() if len(s) else 0
        return s / top if top > 0 else s
