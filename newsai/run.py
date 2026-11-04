#!/usr/bin/env python3
"""Run the pipeline over the fixture set.

    python -m newsai.run                    # live, calls DeepSeek
    python -m newsai.run --oracle           # no API calls; uses fixture expectations
    python -m newsai.run --note "prompt v2" # label the run
    python -m newsai.run --limit 5          # first N posts

Every run writes a new run_id. Compare runs with report.py.
"""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from newsai import llm
from newsai import clock
from newsai import lexical
from newsai import places
from newsai import store
from newsai.adapters import assemble, clean_credit, strip_attribution
from newsai.normalize import normalize_ar_key

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "posts.jsonl"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_fixtures(limit=None, path=None):
    src = Path(path) if path else FIXTURES
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["seq"])
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------- candidates

def _keyset(*values):
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


# Widened from an implicit "everything that hit" to a ranked, capped shortlist.
# The first live run offered 1.9 candidates on average and missed the right story
# 17 times out of 54 — the model was being starved, not misjudging.
CANDIDATE_CAP = 8
LEX_FLOOR = 0.15          # calibrated: calibrate.py sweep, 98% retrieval recall
                          # at 2.9 candidates/post (baseline 81% at 1.4)


def find_candidates(con, run_id, sig, text="", now=None):
    """Candidate lookup.

    An empty field is UNKNOWN, never a wildcard. A candidate qualifies on a
    non-empty entity match, a location match, or enough weighted lexical overlap
    to be worth the model's attention (lexical.py).
    """
    post_entities = _keyset(sig.get("actor"), sig.get("target"), sig.get("aliases"))
    post_location = sig.get("location")

    rows = con.execute(
        "SELECT * FROM stories WHERE run_id=? AND closed_at IS NULL ORDER BY id", (run_id,)
    ).fetchall()

    # lifecycle: a story nobody has touched inside the window stops being a
    # candidate. Without timestamps clock.expired is always False, so this is a
    # no-op on an untimed corpus rather than a behaviour change.
    if now is not None:
        live = []
        for r in rows:
            first = clock.parse(r["first_seen_at"])
            last = clock.parse(r["last_seen_at"])
            if clock.expired(first, last, now):
                con.execute("UPDATE stories SET closed_at=? WHERE id=?",
                            (now.isoformat(), r["id"]))
            else:
                live.append(r)
        rows = live

    # story term bags: title plus every development summary, so a story stays
    # reachable by any of the acts already attached to it
    bags = {}
    for r in rows:
        blob = " ".join(filter(None, [
            r["title"], r["actor_norm"], r["location_norm"],
            *[d["summary"] or "" for d in con.execute(
                "SELECT summary FROM developments WHERE story_id=?", (r["id"],))],
        ]))
        bags[r["id"]] = lexical.terms(blob)
    weights = lexical.idf(list(bags.values()))
    post_terms = lexical.terms(text or " ".join(filter(None, [
        sig.get("actor"), sig.get("action"), sig.get("target")])))

    out = []
    for r in rows:
        story_entities = _keyset(r["actor_norm"], r["target_norm"],
                                 json.loads(r["aliases"] or "[]"))
        entity_hit = bool(post_entities & story_entities)

        # places.py: "exact"/"contains" stand alone; "weak" (siblings, or a place
        # vs its country) is context, not evidence, and needs an entity to agree
        loc_kind, loc_shared = places.match(post_location, r["location_norm"])
        location_hit = loc_kind in ("exact", "contains")
        if loc_kind == "weak" and entity_hit:
            location_hit = True

        lex = lexical.score(post_terms, bags[r["id"]], weights)
        if not (entity_hit or location_hit or lex >= LEX_FLOOR):
            continue
        near = clock.proximity(clock.parse(r["last_seen_at"]), now)

        devs = con.execute(
            "SELECT id, summary, action FROM developments WHERE story_id=? ORDER BY id", (r["id"],)
        ).fetchall()
        out.append({
            "story_id": r["id"],
            "title": r["title"],
            "actor": r["actor_norm"],
            "location": r["location_norm"],
            "matched_on": ", ".join(
                (["entity"] if entity_hit else [])
                + ([f"location {loc_kind} ({loc_shared})"] if loc_kind else [])
                + ([f"wording {lex:.2f}"] if lex >= LEX_FLOOR else [])
                + ([f"recent {near:.2f}"] if now is not None else [])
            ) or "none",
            # proximity ranks, never filters — a slow story is still the right
            # answer when the wording agrees
            "_rank": (1 if entity_hit else 0, 1 if location_hit else 0,
                      lex * (0.5 + near)),
            "developments": [
                {"development_id": d["id"], "summary": d["summary"], "action": d["action"]}
                for d in devs
            ],
            "sources": [
                s["channel"] for s in con.execute(
                    "SELECT DISTINCT p.channel FROM confirmations c "
                    "JOIN posts p ON p.id=c.post_id "
                    "JOIN developments d ON d.id=c.development_id "
                    "WHERE d.story_id=?", (r["id"],))
            ],
        })

    out.sort(key=lambda c: c["_rank"], reverse=True)
    dropped = max(0, len(out) - CANDIDATE_CAP)
    out = out[:CANDIDATE_CAP]
    for c in out:
        c.pop("_rank", None)
    if dropped:
        print(f"      (shortlist capped, {dropped} lower-ranked candidates not shown)")
    return out


