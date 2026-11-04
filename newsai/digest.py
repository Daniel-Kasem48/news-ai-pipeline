"""The product view: breaking stories inside a time window, with their sources.

    python -m newsai.digest --db timed.db --window 60
    python -m newsai.digest --db timed.db --window 15 --at 2026-08-25T10:00:00

A story is in the window if it was touched inside it. Stories are ranked by how
many DISTINCT channels reported them, because that is the one corroboration
signal the pipeline produces: one channel can be wrong, four channels agreeing
within minutes is an event.

Sources are listed two ways, and they are not the same thing:
  * channels     — which feeds carried it, from `confirmations` (which despite
                   the name links every post to its development, not only the
                   posts the model called a confirmation)
  * cited        — the outlet the posts themselves credit, when they name one
"""
import argparse
import os
from datetime import datetime, timedelta

from newsai import clock
from newsai import store


def rows(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def select(con, end, window_minutes, min_channels=1):
    """Stories touched inside the window, ranked by how many channels carried them."""
    start = end - timedelta(minutes=window_minutes)
    hits = rows(con, """
        SELECT s.id AS story_id, s.title,
               p.id AS post_id, p.channel, p.ts, p.source AS cited, p.url,
               d.decision
          FROM posts p
          JOIN decisions d ON d.post_id = p.id
          JOIN stories s   ON s.id = d.story_id
         WHERE p.ts IS NOT NULL AND p.ts > ? AND p.ts <= ?
         ORDER BY p.ts
    """, (start.isoformat(), end.isoformat()))

    stories = {}
    for r in hits:
        st = stories.setdefault(r["story_id"], {
            "id": r["story_id"], "title": r["title"], "channels": {}, "cited": {},
            "posts": [], "links": [], "first": r["ts"], "last": r["ts"],
        })
        st["channels"].setdefault(r["channel"], []).append(r["post_id"])
        if r["cited"]:
            st["cited"][r["cited"]] = st["cited"].get(r["cited"], 0) + 1
        st["posts"].append(r["post_id"])
        if r["url"]:
            st["links"].append((r["channel"], r["url"]))
        st["last"] = max(st["last"], r["ts"])
        st["first"] = min(st["first"], r["ts"])

    out = sorted(stories.values(),
                 key=lambda v: (len(v["channels"]), len(v["posts"])), reverse=True)
    return start, [v for v in out if len(v["channels"]) >= min_channels]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", "timed.db"),
                    help="SQLite path, or a mysql:// URL")
    ap.add_argument("--window", type=float, default=60, help="window length in minutes")
    ap.add_argument("--at", help="window END, ISO-8601 (default: the newest post in the db)")
    ap.add_argument("--min-channels", type=int, default=1,
                    help="hide stories carried by fewer than this many channels")
    opts = ap.parse_args()

    con = store.connect(opts.db)

    end = clock.parse(opts.at) if opts.at else None
    if end is None:
        newest = rows(con, "SELECT MAX(ts) AS t FROM posts WHERE ts IS NOT NULL")[0]["t"]
        end = clock.parse(newest)
    if end is None:
        raise SystemExit("no timestamps in this database — digest needs a clock")
    start = end - timedelta(minutes=opts.window)

    # every post that landed in the window, with the story it was filed under
    hits = rows(con, """
        SELECT s.id AS story_id, s.title, s.last_seen_at,
               p.id AS post_id, p.channel, p.ts, p.source AS cited,
               d.decision, dv.summary
          FROM posts p
          JOIN decisions d     ON d.post_id = p.id
          LEFT JOIN developments dv ON dv.id = d.development_id
          JOIN stories s       ON s.id = d.story_id
         WHERE p.ts IS NOT NULL AND p.ts > ? AND p.ts <= ?
         ORDER BY p.ts
    """, (start.isoformat(), end.isoformat()))

    stories = {}
    for r in hits:
        st = stories.setdefault(r["story_id"], {
            "title": r["title"], "channels": {}, "cited": {}, "posts": [],
            "first": r["ts"], "last": r["ts"], "decisions": [],
        })
        st["channels"].setdefault(r["channel"], []).append(r["post_id"])
        if r["cited"]:
            st["cited"][r["cited"]] = st["cited"].get(r["cited"], 0) + 1
        st["posts"].append(r["post_id"])
        st["last"] = max(st["last"], r["ts"])
        st["first"] = min(st["first"], r["ts"])
        st["decisions"].append(r["decision"])

    ranked = sorted(stories.items(),
                    key=lambda kv: (len(kv[1]["channels"]), len(kv[1]["posts"])),
                    reverse=True)
    ranked = [x for x in ranked if len(x[1]["channels"]) >= opts.min_channels]

    print(f"[{start:%Y-%m-%d %H:%M} .. {end:%H:%M}]  "
          f"{len(ranked)} stories from {sum(len(v['posts']) for _, v in ranked)} posts")
    if not ranked:
        print("  (nothing in this window)")
        return
    print()
    for sid, st in ranked:
        n = len(st["channels"])
        bar = "█" * min(n, 8)
        first = clock.parse(st["first"])
        print(f"  {bar:<8} {n} channel{'s' if n != 1 else ''}   "
              f"{first:%H:%M}  story {sid}")
        print(f"           {st['title'][:100]}")
        for ch, pids in sorted(st["channels"].items(), key=lambda kv: -len(kv[1])):
            print(f"             - {ch:<14} {', '.join(pids)}")
        if st["cited"]:
            cited = ", ".join(f"{k} ({v})" if v > 1 else k for k, v in st["cited"].items())
            print(f"             cites: {cited}")
        print()


if __name__ == "__main__":
    main()
