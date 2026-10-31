"""Detect overlapping or repeated extractions so the final PDF never shows content twice."""
import re

from .geometry import area, intersection_area

OVERLAP_DUP = 0.8
TEXT_DUP = 0.85


def _covered_fraction(regions, others):
    total = sum(area(r["bbox"]) for r in regions) or 1.0
    covered = 0.0
    for r in regions:
        covered += min(area(r["bbox"]), sum(intersection_area(r["bbox"], o["bbox"])
                                            for o in others if o["page"] == r["page"]))
    return covered / total


def _shingles(text, k=5):
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _containment(a, b):
    """Share of the smaller passage's shingles that also occur in the larger one."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _priority(r):
    rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(r["level"], 0)
    size = sum(area(g["bbox"]) for g in r["regions"])
    return (rank, r["score"], size)


def mark_duplicates(results, topic_order):
    """Annotate results in place.

    * Same topic, regions covered by a better result -> ``duplicate_of`` and excluded.
    * Different topics, same regions -> kept once (best topic); others excluded and
      the kept result lists ``also_relevant_to``.
    * Same text found at a different location (repeated passages) -> lower one excluded.
    """
    for r in results:
        r.setdefault("also_relevant_to", [])
        r["_sh"] = _shingles(r.get("text", ""))
    ranked = sorted(results, key=lambda r: (_priority(r), -topic_order.get(r["topic"], 0)), reverse=True)
    kept = []
    for r in ranked:
        for k in kept:
            if k["doc_id"] == r["doc_id"] and _covered_fraction(r["regions"], k["regions"]) >= OVERLAP_DUP:
                reason = "overlaps_result" if k["topic"] == r["topic"] else "same_content_other_topic"
                break
            if min(len(r["_sh"]), len(k["_sh"])) >= 8 and _containment(r["_sh"], k["_sh"]) >= TEXT_DUP:
                reason = "repeated_text"
                break
        else:
            kept.append(r)
            continue
        r["duplicate_of"] = k["id"]
        r["duplicate_reason"] = reason
        r["included"] = False
        r["flags"] = sorted(set(r["flags"]) | {"duplicate"})
        if k["topic"] != r["topic"] and r["topic"] not in k["also_relevant_to"]:
            k["also_relevant_to"].append(r["topic"])
    for r in results:
        r.pop("_sh", None)
    return results