# ---------------------------------------------------------------- call A / B

def call_a(post, oracle_row, oracle):
    if oracle:
        e = oracle_row["expect"]
        return {
            "class": e.get("class"), "reason": "oracle",
            "actor": e.get("actor"), "target": e.get("target"),
            "action": e.get("action"), "location": e.get("location") or post.location,
            "aliases": [a for a in [e.get("alias")] if a],
            "novelty_cue": None, "update_cue": None, "lifecycle_cue": None,
            "stale_date": False,
        }, llm.Usage()

    known = []
    if post.location:
        known.append(f'location (from the channel format): "{post.location}"')
    if post.source:
        known.append(f'attribution (from the channel format): "{post.source}"')
    if post.own_links:
        known.append("the link in this post is the channel's own site, not an external article")

    user = "POST:\n" + post.raw
    if known:
        user += "\n\nALREADY KNOWN — trust these, do not re-derive:\n- " + "\n- ".join(known)
    return llm.call(llm.prompt("call_a"), user, llm.MODEL_A, max_tokens=500)


def call_b(post, sig, candidates, oracle_row, oracle, id_map):
    if not candidates:
        return {"decision": "new_story", "story_id": None, "development_id": None,
                "confidence": 1.0, "reason": "no candidates"}, llm.Usage()

    if oracle:
        e = oracle_row["expect"]
        d = e.get("decision", "new_story")
        target_story = id_map.get(e.get("of", ""), {}).get("story_id")
        target_dev = id_map.get(e.get("of", ""), {}).get("development_id")
        if d == "new_story" or target_story is None:
            return {"decision": "new_story", "story_id": None, "development_id": None,
                    "confidence": 1.0, "reason": "oracle"}, llm.Usage()
        return {"decision": d, "story_id": target_story,
                "development_id": target_dev if d in ("update", "confirmation", "duplicate") else None,
                "confidence": 0.5 if e.get("uncertain") else 1.0,
                "reason": "oracle"}, llm.Usage()

    user = (
        "NEW POST:\n" + post.raw
        + "\n\nSOURCE (channel): " + post.channel
        + "\n\nEXTRACTED SIGNATURE:\n" + json.dumps(sig, ensure_ascii=False, indent=2)
        + "\n\nCANDIDATE OPEN STORIES:\n" + json.dumps(candidates, ensure_ascii=False, indent=2)
    )
    return llm.call(llm.prompt("call_b"), user, llm.MODEL_B, max_tokens=400)


# ---------------------------------------------------------------- apply

