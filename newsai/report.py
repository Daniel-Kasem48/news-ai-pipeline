#!/usr/bin/env python3
"""Score a run against the fixture expectations.

    python -m newsai.report                 # latest run
    python -m newsai.report <run_id>
    python -m newsai.report <run_a> <run_b> # compare two runs

Cost in dollars is printed only when PRICE_IN / PRICE_OUT / PRICE_CACHED are set
(dollars per 1M tokens). The docs deliberately do not hardcode provider rates.
"""
import json
import os
import sys
from collections import defaultdict

from newsai import store


def latest(con):
    # run_id is "run-<ISO timestamp>", so it sorts the same way the rowid it
    # replaces did. SQLite's rowid gave `runs` a free insertion order; InnoDB
    # has no equivalent, and this table has no surrogate key to borrow.
    r = con.execute("SELECT run_id FROM runs"
                    " ORDER BY started_at DESC, run_id DESC LIMIT 1").fetchone()
    return r["run_id"] if r else None


def true_groups(con):
    """Union-find over the fixture 'of' links: posts about the same real event."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    rows = con.execute("SELECT post_id, expect FROM labels").fetchall()
    for r in rows:
        find(r["post_id"])
    for r in rows:
        e = json.loads(r["expect"])
        # only same-development links share a true event key; a new_development
        # is a different event under the same story
        if e.get("of") and e.get("decision") in ("confirmation", "update", "duplicate"):
            union(e["of"], r["post_id"])
    return {p: find(p) for p in parent}


def report(con, run_id):
    run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not run:
        print(f"no such run: {run_id}"); return
    labels = {r["post_id"]: (json.loads(r["expect"]), r["why"])
              for r in con.execute("SELECT * FROM labels")}
    cls = {r["post_id"]: r for r in
           con.execute("SELECT * FROM classifications WHERE run_id=?", (run_id,))}
    dec = {r["post_id"]: r for r in
           con.execute("SELECT * FROM decisions WHERE run_id=?", (run_id,))}

    print(f"\n=== {run_id}")
    print(f"    models {run['model_a']} / {run['model_b']}   prompts {run['prompt_a_hash']}/{run['prompt_b_hash']}"
          + (f"   note: {run['note']}" if run["note"] else ""))

    labelled = any(exp for exp, _ in labels.values())
    if not labelled:
        print("\n-- Gate / decisions: corpus is unlabelled, nothing to score against.")
        print("   Everything below is the pipeline's own behaviour, not accuracy.")

    # ---- gate
    gate_rows, gate_ok, breaking_total, breaking_kept = [], 0, 0, 0
    for pid, (exp, why) in labels.items():
        want = exp.get("class")
        if pid not in cls:
            got = dec.get(pid, {})["decision"] if pid in dec else "-"
            got = "breaking" if got == "duplicate" else "(not run)"
        else:
            got = cls[pid]["class"]
        if want == "breaking":
            breaking_total += 1
            if got == "breaking":
                breaking_kept += 1
        if got == want:
            gate_ok += 1
        else:
            gate_rows.append((pid, want, got, why))

    n = len(labels)
    if labelled:
      print(f"\n-- Gate")
      print(f"   accuracy            {gate_ok}/{n}  ({gate_ok/n:.0%})")
      if breaking_total:
          print(f"   breaking recall     {breaking_kept}/{breaking_total}"
                f"  ({breaking_kept/breaking_total:.0%})"
                f"   <- a dropped breaking is fatal; a dropped article is free")
      if gate_rows:
          print("   misses:")
          for pid, want, got, why in gate_rows:
              print(f"     {pid:<26} want={want:<9} got={got:<9} | {why}")

    # ---- decisions
    dec_rows, dec_ok, dec_n = [], 0, 0
    id_story = {pid: r["story_id"] for pid, r in dec.items()}
    for pid, (exp, why) in labels.items():
        want = exp.get("decision")
        if not want or pid not in dec:
            continue
        dec_n += 1
        got = dec[pid]["decision"]
        want_story = id_story.get(exp.get("of")) if exp.get("of") else None
        story_ok = (want_story is None) or (dec[pid]["story_id"] == want_story)
        if got == want and story_ok:
            dec_ok += 1
        else:
            dec_rows.append((pid, want, got, dec[pid]["story_id"], want_story, why))

    if labelled:
      print(f"\n-- Stage 3 decisions")
      print(f"   accuracy            {dec_ok}/{dec_n}  ({dec_ok/dec_n:.0%})" if dec_n else "   none")
    if labelled and dec_rows:
        print("   misses:")
        for pid, want, got, gs, ws, why in dec_rows:
            tail = f" (story {gs}, wanted {ws})" if ws is not None else ""
            print(f"     {pid:<26} want={want:<16} got={got:<16}{tail}\n"
                  f"     {'':<26} | {why}")

    # ---- compression / merge / split
    # Scored at the DEVELOPMENT level, not the story level.
    # the original spec says "two true_event_keys in one story", but under the
    # Story -> Development model a story is SUPPOSED to hold several distinct
    # events (four quotes from one speech, three acts in one town). Scoring at
    # the story level marks every correct quote stream as a merge error.
    groups = true_groups(con)
    dev_of = {pid: r["development_id"] for pid, r in dec.items()
              if r["development_id"] is not None}
    by_dev, by_group = defaultdict(set), defaultdict(set)
    for pid, did in dev_of.items():
        g = groups.get(pid, pid)
        by_dev[did].add(g)
        by_group[g].add(did)

    merges = {d: g for d, g in by_dev.items() if len(g) > 1}
    splits = {g: d for g, d in by_group.items() if len(d) > 1}

    n_stories = con.execute("SELECT COUNT(*) c FROM stories WHERE run_id=?", (run_id,)).fetchone()["c"]
    n_dev = con.execute("SELECT COUNT(*) c FROM developments WHERE run_id=?", (run_id,)).fetchone()["c"]
    n_conf = con.execute("SELECT COUNT(*) c FROM decisions WHERE run_id=? AND decision='confirmation'",
                         (run_id,)).fetchone()["c"]
    kept = sum(1 for pid in dec)

    print(f"\n-- Clustering")
    print(f"   breaking posts in   {kept}")
    print(f"   stories out         {n_stories}")
    print(f"   compression         {kept / n_stories:.2f}x" if n_stories else "")
    print(f"   developments        {n_dev}")
    print(f"   confirmations       {n_conf}")
    print(f"   confirmations/story {n_conf / n_stories:.2f}"
          f"   <- if this is ~0, the channels do not overlap" if n_stories else "")
    if labelled:
        print(f"   merge errors        {len(merges)}   <- development holding 2+ true events; should be ~0")
        for d, g in merges.items():
            print(f"     development {d} holds {len(g)} true events: {sorted(g)}")
        print(f"   split errors        {len(splits)}   <- true event spread over 2+ developments; some tolerable")
        for g, d in splits.items():
            print(f"     event {g} spread over developments {sorted(d)}")
    else:
        # Without labels every post is its own "true event", so every merge the
        # pipeline made would be counted as an error and every split as a
        # success. Printing that is worse than printing nothing.
        print("   merge / split errors  unmeasurable: corpus is unlabelled")

    # ---- cost
    tin = tout = tcache = 0
    for table in ("classifications", "decisions"):
        r = con.execute(f"SELECT COALESCE(SUM(tokens_in),0) i, COALESCE(SUM(tokens_out),0) o,"
                        f" COALESCE(SUM(tokens_cached),0) c FROM {table} WHERE run_id=?",
                        (run_id,)).fetchone()
        tin += r["i"]; tout += r["o"]; tcache += r["c"]

    print(f"\n-- Cost")
    print(f"   tokens in           {tin:,}")
    print(f"   tokens out          {tout:,}")
    if tin:
        print(f"   cache hit rate      {tcache / tin:.0%}"
              f"   <- near zero means volatile content leaked into the prompt prefix")
    pi, po = os.environ.get("PRICE_IN"), os.environ.get("PRICE_OUT")
    if pi and po:
        pc = float(os.environ.get("PRICE_CACHED", pi))
        miss = tin - tcache
        usd = (miss * float(pi) + tcache * pc + tout * float(po)) / 1e6
        print(f"   run cost            ${usd:.4f}   (rates from PRICE_* env)")
    else:
        print("   run cost            set PRICE_IN / PRICE_OUT / PRICE_CACHED ($ per 1M) to price it")


def main():
    args = sys.argv[1:]
    db = None
    if "--db" in args:
        i = args.index("--db"); db = args[i + 1]; del args[i:i + 2]
    con = store.connect(db) if db else store.connect()
    if not args:
        rid = latest(con)
        if not rid:
            print("no runs yet — python -m newsai.run --oracle"); return
        report(con, rid)
    else:
        for rid in args:
            report(con, rid)


if __name__ == "__main__":
    main()
