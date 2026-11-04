"""Retrieval-recall sweep. No API calls.

Replays the corpus with the signatures Call A actually produced on the last live
run, assembles stories according to the GOLD labels, and asks one question per
post: was the story it should have matched present in the shortlist?

That isolates retrieval from judgment. Decision accuracy can never exceed this.
"""
import json
import sys

from newsai import lexical
from newsai import places
from newsai import store
from newsai.normalize import normalize_ar_key


def keyset(*values):
    out = set()
    for v in values:
        if not v:
            continue
        if isinstance(v, str):
            v = [v]
        for item in v:
            k = normalize_ar_key(str(item))
            if k:
                out.add(k)
    return out


def sweep(db, rid, labels, floor, cap, use_lex=True):
    con = store.connect(db)
    cls = {r["post_id"]: r for r in
           con.execute("SELECT * FROM classifications WHERE run_id=?", (rid,))}
    posts = {r["id"]: r for r in con.execute("SELECT * FROM posts")}
    order = sorted(cls, key=lambda p: posts[p]["seq"] if p in posts else 0)

    stories = {}          # gold story id -> dict(entities, location, terms)
    owner = {}            # post id -> gold story id
    hit = miss = 0
    missed = []

    for pid in order:
        c = cls[pid]
        if c["class"] != "breaking":
            continue
        lab = labels.get(pid)
        if not lab:
            continue
        _, dec, of, _ = lab
        text = posts[pid]["text"] if pid in posts else ""
        p_ent = keyset(c["actor"], c["target"], json.loads(c["aliases"] or "[]"))
        p_loc = c["location"]
        p_terms = lexical.terms(text)

        bags = {sid: s["terms"] for sid, s in stories.items()}
        weights = lexical.idf(list(bags.values())) if bags else {}

        scored = []
        for sid, s in stories.items():
            ent = bool(p_ent & s["entities"])
            kind, _ = places.match(p_loc, s["location"])
            loc = kind in ("exact", "contains") or (kind == "weak" and ent)
            lex = lexical.score(p_terms, s["terms"], weights) if use_lex else 0.0
            if ent or loc or (use_lex and lex >= floor):
                scored.append(((1 if ent else 0, 1 if loc else 0, lex), sid))
        scored.sort(reverse=True)
        shortlist = {sid for _, sid in scored[:cap]}

        if of:
            want = owner.get(of)
            if want is not None:
                if want in shortlist: hit += 1
                else: miss += 1; missed.append(pid)

        # place the post into its GOLD story
        sid = owner.get(of) if of else None
        if sid is None:
            sid = len(stories) + 1
            stories[sid] = {"entities": set(), "location": p_loc, "terms": set()}
        owner[pid] = sid
        stories[sid]["entities"] |= p_ent
        stories[sid]["terms"] |= p_terms
        if not stories[sid]["location"]:
            stories[sid]["location"] = p_loc

    return hit, miss, missed


if __name__ == "__main__":
    sys.path.insert(0, "/tmp/claude-1000/-home-daniel-projects-ai-news-aggregator/a8528e8f-922b-4a4b-a53d-dbea19cbe63f/scratchpad")
    from labels import L
    db, rid = "live.db", "run-20260825T105215+0000"

    h, m, _ = sweep(db, rid, L, 0, 8, use_lex=False)
    print(f"baseline (entity+location only, cap 8):  {h}/{h+m}  ({100*h//(h+m)}%)")
    print()
    print(" floor  cap   recall")
    for cap in (5, 8, 12):
        for floor in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25):
            h, m, _ = sweep(db, rid, L, floor, cap)
            print(f"  {floor:.2f}  {cap:3}   {h}/{h+m}  ({100*h//(h+m)}%)")
        print()