def apply_decision(con, run_id, post, sig, dec, stamp):
    d = dec.get("decision")
    story_id, dev_id = dec.get("story_id"), dec.get("development_id")
    summary = post.text[:160]

    if d == "new_story":
        cur = con.execute(
            "INSERT INTO stories (run_id, actor_norm, target_norm, location_norm, aliases, title,"
            " first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, sig.get("actor"), sig.get("target"), sig.get("location"),
             store.j(sig.get("aliases") or []), summary, stamp, stamp))
        story_id = cur.lastrowid
        cur = con.execute(
            "INSERT INTO developments (story_id, run_id, summary, action, kind,"
            " post_id, channel, cited_source) VALUES (?,?,?,?,?,?,?,?)",
            (story_id, run_id, summary, sig.get("action"), "initial",
             post.id, post.channel, post.source))
        dev_id = cur.lastrowid

    elif d == "new_development":
        cur = con.execute(
            "INSERT INTO developments (story_id, run_id, summary, action, kind,"
            " post_id, channel, cited_source) VALUES (?,?,?,?,?,?,?,?)",
            (story_id, run_id, summary, sig.get("action"), "development",
             post.id, post.channel, post.source))
        dev_id = cur.lastrowid

    elif d in ("update", "confirmation", "duplicate"):
        if dev_id is None and story_id is not None:
            row = con.execute(
                "SELECT id FROM developments WHERE story_id=? ORDER BY id DESC LIMIT 1",
                (story_id,)).fetchone()
            dev_id = row["id"] if row else None

    if dev_id is not None and d != "duplicate":
        con.execute(
            "INSERT INTO confirmations (development_id, run_id, post_id, decision_confidence)"
            " VALUES (?,?,?,?)", (dev_id, run_id, post.id, dec.get("confidence")))

    if story_id is not None:
        con.execute("UPDATE stories SET last_seen_at=? WHERE id=?", (stamp, story_id))
        # merge any aliases this post contributed into the story's alias set
        if sig.get("aliases"):
            row = con.execute("SELECT aliases FROM stories WHERE id=?", (story_id,)).fetchone()
            merged = sorted(set(json.loads(row["aliases"] or "[]")) | set(sig["aliases"]))
            con.execute("UPDATE stories SET aliases=? WHERE id=?", (store.j(merged), story_id))

    return story_id, dev_id


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", action="store_true",
                    help="no API calls; replay fixture expectations to exercise the plumbing")
    ap.add_argument("--note", default="")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", str(store.DB)),
                    help="SQLite path, or a mysql:// URL")
    ap.add_argument("--fixtures", help="path to a .jsonl corpus (default: fixtures/posts.jsonl)")
    ap.add_argument("--stream", help="continue an existing story space instead of starting a "
                                     "fresh one. Required for repeated runs: without it every "
                                     "invocation begins with zero open stories, so a story "
                                     "reported at 13:55 and confirmed at 14:05 can never match.")
    args = ap.parse_args()

    con = store.connect(args.db)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prompt_a, prompt_b = llm.prompt("call_a"), llm.prompt("call_b")
    run_id = args.stream or (
        ("oracle-" if args.oracle else "run-") + started.replace(":", "").replace("-", ""))

    con.execute("INSERT OR IGNORE INTO runs (run_id, started_at, model_a, model_b, prompt_a_hash,"
                " prompt_b_hash, note) VALUES (?,?,?,?,?,?,?)",
                (run_id, started, "oracle" if args.oracle else llm.MODEL_A,
                 "oracle" if args.oracle else llm.MODEL_B,
                 sha(prompt_a), sha(prompt_b), args.note))

    rows = load_fixtures(args.limit, args.fixtures)
    # a corpus with timestamps is replayed in its own order, not paste order
    if any(r.get("time") or r.get("ts") for r in rows):
        rows.sort(key=lambda r: (clock.parse(r.get("ts") or r.get("time"))
                                 or datetime.max, r.get("seq", 0)))
    seen_hashes, id_map = {}, {}
    total = llm.Usage()
    dropped = 0

    for row in rows:
        post = assemble(row["id"], row["channel"], row["text"])
        now = clock.parse(row.get("ts") or row.get("time"))
        stamp = now.isoformat() if now else started
        key = normalize_ar_key(post.text)
        key_hash = sha(key)

        con.execute(
            "INSERT OR REPLACE INTO posts (id, channel, seq, raw, text, text_key, key_hash,"
            " location, source, notes, ts, msg_id, url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (post.id, post.channel, row["seq"], post.raw, post.text, key, key_hash,
             post.location, post.source, store.j(post.notes),
             now.isoformat() if now else None, row.get("msg_id"), row.get("url")))
        con.execute("INSERT OR REPLACE INTO labels (post_id, expect, why) VALUES (?,?,?)",
                    (row["id"], store.j(row["expect"]), row.get("why", "")))

        # --- free exact-duplicate check, before any token is spent
        if key_hash in seen_hashes:
            prior = seen_hashes[key_hash]
            con.execute("INSERT OR REPLACE INTO decisions (post_id, run_id, decision, story_id,"
                        " development_id, confidence, reason, candidates, tokens_in, tokens_out,"
                        " tokens_cached, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (post.id, run_id, "duplicate", id_map.get(prior, {}).get("story_id"),
                         id_map.get(prior, {}).get("development_id"), 1.0,
                         f"normalized hash matches {prior}", "[]", 0, 0, 0, "rule"))
            print(f"  {row['seq']:>2} {post.id:<26} duplicate of {prior}  (0 tokens)")
            continue
        seen_hashes[key_hash] = post.id

        # --- Stage 1+2
        try:
            sig, usage_a = call_a(post, row, args.oracle)
        except llm.DryRun as e:
            print(f"\n{e}. Put a key in .env, or run with --oracle to test the plumbing.")
            sys.exit(1)
        total += usage_a
        # a credit is copied verbatim, so it arrives tagged as the post tagged it
        sig["cited_source"] = clean_credit(sig.get("cited_source"))

        con.execute(
            "INSERT OR REPLACE INTO classifications (post_id, run_id, class, reason, actor, target,"
            " action, location, location_parent, cited_source, aliases, novelty_cue, update_cue, lifecycle_cue,"
            " stale_date, tokens_in, tokens_out, tokens_cached, model)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (post.id, run_id, sig.get("class"), sig.get("reason"), sig.get("actor"),
             sig.get("target"), sig.get("action"), sig.get("location"), sig.get("location_parent"),
             sig.get("cited_source"),
             store.j(sig.get("aliases") or []), sig.get("novelty_cue"), sig.get("update_cue"),
             sig.get("lifecycle_cue"), int(bool(sig.get("stale_date"))),
             usage_a.tokens_in, usage_a.tokens_out, usage_a.tokens_cached,
             "oracle" if args.oracle else llm.MODEL_A))

        # Stage 0 cannot tell an outlet credit from a quoted speaker — both are
        # "<x>:" — but Call A has already separated them. Strip here, before the
        # title, the story term bag and Stage 3's lexical search are taken, so all
        # three see the same text.
        cleaned, cn = strip_attribution(post.text, sig.get("cited_source"))
        if cn:
            post = replace(post, text=cleaned, notes=post.notes + cn,
                           source=post.source or sig.get("cited_source"))
            con.execute("UPDATE posts SET text=?, source=?, notes=? WHERE id=?",
                        (post.text, post.source, store.j(post.notes), post.id))

        # containment stated by the post itself -> review queue, never auto-inserted
        # (a bad alias silently merges unrelated stories)
        if sig.get("location") and sig.get("location_parent"):
            child, parent = sig["location"], sig["location_parent"]
            known = places.match(child, parent)[0] in ("exact", "contains")
            if not known:
                con.execute(
                    "INSERT INTO place_proposals (child, parent, run_id, post_id) VALUES (?,?,?,?)"
                    " ON CONFLICT(child, parent) DO UPDATE SET seen = seen + 1",
                    (child, parent, run_id, post.id))

        if sig.get("class") != "breaking":
            dropped += 1
            print(f"  {row['seq']:>2} {post.id:<26} DROP ({sig.get('class')}) — {sig.get('reason')}")
            continue

        # --- Stage 3
        candidates = find_candidates(con, run_id, sig, post.text, now)
        dec, usage_b = call_b(post, sig, candidates, row, args.oracle, id_map)
        total += usage_b

        story_id, dev_id = apply_decision(con, run_id, post, sig, dec, stamp)
        id_map[post.id] = {"story_id": story_id, "development_id": dev_id}

        con.execute("INSERT OR REPLACE INTO decisions (post_id, run_id, decision, story_id,"
                    " development_id, confidence, reason, candidates, tokens_in, tokens_out,"
                    " tokens_cached, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (post.id, run_id, dec.get("decision"), story_id, dev_id,
                     dec.get("confidence"), dec.get("reason"),
                     store.j([c["story_id"] for c in candidates]),
                     usage_b.tokens_in, usage_b.tokens_out, usage_b.tokens_cached,
                     "oracle" if args.oracle else llm.MODEL_B))
        con.commit()

        cand = f"{len(candidates)} cand" if candidates else "no cand"
        print(f"  {row['seq']:>2} {post.id:<26} {dec.get('decision'):<16} "
              f"story={story_id} conf={dec.get('confidence')} ({cand})")

    con.commit()
    n_stories = con.execute("SELECT COUNT(*) c FROM stories WHERE run_id=?", (run_id,)).fetchone()["c"]
    print(f"\nrun_id: {run_id}")
    print(f"posts {len(rows)} | dropped {dropped} | stories {n_stories} | "
          f"tokens in {total.tokens_in} out {total.tokens_out} cached {total.tokens_cached}")
    print(f"report: python -m newsai.report {run_id}")


if __name__ == "__main__":
    main()
