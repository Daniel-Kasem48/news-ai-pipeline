"""Lexical candidate channel — the third way a post can reach an open story.

Entity and location matching both fail on a channel that publishes neither an
actor the other channel would recognise nor a location prefix. Measured on the
first live run: 70% of one channel's posts carried no location at all, aliases
were emitted on 1% of posts, and 17 of 54 posts that should have matched were
never shown the right story.

This module scores raw term overlap instead. Terms are weighted by inverse
document frequency computed over the open stories *in this run* — so a term that
shows up in half the stories counts for almost nothing, and a rare term counts
for a lot. The weighting is derived from the corpus at query time; nothing about
any specific word, place or person is written down here.
"""
import math
import re
from collections import Counter

from newsai.normalize import normalize_ar_key

# One character is never evidence, and a term that is only digits is a count,
# not a subject. Everything else is kept — no stopword list, because a fixed
# list is a claim about the language that idf makes for free from the data.
_MIN_LEN = 3


def terms(text: str) -> set:
    if not text:
        return set()
    key = normalize_ar_key(text)
    out = set()
    for t in re.split(r"\s+", key):
        t = t.strip()
        if len(t) < _MIN_LEN or t.isdigit():
            continue
        out.add(t)
    return out


def idf(doc_terms: list) -> dict:
    """Inverse document frequency over the open stories, recomputed per query.

    Corpus is tens of stories, so this is cheap; keeping it live means a term
    that becomes common mid-run (a story everyone is covering) decays on its own.
    """
    n = len(doc_terms) or 1
    df = Counter()
    for ts in doc_terms:
        df.update(ts)
    return {t: math.log(1 + n / c) for t, c in df.items()}


def score(post_terms: set, story_terms: set, weights: dict) -> float:
    """Weighted Jaccard-ish overlap, normalised so long stories do not dominate.

    Denominator is the post's own weight, not the union: a story that has
    accumulated twenty developments should not be penalised for being rich.
    """
    shared = post_terms & story_terms
    if not shared:
        return 0.0
    num = sum(weights.get(t, 1.0) for t in shared)
    den = sum(weights.get(t, 1.0) for t in post_terms) or 1.0
    return num / den
