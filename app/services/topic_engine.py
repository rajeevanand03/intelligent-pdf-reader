"""Topic understanding and hybrid relevance scoring.

For every topic the engine builds a set of weighted query variants (topic name,
related concepts, the user's description and its clauses, assigned keywords and
document phrases), then scores each logical element with:

  * semantic similarity of the element and of an overlapping context window,
  * pseudo-relevance feedback (similarity to the strongest passages found),
  * heading / section relevance,
  * exact + partial keyword matches and BM25.

Thresholds adapt per document to its background similarity level, so a document
that is generally "close" to the topic does not flood the results. Elements are
then grown into complete logical discussions (see ``build_units``).
"""
import json
import os
import re
from collections import Counter

import numpy as np

from .keyword_search import STOPWORDS, BM25Index, KeywordMatcher, tokenize
from .semantic_search import cosine_matrix
from .table_extractor import row_texts, table_text

LEVELS = ["LOW", "MEDIUM", "HIGH"]
RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

MODE_THRESHOLDS = {
    "strict": {"high": 0.46, "medium": 0.36, "low": 0.29},
    "exploratory": {"high": 0.43, "medium": 0.32, "low": 0.25},
}
BACKGROUND_REF = 0.14
GLOSSARY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "topic_glossary.json")


def default_settings():
    return {
        "semantic": True,
        "keyword": True,
        "mode": "strict",
        "min_level": "MEDIUM",
        "supporting_context": True,
        "tables": True,
        "paragraphs": True,
        "ocr": True,
        "high_threshold": None,
        "medium_threshold": None,
    }


def load_glossary(path=GLOSSARY_PATH):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return {k.lower(): v for k, v in data.items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def glossary_concepts(topic, glossary):
    key = topic.lower().strip()
    if key in glossary:
        return list(glossary[key])
    toks = set(tokenize(key))
    best, best_overlap = None, 0.0
    for k in glossary:
        kt = set(tokenize(k))
        if not kt or not toks:
            continue
        overlap = len(kt & toks) / len(kt | toks)
        if overlap > best_overlap:
            best, best_overlap = k, overlap
    if best and best_overlap >= 0.66:
        return list(glossary[best])
    return []


def split_clauses(description):
    parts = re.split(r"[.;:\n]|,\s*|\band\b|\bor\b", description or "")
    out = []
    for p in parts:
        p = re.sub(r"^\s*(i want to|i would like to|show me|find|understand|how|what|which|everything)\b", "", p.strip(), flags=re.I)
        p = p.strip(" ,")
        if len(p.split()) >= 2:
            out.append(p)
    return out


def mine_phrases(texts, max_candidates=400):
    """Frequent 2-3 word phrases from the documents (candidate related concepts)."""
    counter = Counter()
    for t in texts:
        words = re.findall(r"[A-Za-z][A-Za-z\-]+", t.lower())
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or any(len(w) < 3 for w in gram):
                    continue
                counter[" ".join(gram)] += 1
    return [p for p, _ in counter.most_common(max_candidates)]


class TopicQuery:
    def __init__(self, name):
        self.name = name
        self.variants = []  # (text, weight, kind)
        self.keywords = []  # lexical terms (topic name + assigned user keywords)
        self.emb = None
        self.weights = None

    def add(self, text, weight, kind):
        if text and all(text.lower() != v[0].lower() for v in self.variants):
            self.variants.append((text, weight, kind))

    def to_dict(self):
        return {"name": self.name, "keywords": self.keywords,
                "concepts": [v[0] for v in self.variants if v[2] in ("concept", "mined")][:25],
                "uses_description": any(v[2] == "description" for v in self.variants)}


class TopicEngine:
    def __init__(self, encoder, settings=None, glossary=None):
        self.encoder = encoder
        self.settings = {**default_settings(), **(settings or {})}
        self.glossary = load_glossary() if glossary is None else glossary
        if not self.settings["semantic"] and not self.settings["keyword"]:
            self.settings["keyword"] = True

    # ------------------------------------------------------------------ queries
    def build_queries(self, topics, description, keywords, corpus_texts):
        topics = [t.strip() for t in topics if t and t.strip()]
        description = (description or "").strip()
        if not topics and description:
            topics = ["Custom interest"]
        if not topics and keywords:
            topics = ["Keywords"]
        queries = [TopicQuery(t) for t in topics]
        sem = self.settings["semantic"]

        for q in queries:
            if q.name not in ("Custom interest", "Keywords"):
                q.add(q.name, 1.0, "topic")
                q.keywords.append(q.name)
            for c in glossary_concepts(q.name, self.glossary):
                q.add(c, 0.92, "concept")

        if description:
            targets = queries
            if sem and len(queries) > 1:
                d_emb = self.encoder.encode([description])[0]
                t_emb = self.encoder.encode([q.name for q in queries])
                sims = t_emb @ d_emb
                targets = [q for q, s in zip(queries, sims) if s >= 0.3] or [queries[int(np.argmax(sims))]]
            for q in targets:
                q.add(description, 1.0, "description")
                for c in split_clauses(description):
                    q.add(c, 0.95, "description")

        if keywords:
            if sem and len(queries) > 1:
                k_emb = self.encoder.encode(keywords)
                centroids = []
                for q in queries:
                    e = self.encoder.encode([v[0] for v in q.variants] or [q.name])
                    centroids.append(e)
                for k, ke in zip(keywords, k_emb):
                    sims = [float(np.max(c @ ke)) for c in centroids]
                    best = int(np.argmax(sims))
                    owners = [queries[best]] if sims[best] >= 0.35 else queries
                    for q in owners:
                        q.keywords.append(k)
                        q.add(k, 0.88, "keyword")
            else:
                for q in queries:
                    for k in keywords:
                        q.keywords.append(k)
                        q.add(k, 0.88, "keyword")
            if queries and queries[0].name == "Keywords":
                queries[0].keywords = list(keywords)

        if sem and corpus_texts:
            phrases = mine_phrases(corpus_texts)
            if phrases:
                p_emb = self.encoder.encode(phrases)
                for q in queries:
                    seed = self.encoder.encode([v[0] for v in q.variants if v[2] in ("topic", "description")] or [q.name])
                    sims = (p_emb @ seed.T).max(axis=1)
                    for idx in np.argsort(-sims)[:8]:
                        if sims[idx] >= 0.62:
                            q.add(phrases[idx], 0.85, "mined")

        if sem:
            for q in queries:
                if not q.variants:
                    q.add(q.name, 1.0, "topic")
                q.emb = self.encoder.encode([v[0] for v in q.variants])
                q.weights = np.array([v[1] for v in q.variants], dtype=np.float32)
        return queries

    # ------------------------------------------------------------------ scoring
    @staticmethod
    def _sem(emb, q):
        if q.emb is None or len(emb) == 0:
            return np.zeros(len(emb)), np.zeros(len(emb), dtype=int)
        sims = cosine_matrix(emb, q.emb) * q.weights[None, :]
        return sims.max(axis=1), sims.argmax(axis=1)

    def thresholds(self, background):
        base = dict(MODE_THRESHOLDS.get(self.settings["mode"], MODE_THRESHOLDS["strict"]))
        if self.settings.get("high_threshold") is not None:
            base["high"] = float(self.settings["high_threshold"])
        if self.settings.get("medium_threshold") is not None:
            base["medium"] = float(self.settings["medium_threshold"])
            base["low"] = base["medium"] - 0.07
        shift = 0.5 * max(0.0, background - BACKGROUND_REF)
        return {k: round(min(0.95, v + shift), 4) for k, v in base.items()}

    @staticmethod
    def level_for(score, th):
        if score >= th["high"]:
            return "HIGH"
        if score >= th["medium"]:
            return "MEDIUM"
        if score >= th["low"]:
            return "LOW"
        return "NONE"

    def _combine(self, sem_core, head, lex, head_lex):
        """Weighted hybrid score. Heading relevance only counts above a floor, so a
        loosely related document title does not lift every element beneath it."""
        s = self.settings
        head = np.clip((np.asarray(head) - 0.35) / 0.5, 0.0, 1.0)
        if s["semantic"] and s["keyword"]:
            return 0.62 * sem_core + 0.20 * head + 0.18 * lex
        if s["semantic"]:
            return 0.78 * sem_core + 0.22 * head
        return 0.62 * lex + 0.2 * head_lex + 0.18 * np.minimum(lex, 1.0)

    def score_document(self, analysis, queries):
        """Score elements and tables of one analysed document for every topic."""
        els = [e for e in analysis["elements"] if e["type"] != "table"]
        tables = analysis["tables"]
        sem_on, kw_on = self.settings["semantic"], self.settings["keyword"]
        by_id = {e["id"]: e for e in els}
        index = {e["id"]: i for i, e in enumerate(els)}

        texts = [e["text"] for e in els]
        windows = []
        for i, e in enumerate(els):
            parts = [e["text"]]
            if e["type"] != "heading":
                if i > 0 and els[i - 1]["section_id"] == e["section_id"] and els[i - 1]["type"] != "heading":
                    parts.insert(0, els[i - 1]["text"])
                if i + 1 < len(els) and els[i + 1]["section_id"] == e["section_id"] and els[i + 1]["type"] != "heading":
                    parts.append(els[i + 1]["text"])
            windows.append(" ".join(parts))

        t_title = [(t.get("title") or "") + " " + " ".join(t["rows"][0] if t["rows"] else []) for t in tables]
        t_full = [table_text(t, max_rows=25) for t in tables]
        t_rows = [row_texts(t) for t in tables]

        emb = win_emb = tt_emb = tf_emb = None
        row_emb = []
        if sem_on:
            emb = self.encoder.encode(texts)
            win_emb = self.encoder.encode(windows)
            tt_emb = self.encoder.encode(t_title)
            tf_emb = self.encoder.encode(t_full)
            flat_rows = [r for rows in t_rows for r in rows]
            fr = self.encoder.encode(flat_rows) if flat_rows else np.zeros((0, 384))
            k = 0
            for rows in t_rows:
                row_emb.append(fr[k:k + len(rows)])
                k += len(rows)

        bm25 = BM25Index(texts + [table_text(t) for t in tables]) if kw_on else None
        results = {}
        for q in queries:
            matcher = KeywordMatcher(q.keywords)
            n = len(els)
            if sem_on:
                s_sem, s_arg = self._sem(emb, q)
                s_ctx, _ = self._sem(win_emb, q)
            else:
                s_sem = s_ctx = np.zeros(n)
                s_arg = np.zeros(n, dtype=int)

            lex = np.zeros(n)
            exact_hits, partial_hits = [[] for _ in range(n)], [[] for _ in range(n)]
            bm = bm25.scores(q.keywords) if (kw_on and bm25 and q.keywords) else np.zeros(n + len(tables))
            if kw_on:
                for i, e in enumerate(els):
                    ex, pa = matcher.match(e["text"])
                    exact_hits[i], partial_hits[i] = ex, pa
                    ks = 0.0
                    if ex:
                        ks = 1.0 if any(k.lower() == q.name.lower() for k in ex) else min(1.0, 0.75 + 0.1 * (len(ex) - 1))
                    elif pa:
                        ks = 0.45
                    lex[i] = max(ks, 0.7 * bm[i])

            heading_sem = {}
            heading_lex = {}
            for i, e in enumerate(els):
                if e["type"] == "heading":
                    heading_sem[e["id"]] = s_sem[i]
                    heading_lex[e["id"]] = lex[i]

            def head_score(e, table):
                vals, lvals = [], []
                ids = list(e["heading_ids"])
                if not table and e["type"] == "heading" and ids and ids[-1] == e["id"]:
                    ids = ids[:-1]
                for depth, hid in enumerate(reversed(ids)):
                    decay = 0.85 ** depth
                    vals.append(decay * heading_sem.get(hid, 0.0))
                    lvals.append(decay * heading_lex.get(hid, 0.0))
                return (max(vals) if vals else 0.0), (max(lvals) if lvals else 0.0)

            head = np.zeros(n)
            hlex = np.zeros(n)
            for i, e in enumerate(els):
                head[i], hlex[i] = head_score(e, False)

            sem_core = np.maximum(s_sem, np.minimum(0.92 * s_ctx, s_sem + 0.15))
            first = self._combine(sem_core, head, lex, hlex)
            body_mask = np.array([e["type"] != "heading" for e in els], dtype=bool)
            background = float(np.percentile(first[body_mask], 30)) if body_mask.any() else 0.0
            th = self.thresholds(background)

            # Pseudo-relevance feedback: learn the document's own vocabulary for the topic.
            s_prf = np.zeros(n)
            prf_vec = None
            if sem_on and body_mask.any():
                strong = [i for i in np.argsort(-first) if body_mask[i] and first[i] >= th["high"]][:5]
                if strong:
                    prf_vec = emb[strong].mean(axis=0)
                    prf_vec /= (np.linalg.norm(prf_vec) or 1.0)
                    s_prf = emb @ prf_vec
                    sem_core = np.maximum(sem_core, np.minimum(0.8 * s_prf, s_sem + 0.2))

            final = self._combine(sem_core, head, lex, hlex)
            el_scores = {}
            for i, e in enumerate(els):
                variant = q.variants[int(s_arg[i])][0] if sem_on and q.variants else None
                el_scores[e["id"]] = {
                    "score": round(float(final[i]), 4),
                    "level": self.level_for(final[i], th),
                    "semantic": round(float(s_sem[i]), 3),
                    "context": round(float(s_ctx[i]), 3),
                    "prf": round(float(s_prf[i]), 3),
                    "heading": round(float(head[i]), 3),
                    "lexical": round(float(lex[i]), 3),
                    "concept": variant,
                    "exact": exact_hits[i],
                    "partial": partial_hits[i],
                }

            tab_scores = {}
            for ti, t in enumerate(tables):
                ex, pa = matcher.match(table_text(t)) if kw_on else ([], [])
                if sem_on:
                    st, at = self._sem(tt_emb[ti:ti + 1], q)
                    sf, _ = self._sem(tf_emb[ti:ti + 1], q)
                    sr, ar = self._sem(row_emb[ti], q) if len(row_emb[ti]) else (np.zeros(1), np.zeros(1, dtype=int))
                    best_row = int(np.argmax(sr)) if len(sr) else 0
                    sem_val = max(float(st[0]), float(sf[0]), 0.95 * float(sr.max()))
                    if prf_vec is not None:
                        sem_val = max(sem_val, 0.8 * float(tf_emb[ti] @ prf_vec))
                    variant = q.variants[int(at[0])][0]
                else:
                    sem_val, best_row, variant, sr = 0.0, 0, None, np.zeros(1)
                ks = 0.0
                if ex:
                    ks = 1.0 if any(k.lower() == q.name.lower() for k in ex) else min(1.0, 0.75 + 0.1 * (len(ex) - 1))
                elif pa:
                    ks = 0.45
                lx = max(ks, 0.7 * float(bm[n + ti])) if kw_on else 0.0
                anchor = next((e for e in analysis["elements"] if e.get("table_id") == t["id"]), None)
                hs, hl = head_score(anchor, True) if anchor else (0.0, 0.0)
                sc = float(self._combine(np.array([sem_val]), np.array([hs]), np.array([lx]), np.array([hl]))[0])
                tab_scores[t["id"]] = {
                    "score": round(sc, 4), "level": self.level_for(sc, th), "semantic": round(sem_val, 3),
                    "heading": round(hs, 3), "lexical": round(lx, 3), "concept": variant, "exact": ex, "partial": pa,
                    "best_row": t["rows"][best_row] if t["rows"] and best_row < len(t["rows"]) else None,
                    "row_semantic": round(float(sr.max()) if len(sr) else 0.0, 3),
                }
            results[q.name] = {"elements": el_scores, "tables": tab_scores, "thresholds": th,
                               "background": round(background, 4)}
        return results, by_id, index

    # ---------------------------------------------------------- logical units
    def build_units(self, analysis, topic, scored):
        """Grow relevant elements into complete logical discussions for one topic."""
        s = self.settings
        th = scored["thresholds"]
        sc = scored["elements"]
        exploratory = s["mode"] == "exploratory"
        seed_rank = RANK["LOW"] if exploratory else RANK[s["min_level"]]
        support = s["supporting_context"]
        cont = th["low"] - (0.04 if exploratory else 0.0)
        max_gap = (1 if exploratory else 0) if support else 0

        seq = [e for e in analysis["elements"] if e["type"] != "table"]
        pos = {e["id"]: i for i, e in enumerate(seq)}
        runs, cur, gap, pending = [], None, [], []

        def close():
            nonlocal cur, gap
            if cur and any(RANK[sc[x["id"]]["level"]] >= seed_rank for x in cur):
                runs.append(cur)
            cur, gap = None, []

        for e in seq:
            if e["type"] == "heading":
                close()
                pending = []
                continue
            info = sc[e["id"]]
            is_seed = RANK[info["level"]] >= seed_rank
            is_support = support and info["score"] >= cont
            if is_seed or (cur is not None and is_support):
                if cur is None:
                    cur = list(pending) if support else []
                    pending = []
                elif gap:
                    if len(gap) <= max_gap:
                        cur.extend(gap)
                    else:
                        close()
                        cur = []
                    gap = []
                cur.append(e)
            elif is_support:
                pending.append(e)
                if cur is not None:
                    gap.append(e)
            else:
                pending = []
                if cur is not None:
                    gap.append(e)
                    if len(gap) > max_gap:
                        close()
        close()

        units = []
        for run in runs:
            members = {e["id"] for e in run}
            self._complete_lists(seq, pos, members)
            ordered = sorted((seq[pos[i]] for i in members), key=lambda e: e["order"])
            first = ordered[0]
            if s["paragraphs"]:
                for hid in self._heading_chain(seq, pos, first):
                    members.add(hid)
            if support:
                self._fill_gaps(seq, pos, members)
            ordered = sorted((seq[pos[i]] for i in members), key=lambda e: e["order"])
            body = [e for e in ordered if e["type"] != "heading"]
            seeds = [e for e in body if RANK[sc[e["id"]]["level"]] >= seed_rank] or body
            best = max(seeds, key=lambda e: sc[e["id"]]["score"])
            level = max((sc[e["id"]]["level"] for e in seeds), key=lambda l: RANK[l])
            flags = []
            nxt_i = pos[ordered[-1]["id"]] + 1
            if nxt_i < len(seq) and seq[nxt_i]["type"] != "heading" and seq[nxt_i]["id"] not in members \
                    and sc[seq[nxt_i]["id"]]["score"] >= cont - 0.04:
                flags.append("boundary_uncertain")
            best_score = sc[best["id"]]["score"]
            lvl_floor = th[level.lower()] if level != "NONE" else th["low"]
            if best_score - lvl_floor < 0.02:
                flags.append("borderline_relevance")
            if any(e.get("low_conf") for e in ordered):
                flags.append("low_ocr_confidence")
            units.append({
                "kind": "section",
                "topic": topic,
                "doc_id": first["doc_id"],
                "element_ids": [e["id"] for e in ordered],
                "pages": sorted({e["page"] for e in ordered}),
                "level": level,
                "score": best_score,
                "mean_score": round(float(np.mean([sc[e["id"]]["score"] for e in body])) if body else best_score, 4),
                "best_element": best["id"],
                "evidence": sc[best["id"]],
                "matched_keywords": sorted({k for e in ordered for k in sc[e["id"]]["exact"] + sc[e["id"]]["partial"]}),
                "section": ordered[0]["section_path"][-1] if ordered[0]["section_path"] else None,
                "tags": sorted({t for e in ordered for t in e.get("tags", [])}),
                "flags": flags,
                "seed_count": len([e for e in body if RANK[sc[e["id"]]["level"]] >= seed_rank]),
                "support_count": len([e for e in body if RANK[sc[e["id"]]["level"]] < seed_rank]),
                "first_order": first["order"],
            })
        return units

    @staticmethod
    def _fill_gaps(seq, pos, members, max_fill=4):
        """Include short runs of skipped elements between members of the same section so
        a discussion is never split into disconnected fragments."""
        idx = sorted(pos[m] for m in members)
        for a, b in zip(idx, idx[1:]):
            between = seq[a + 1:b]
            if not between or len(between) > max_fill:
                continue
            path = seq[b]["section_path"]
            if all(e["type"] != "heading" and e["section_path"] == path for e in between):
                members.update(e["id"] for e in between)

    @staticmethod
    def _complete_lists(seq, pos, members):
        """Keep bullet / numbered lists whole together with their lead-in sentence."""
        changed = True
        while changed:
            changed = False
            for mid in list(members):
                i = pos[mid]
                e = seq[i]
                neighbours = []
                if e["type"] == "list_item" or e["text"].rstrip().endswith(":"):
                    j = i + 1
                    while j < len(seq) and seq[j]["type"] == "list_item":
                        neighbours.append(seq[j]["id"])
                        j += 1
                if e["type"] == "list_item":
                    j = i - 1
                    while j >= 0 and seq[j]["type"] == "list_item":
                        neighbours.append(seq[j]["id"])
                        j -= 1
                    if j >= 0 and seq[j]["type"] in ("paragraph",) and seq[j]["text"].rstrip().endswith(":"):
                        neighbours.append(seq[j]["id"])
                for nid in neighbours:
                    if nid not in members:
                        members.add(nid)
                        changed = True

    @staticmethod
    def _heading_chain(seq, pos, first):
        """Section heading of the unit plus parent headings that directly precede it."""
        chain = []
        sid = first.get("section_id")
        if not sid or sid not in pos:
            return chain
        chain.append(sid)
        i = pos[sid] - 1
        while i >= 0 and seq[i]["type"] == "heading":
            chain.append(seq[i]["id"])
            i -= 1
        return chain

    def table_units(self, analysis, topic, scored):
        s = self.settings
        if not s["tables"]:
            return []
        exploratory = s["mode"] == "exploratory"
        min_rank = RANK["LOW"] if exploratory else RANK[s["min_level"]]
        th = scored["thresholds"]
        units = []
        for t in analysis["tables"]:
            info = scored["tables"][t["id"]]
            if RANK[info["level"]] < min_rank:
                continue
            flags = []
            if info["score"] - th[info["level"].lower()] < 0.02:
                flags.append("borderline_relevance")
            if t.get("source") == "ocr":
                flags.append("ocr_table")
                if (t.get("ocr_conf") or 100) < 60:
                    flags.append("low_ocr_confidence")
            anchor = next((e for e in analysis["elements"] if e.get("table_id") == t["id"]), None)
            units.append({
                "kind": "table",
                "topic": topic,
                "doc_id": t["doc_id"],
                "table_id": t["id"],
                "element_ids": [anchor["id"]] if anchor else [],
                "pages": sorted({p["page"] for p in t["parts"]}),
                "level": info["level"],
                "score": info["score"],
                "mean_score": info["score"],
                "evidence": info,
                "matched_keywords": sorted(set(info["exact"] + info["partial"])),
                "section": anchor["section_path"][-1] if anchor and anchor["section_path"] else None,
                "tags": sorted(set(anchor.get("tags", []))) if anchor else [],
                "flags": flags,
                "first_order": anchor["order"] if anchor else 0,
            })
        return units


def explain(unit, topic, doc_elements=None):
    """Human-readable reasons for selecting a unit."""
    ev = unit.get("evidence") or {}
    reasons = []
    if ev.get("exact"):
        reasons.append("Exact keyword match: " + ", ".join(f"'{k}'" for k in ev["exact"][:4]))
    elif ev.get("partial"):
        reasons.append("Partial keyword match: " + ", ".join(f"'{k}'" for k in ev["partial"][:4]))
    if ev.get("semantic", 0) >= 0.3:
        concept = ev.get("concept")
        extra = f" (closest concept: '{concept}')" if concept and concept.lower() != topic.lower() else ""
        reasons.append(f"Semantic similarity {ev['semantic']:.2f} to '{topic}'{extra}")
    if unit["kind"] == "table":
        if ev.get("best_row") and ev.get("row_semantic", 0) >= 0.35:
            reasons.append("Relevant row: " + " | ".join(c for c in ev["best_row"] if c)[:120])
        reasons.append("Complete table extracted (all rows, columns and headers)")
    if ev.get("context", 0) > ev.get("semantic", 0) + 0.05:
        reasons.append(f"Surrounding context is relevant ({ev['context']:.2f})")
    if ev.get("prf", 0) >= 0.5:
        reasons.append(f"Similar to the strongest '{topic}' passages in this document ({ev['prf']:.2f})")
    if ev.get("heading", 0) >= 0.35 and unit.get("section"):
        reasons.append(f"Within relevant section '{unit['section']}'")
    if unit["kind"] == "section" and unit.get("support_count"):
        reasons.append(f"Includes {unit['support_count']} supporting paragraph(s) continuing the discussion")
    if len(unit["pages"]) > 1:
        reasons.append(f"Discussion continues across pages {unit['pages'][0] + 1}-{unit['pages'][-1] + 1}")
    if "case_study" in unit.get("tags", []):
        reasons.append("Contains a case study / example")
    if "conclusion" in unit.get("tags", []):
        reasons.append("Contains conclusions / recommendations")
    return reasons or [f"Relevance score {unit['score']:.2f}"]
